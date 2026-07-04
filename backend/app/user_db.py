"""
user_db.py — User accounts, devices, emergency contacts, preferences, and
incidents for ResQNet's User Dashboard.

Schema overview (4 core tables, per spec)
──────────────────────────────────────────
users              — one row per registered person. Firebase UID is used
                      as user_id. No password stored — Firebase owns auth.
devices            — one row per physical device. No owner column here —
                      ownership lives in user_devices.
user_devices       — relation table: which user owns which device.
emergency_contacts — up to 3 per user, keyed by user_id, email-first.

Plus supporting tables:
user_preferences   — notification toggles + quiet hours, one row per user
incidents          — one row per emergency event
email_queue        — outbound email jobs (Phase 4 emergency alerts); a
                      Google Apps Script (owned and written by the project
                      author, not this codebase) polls GET
                      /email-queue/pending and sends each one via
                      GmailApp.sendEmail(), then reports back via
                      POST /email-queue/{id}/mark-sent or mark-failed.
                      See EMAIL_QUEUE_INTEGRATION.md for the full contract.

Registration OTP
─────────────────
OTP verification during signup is handled ENTIRELY by a separate Google
Apps Script web app (its own Sheet, its own regId, its own OTP round-trip
— see OTP_Registration_Backend.gs). This codebase is not involved in that
exchange at all. The frontend only calls create_user() (via POST
/user/register, see user_routes.py) AFTER the Apps Script's action=verify
has already succeeded.

Login
─────
Login is password-based (temporary stopgap — Firebase will eventually
replace this). It deliberately does NOT use OTP; OTP is registration-only.

ID generation
─────────────
user_id   = "<firstname>_<lastname>_<phonenumber>"   e.g. "aksh_kumar_9876543210"
            lowercased, spaces -> underscores, phone digits only (no +/spaces/dashes)
device_id = "<user_id>_<5-digit>"    e.g. "aksh_kumar_9876543210_48213"
"""

import json
import random
from datetime import datetime, date
from typing import Optional

import asyncpg

from app.db import _pool, db_enabled  # reuse the same connection pool


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------




def generate_device_id(user_id: str) -> str:
    """
    device_id = <user_id>_<random 5-digit number, zero-padded>
    Caller is responsible for retrying if this collides (extremely unlikely
    at small scale, but device_id has a UNIQUE constraint so a collision
    will raise — see register_user_device()).
    """
    suffix = "".join(random.choices(string.digits, k=5))
    return f"{user_id}_{suffix}"


def generate_incident_id(device_id: str, trigger_ts: int) -> str:
    """incident_id = <device_id>_INC<trigger_timestamp>"""
    return f"{device_id}_INC{trigger_ts}"





# ---------------------------------------------------------------------------
# Schema setup
# ---------------------------------------------------------------------------

async def init_user_tables():
    """
    Call once at app startup, after db.init_db() has created the pool.
    Safe to call even if Postgres logging is disabled.
    """
    from app import db as dbmod

    if not db_enabled():
        return

    pool = dbmod._pool
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id         TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                dob             DATE NOT NULL,
                phone           TEXT NOT NULL UNIQUE,
                email           TEXT NOT NULL UNIQUE,
                phone_verified  BOOLEAN NOT NULL DEFAULT FALSE,
                created_at      BIGINT NOT NULL
            )
        """)

        # Devices
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                device_id       TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                friendly_name   TEXT NOT NULL,
                battery         INTEGER,
                last_seen       BIGINT,
                status          TEXT NOT NULL DEFAULT 'offline',
                created_at      BIGINT NOT NULL
            )
        """)

        # Relation table: which user owns which device.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_devices (
                id              BIGSERIAL PRIMARY KEY,
                user_id         TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                device_id       TEXT NOT NULL UNIQUE REFERENCES devices(device_id) ON DELETE CASCADE,
                added_at        BIGINT NOT NULL
            )
        """)

        # Emergency contacts — email-first (phone optional). Up to 3 per
        # user, each with a unique priority (1 = notified first).
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS emergency_contacts (
                id              BIGSERIAL PRIMARY KEY,
                user_id         TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                name            TEXT NOT NULL,
                email           TEXT NOT NULL,
                phone           TEXT,
                priority        INTEGER NOT NULL,
                notify_email    BOOLEAN NOT NULL DEFAULT TRUE,
                notify_sms      BOOLEAN NOT NULL DEFAULT FALSE,
                notify_whatsapp BOOLEAN NOT NULL DEFAULT FALSE,
                UNIQUE (user_id, priority)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id             TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                notify_on_emergency BOOLEAN NOT NULL DEFAULT TRUE,
                notify_on_escalation BOOLEAN NOT NULL DEFAULT TRUE,
                notify_on_low_battery BOOLEAN NOT NULL DEFAULT TRUE,
                quiet_hours_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                quiet_hours_start   TEXT,
                quiet_hours_end     TEXT,
                language            TEXT NOT NULL DEFAULT 'en'
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id     TEXT PRIMARY KEY,
                device_id       TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
                user_id         TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                em_id           TEXT,
                started_at      BIGINT NOT NULL,
                ended_at        BIGINT,
                status          TEXT NOT NULL DEFAULT 'active',
                responder_token TEXT,
                notified_at     BIGINT
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS email_queue (
                id              BIGSERIAL PRIMARY KEY,
                to_email        TEXT NOT NULL,
                to_name         TEXT,
                template_type   TEXT NOT NULL,
                payload         JSONB NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                created_at      BIGINT NOT NULL,
                sent_at         BIGINT,
                error           TEXT
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_email_queue_status
            ON email_queue (status, created_at ASC)
        """)

    print("  User tables : ENABLED  (users, devices, user_devices, "
          "emergency_contacts, preferences, incidents, email_queue)")


# ---------------------------------------------------------------------------
# Email job queue
# ---------------------------------------------------------------------------

async def enqueue_email(to_email: str, to_name: Optional[str],
                         template_type: str, payload: dict) -> int:
    from app import db as dbmod
    if not db_enabled():
        raise RuntimeError(
            "Postgres not configured (DATABASE_URL unset) — cannot queue email."
        )

    pool = dbmod._pool
    now = int(datetime.utcnow().timestamp())

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO email_queue (to_email, to_name, template_type, payload, created_at)
            VALUES ($1, $2, $3, $4::jsonb, $5)
            RETURNING id
            """,
            to_email, to_name, template_type, json.dumps(payload), now,
        )
        return row["id"]


async def list_pending_emails(limit: int = 50) -> list[dict]:
    from app import db as dbmod
    if not db_enabled():
        return []

    pool = dbmod._pool
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, to_email, to_name, template_type, payload, created_at
            FROM email_queue
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT $1
            """,
            limit,
        )
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"]) if isinstance(d["payload"], str) else d["payload"]
            out.append(d)
        return out


async def mark_email_sent(email_id: int) -> bool:
    from app import db as dbmod
    pool = dbmod._pool
    now = int(datetime.utcnow().timestamp())
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE email_queue SET status = 'sent', sent_at = $2 WHERE id = $1",
            email_id, now,
        )
        return result.endswith("1")


async def mark_email_failed(email_id: int, error: str) -> bool:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE email_queue SET status = 'failed', error = $2 WHERE id = $1",
            email_id, error,
        )
        return result.endswith("1")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def create_user(user_id: str, name: str, dob: date, phone: str, email: str) -> dict:
    """
    Creates a user row with verified=True immediately.
    Raises asyncpg.UniqueViolationError if phone, email, or user_id already exist.
    """
    from app import db as dbmod
    pool = dbmod._pool

    now = int(datetime.utcnow().timestamp())
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, name, dob, phone, email, phone_verified, created_at)
            VALUES ($1, $2, $3, $4, $5, TRUE, $6)
            """,
            user_id, name, dob, phone, email, now,
        )
        await conn.execute(
            """
            INSERT INTO user_preferences (user_id) VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )

    return {"user_id": user_id, "name": name, "dob": dob.isoformat(), "phone": phone, "email": email}



async def get_user(user_id: str) -> Optional[dict]:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        return dict(row) if row else None


async def get_user_by_phone(phone: str) -> Optional[dict]:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE phone = $1", phone)
        return dict(row) if row else None


async def get_user_by_email(email: str) -> Optional[dict]:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email)
        return dict(row) if row else None





# ---------------------------------------------------------------------------
# Emergency contacts (max 3 per user, priority-ordered, email-first)
# ---------------------------------------------------------------------------

MAX_CONTACTS_PER_USER = 3


async def add_emergency_contact(
    user_id: str, name: str, email: str, phone: Optional[str],
    priority: int, notify_email: bool = True,
    notify_sms: bool = False, notify_whatsapp: bool = False,
) -> dict:
    from app import db as dbmod
    pool = dbmod._pool

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM emergency_contacts WHERE user_id = $1", user_id
        )
        if count >= MAX_CONTACTS_PER_USER:
            raise ValueError(
                f"User already has {MAX_CONTACTS_PER_USER} emergency contacts (the maximum)."
            )

        row = await conn.fetchrow(
            """
            INSERT INTO emergency_contacts
                (user_id, name, email, phone, priority, notify_email, notify_sms, notify_whatsapp)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            RETURNING id, user_id, name, email, phone, priority,
                      notify_email, notify_sms, notify_whatsapp
            """,
            user_id, name, email, phone, priority,
            notify_email, notify_sms, notify_whatsapp,
        )
        return dict(row)


async def list_emergency_contacts(user_id: str) -> list[dict]:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, name, email, phone, priority,
                   notify_email, notify_sms, notify_whatsapp
            FROM emergency_contacts
            WHERE user_id = $1
            ORDER BY priority ASC
            """,
            user_id,
        )
        return [dict(r) for r in rows]


async def update_emergency_contact(contact_id: int, **fields) -> None:
    if not fields:
        return
    from app import db as dbmod
    pool = dbmod._pool

    set_clauses = [f"{col} = ${i+2}" for i, col in enumerate(fields.keys())]
    sql = f"UPDATE emergency_contacts SET {', '.join(set_clauses)} WHERE id = $1"

    async with pool.acquire() as conn:
        await conn.execute(sql, contact_id, *fields.values())


async def remove_emergency_contact(contact_id: int) -> None:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM emergency_contacts WHERE id = $1", contact_id)


# ---------------------------------------------------------------------------
# Devices (+ user_devices relation table)
# ---------------------------------------------------------------------------

async def register_user_device(user_id: str, friendly_name: str) -> dict:
    """
    Generates a device_id, inserts into `devices`, links it to the user in
    `user_devices`, AND provisions that device's Postgres circular-buffer
    log table by reusing db.py's existing ensure_device_table().
    """
    from app import db as dbmod

    device_id = generate_device_id(user_id)
    now = int(datetime.utcnow().timestamp())

    pool = dbmod._pool
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO devices (device_id, user_id, friendly_name, status, created_at)
                VALUES ($1, $2, $3, 'offline', $4)
                """,
                device_id, user_id, friendly_name, now,
            )
            await conn.execute(
                """
                INSERT INTO user_devices (user_id, device_id, added_at)
                VALUES ($1, $2, $3)
                """,
                user_id, device_id, now,
            )

    await dbmod.ensure_device_table(device_id)

    return {"device_id": device_id, "user_id": user_id, "friendly_name": friendly_name}


async def list_user_devices(user_id: str) -> list[dict]:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT d.device_id, d.friendly_name, d.battery, d.last_seen,
                   d.status, d.created_at
            FROM devices d
            JOIN user_devices ud ON ud.device_id = d.device_id
            WHERE ud.user_id = $1
            ORDER BY d.created_at ASC
            """,
            user_id,
        )
        return [dict(r) for r in rows]


async def get_owner_user_id(device_id: str) -> Optional[str]:
    """Looks up which user owns a device, via the user_devices relation
    table. Used by main.py when an emergency starts on a device, to find
    who to notify (responder link + emergency contacts)."""
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT user_id FROM user_devices WHERE device_id = $1", device_id
        )


async def update_device_status(device_id: str, battery: Optional[int] = None,
                                last_seen: Optional[int] = None,
                                status: Optional[str] = None) -> None:
    """Called from /device/update (main.py) to keep the devices table's
    battery/last_seen/status in sync with live broadcasts."""
    from app import db as dbmod
    pool = dbmod._pool

    updates = []
    values = []
    i = 1
    if battery is not None:
        updates.append(f"battery = ${i}"); values.append(battery); i += 1
    if last_seen is not None:
        updates.append(f"last_seen = ${i}"); values.append(last_seen); i += 1
    if status is not None:
        updates.append(f"status = ${i}"); values.append(status); i += 1

    if not updates:
        return

    values.append(device_id)
    sql = f"UPDATE devices SET {', '.join(updates)} WHERE device_id = ${i}"

    async with pool.acquire() as conn:
        await conn.execute(sql, *values)


async def remove_device(device_id: str) -> None:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        # user_devices row cascades on delete via FK
        await conn.execute("DELETE FROM devices WHERE device_id = $1", device_id)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

async def get_preferences(user_id: str) -> Optional[dict]:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM user_preferences WHERE user_id = $1", user_id
        )
        return dict(row) if row else None


async def update_preferences(user_id: str, **fields) -> None:
    if not fields:
        return
    from app import db as dbmod
    pool = dbmod._pool

    set_clauses = [f"{col} = ${i+2}" for i, col in enumerate(fields.keys())]
    sql = f"UPDATE user_preferences SET {', '.join(set_clauses)} WHERE user_id = $1"

    async with pool.acquire() as conn:
        await conn.execute(sql, user_id, *fields.values())


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------

async def create_incident(device_id: str, user_id: str, em_id: str, started_at: int,
                           responder_token: Optional[str] = None) -> str:
    from app import db as dbmod
    pool = dbmod._pool

    incident_id = generate_incident_id(device_id, started_at)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO incidents (incident_id, device_id, user_id, em_id, started_at, status, responder_token, notified_at)
            VALUES ($1, $2, $3, $4, $5, 'active', $6, $7)
            ON CONFLICT (incident_id) DO NOTHING
            """,
            incident_id, device_id, user_id, em_id, started_at,
            responder_token, int(datetime.utcnow().timestamp()) if responder_token else None,
        )
    return incident_id


async def close_incident(incident_id: str, ended_at: int) -> None:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE incidents SET status = 'resolved', ended_at = $2 WHERE incident_id = $1",
            incident_id, ended_at,
        )


async def close_incident_by_token(responder_token: str, ended_at: int) -> Optional[str]:
    """Called when the responder dashboard resolves an incident via the
    session-token Apps Script. Returns the incident_id closed, or None."""
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE incidents SET status = 'resolved', ended_at = $2
            WHERE responder_token = $1 AND status != 'resolved'
            RETURNING incident_id
            """,
            responder_token, ended_at,
        )
        return row["incident_id"] if row else None


async def get_active_incident_for_device(device_id: str) -> Optional[dict]:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM incidents WHERE device_id = $1 AND status = 'active' "
            "ORDER BY started_at DESC LIMIT 1",
            device_id,
        )
        return dict(row) if row else None


async def list_user_incidents(user_id: str) -> list[dict]:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM incidents WHERE user_id = $1 ORDER BY started_at DESC", user_id
        )
        return [dict(r) for r in rows]