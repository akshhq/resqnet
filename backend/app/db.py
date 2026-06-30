"""
db.py — Neon Postgres logging layer for ResQNet.

Schema design
─────────────
1. Normal logs — ONE table per device, named `device_<sanitised_device_id>`.
   Fixed-size circular buffer of CIRCULAR_BUFFER_SIZE (500) rows. Once full,
   every new insert is paired with a delete of the oldest row in the SAME
   transaction — enqueue and dequeue happen together, so the table never
   exceeds the cap.

2. Emergency logs — ONE table per emergency event, named
   `<device_id>_EM<unix_timestamp_of_trigger>`. Created the moment an
   emergency starts. Pre-seeded with the last EMERGENCY_LOOKBACK_SECONDS
   (300s / 5 min) of rows pulled from that device's normal circular buffer
   table, then appended to on every tick for the duration of the emergency —
   uncapped, nothing is ever deleted from an emergency table.

3. `_emergency_registry` — a small metadata table tracking which emergency
   table is currently ACTIVE for each device (since table names are dynamic
   and Postgres has no built-in way to look that up). On reset, the matching
   row is marked closed with an end timestamp; the emergency table itself is
   left in place permanently as an audit trail.

All table/column identifiers are validated through `_safe_ident()` before
being interpolated into SQL — asyncpg does not support parameterised
identifiers (table names), only parameterised VALUES, so this validation is
the only thing standing between this code and SQL injection via a malicious
device_id. Never relax this without understanding why it's here.
"""

import os
import re
import time
from typing import Optional

import asyncpg

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CIRCULAR_BUFFER_SIZE       = 500   # max rows per device's normal log table
EMERGENCY_LOOKBACK_SECONDS = 300   # 5 minutes of pre-trigger context

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

_pool: Optional[asyncpg.Pool] = None


def db_enabled() -> bool:
    """Postgres logging is entirely optional — off if DATABASE_URL isn't set."""
    return bool(DATABASE_URL)


# ---------------------------------------------------------------------------
# Identifier safety
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"[^a-zA-Z0-9_]")


def _safe_ident(raw: str) -> str:
    """
    Sanitise a string for use as a Postgres table/column identifier.
    asyncpg cannot parameterise identifiers (only values), so any string
    interpolated into a CREATE TABLE / INSERT INTO ... statement MUST be
    passed through this first. Strips everything except alphanumerics and
    underscores, lowercases, and guarantees the result starts with a letter
    (Postgres identifiers cannot start with a digit).
    """
    cleaned = _IDENT_RE.sub("_", raw).lower()
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"d_{cleaned}"
    return cleaned[:63]   # Postgres identifier length limit


def _device_table(device_id: str) -> str:
    return f"device_{_safe_ident(device_id)}"


def _emergency_table(device_id: str, em_id: str) -> str:
    return f"{_safe_ident(device_id)}_em{_safe_ident(em_id)}"


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------

async def init_db():
    """Call once at app startup (inside the lifespan handler)."""
    global _pool
    if not db_enabled():
        print("  Postgres : DISABLED (no DATABASE_URL set)")
        return

    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _emergency_registry (
                device_id      TEXT NOT NULL,
                em_id          TEXT NOT NULL,
                table_name     TEXT NOT NULL,
                started_at     BIGINT NOT NULL,
                ended_at       BIGINT,
                status         TEXT NOT NULL DEFAULT 'active',
                PRIMARY KEY (device_id, em_id)
            )
        """)

    print("  Postgres : ENABLED")


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def _require_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError(
            "Postgres pool not initialised. Call init_db() at startup, "
            "or check DATABASE_URL is set if you expected logging to be active."
        )
    return _pool


# ---------------------------------------------------------------------------
# Normal log table (circular buffer)
# ---------------------------------------------------------------------------

_LOG_COLUMNS_SQL = """
    id          BIGSERIAL PRIMARY KEY,
    device_id   TEXT NOT NULL,
    ts          BIGINT NOT NULL,
    latitude    DOUBLE PRECISION NOT NULL,
    longitude   DOUBLE PRECISION NOT NULL,
    speed       DOUBLE PRECISION NOT NULL,
    context     TEXT NOT NULL,
    battery     INTEGER NOT NULL,
    emergency   BOOLEAN NOT NULL,
    risk        TEXT NOT NULL,
    alert       BOOLEAN NOT NULL,
    escalation  TEXT,
    reset       BOOLEAN NOT NULL
"""


async def ensure_device_table(device_id: str) -> None:
    """
    Called from /device/register (per your spec — table is provisioned at
    registration time, not lazily on first update).
    """
    if not db_enabled():
        return
    pool = _require_pool()
    table = _device_table(device_id)
    async with pool.acquire() as conn:
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                {_LOG_COLUMNS_SQL}
            )
        """)
        # Index on ts so the 5-min lookback query for emergencies is fast
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table}_ts ON {table} (ts)
        """)


async def insert_normal_log(device_id: str, payload: dict) -> None:
    """
    Insert one row into the device's circular buffer table.
    If the table is at CIRCULAR_BUFFER_SIZE, the oldest row is deleted in
    the SAME transaction as the insert — enqueue and dequeue together, so
    the table is never observed to exceed the cap by an external reader.
    """
    if not db_enabled():
        return
    pool = _require_pool()
    table = _device_table(device_id)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                f"""
                INSERT INTO {table}
                    (device_id, ts, latitude, longitude, speed, context,
                     battery, emergency, risk, alert, escalation, reset)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                """,
                device_id, payload["timestamp"], payload["latitude"],
                payload["longitude"], payload["speed"], payload["context"],
                payload["battery"], payload["emergency"], payload["risk"],
                payload["alert"], payload["escalation"], payload["reset"],
            )

            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            if count > CIRCULAR_BUFFER_SIZE:
                await conn.execute(f"""
                    DELETE FROM {table}
                    WHERE id = (SELECT id FROM {table} ORDER BY id ASC LIMIT 1)
                """)


# ---------------------------------------------------------------------------
# Emergency log table
# ---------------------------------------------------------------------------

async def start_emergency_log(device_id: str, trigger_ts: int) -> str:
    """
    Called the moment a device's emergency flag flips False → True.

    Creates a new table named <device_id>_EM<trigger_ts>, registers it as
    ACTIVE in _emergency_registry, and pre-seeds it with the last
    EMERGENCY_LOOKBACK_SECONDS of rows from the device's normal circular
    buffer table (if any exist in that window).

    Returns the em_id (just the timestamp string) so the caller can pass it
    to append_emergency_log() / close_emergency_log() for the rest of this
    emergency's lifetime without re-deriving it.
    """
    if not db_enabled():
        return str(trigger_ts)

    pool   = _require_pool()
    em_id  = str(trigger_ts)
    table  = _emergency_table(device_id, em_id)
    source = _device_table(device_id)

    async with pool.acquire() as conn:
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                {_LOG_COLUMNS_SQL}
            )
        """)

        await conn.execute(
            """
            INSERT INTO _emergency_registry
                (device_id, em_id, table_name, started_at, status)
            VALUES ($1, $2, $3, $4, 'active')
            ON CONFLICT (device_id, em_id) DO NOTHING
            """,
            device_id, em_id, table, trigger_ts,
        )

        # Pre-seed with the 5 minutes of context leading up to the trigger.
        # Source table may not exist yet if this device was never registered
        # through the normal flow — guard with a check.
        source_exists = await conn.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", source
        )
        if source_exists:
            lookback_start = trigger_ts - EMERGENCY_LOOKBACK_SECONDS
            # Strictly LESS THAN trigger_ts — the trigger tick itself is
            # already present in the normal table (insert_normal_log runs
            # before this), and the caller (device_update) appends it to the
            # emergency table separately right after this function returns.
            # Using <= here would duplicate that row.
            await conn.execute(f"""
                INSERT INTO {table}
                    (device_id, ts, latitude, longitude, speed, context,
                     battery, emergency, risk, alert, escalation, reset)
                SELECT
                    device_id, ts, latitude, longitude, speed, context,
                    battery, emergency, risk, alert, escalation, reset
                FROM {source}
                WHERE ts >= $1 AND ts < $2
                ORDER BY ts ASC
            """, lookback_start, trigger_ts)

    return em_id


async def append_emergency_log(device_id: str, em_id: str, payload: dict) -> None:
    """
    Append one row to an active emergency's table. Never deletes — emergency
    logs are intentionally uncapped per spec, unlike the normal circular
    buffer.
    """
    if not db_enabled():
        return
    pool  = _require_pool()
    table = _emergency_table(device_id, em_id)

    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO {table}
                (device_id, ts, latitude, longitude, speed, context,
                 battery, emergency, risk, alert, escalation, reset)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """,
            device_id, payload["timestamp"], payload["latitude"],
            payload["longitude"], payload["speed"], payload["context"],
            payload["battery"], payload["emergency"], payload["risk"],
            payload["alert"], payload["escalation"], payload["reset"],
        )


async def close_emergency_log(device_id: str, em_id: str, end_ts: int) -> None:
    """
    Called on reset. Marks the registry row 'closed' with an end timestamp.
    The emergency table itself is left in the database permanently as an
    audit trail — nothing is deleted or archived elsewhere.
    """
    if not db_enabled():
        return
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE _emergency_registry
            SET status = 'closed', ended_at = $3
            WHERE device_id = $1 AND em_id = $2
            """,
            device_id, em_id, end_ts,
        )


async def get_active_emergency_id(device_id: str) -> Optional[str]:
    """
    Returns the em_id of the currently active emergency for this device,
    or None if there isn't one. Used so device_update() knows whether to
    route this tick's log into an emergency table in addition to the
    normal circular buffer.
    """
    if not db_enabled():
        return None
    pool = _require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT em_id FROM _emergency_registry
            WHERE device_id = $1 AND status = 'active'
            ORDER BY started_at DESC LIMIT 1
            """,
            device_id,
        )
        return row["em_id"] if row else None
