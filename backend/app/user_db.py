"""
user_db.py — User accounts, devices, emergency contacts, preferences, and
incidents for ResQNet's User Dashboard.

Schema overview
───────────────
users                — one row per registered person
emergency_contacts   — up to 3 per user, with priority + notification method
devices              — one row per registered device, linked to a user
user_preferences     — notification toggles + quiet hours, one row per user
incidents            — one row per emergency event (created in Phase 4/5,
                        but the table is created now so the schema is whole)
otp_codes            — short-lived OTP codes for phone verification

ID generation
─────────────
user_id   = "<Name>_<DOB>"           e.g. "Aksh_Kumar_19042005"
            spaces in name -> underscores, DOB formatted as DDMMYYYY
device_id = "<user_id>_<5-digit>"    e.g. "Aksh_Kumar_19042005_48213"

This file deliberately mirrors the patterns already established in db.py
(asyncpg pool, identifier safety, optional-by-default via DATABASE_URL) so
the two modules feel like one system rather than two different styles.
"""

import os
import random
import re
from datetime import datetime, date
from typing import Optional

import asyncpg

from app.db import _pool, db_enabled  # reuse the same connection pool

# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

_NAME_CLEAN_RE = re.compile(r"[^a-zA-Z]+")


def _clean_name_part(name: str) -> str:
    """
    Turns a free-text name into a safe identifier component.
    "Aksh Kumar" -> "Aksh_Kumar"
    Strips anything that isn't a letter or space, then replaces spaces
    with underscores. Multiple consecutive spaces collapse to one underscore.
    """
    # Keep letters and spaces only, then convert runs of whitespace to "_"
    letters_and_spaces = re.sub(r"[^a-zA-Z\s]", "", name).strip()
    parts = letters_and_spaces.split()
    return "_".join(parts)


def generate_user_id(name: str, dob: date) -> str:
    """
    user_id = <Name with spaces->underscores>_<DOB as DDMMYYYY>
    e.g. generate_user_id("Aksh Kumar", date(2005, 4, 19))
         -> "Aksh_Kumar_19042005"
    """
    name_part = _clean_name_part(name)
    dob_part = dob.strftime("%d%m%Y")
    return f"{name_part}_{dob_part}"


def generate_device_id(user_id: str) -> str:
    """
    device_id = <user_id>_<random 5-digit number, zero-padded>
    e.g. "Aksh_Kumar_19042005_04821"
    Caller is responsible for retrying if this collides (extremely unlikely
    at small scale, but device_id has a UNIQUE constraint so a collision
    will raise — see register_device()).
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
    Safe to call even if Postgres logging is disabled — does nothing in
    that case, matching the pattern in db.py.
    """
    from app import db as dbmod   # local import avoids circular import at module load time

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

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS emergency_contacts (
                id              BIGSERIAL PRIMARY KEY,
                user_id         TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                name            TEXT NOT NULL,
                phone           TEXT NOT NULL,
                email           TEXT,
                priority        INTEGER NOT NULL,         -- 1 = first notified, 2, 3
                notify_sms      BOOLEAN NOT NULL DEFAULT TRUE,
                notify_whatsapp BOOLEAN NOT NULL DEFAULT TRUE,
                notify_email    BOOLEAN NOT NULL DEFAULT TRUE,
                UNIQUE (user_id, priority)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                device_id       TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                friendly_name   TEXT NOT NULL,
                battery         INTEGER,
                last_seen       BIGINT,
                status          TEXT NOT NULL DEFAULT 'offline',  -- online / offline / emergency
                created_at      BIGINT NOT NULL
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id             TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                notify_on_emergency BOOLEAN NOT NULL DEFAULT TRUE,
                notify_on_escalation BOOLEAN NOT NULL DEFAULT TRUE,
                notify_on_low_battery BOOLEAN NOT NULL DEFAULT TRUE,
                quiet_hours_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                quiet_hours_start   TEXT,    -- "22:00" 24h format, local to user
                quiet_hours_end     TEXT,    -- "07:00"
                language            TEXT NOT NULL DEFAULT 'en'
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id     TEXT PRIMARY KEY,
                device_id       TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
                user_id         TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                em_id           TEXT,        -- matches db.py's emergency table em_id
                started_at      BIGINT NOT NULL,
                ended_at        BIGINT,
                status          TEXT NOT NULL DEFAULT 'active',  -- active / resolved
                responder_token TEXT,        -- Phase 5: token embedded in responder link
                notified_at     BIGINT        -- when SMS/WhatsApp/Email were dispatched
            )
        """)

    print("  User tables : ENABLED  (users, contacts, devices, preferences, incidents)")


# ---------------------------------------------------------------------------
# MSG91 OTP — server-side token verification
#
# Flow:
#   1. Frontend loads MSG91 widget JS with widgetId + tokenAuth
#   2. User enters phone → window.sendOtp(phone) → MSG91 sends SMS directly
#   3. User enters code → window.verifyOtp(code) → MSG91 returns access_token
#   4. Frontend POSTs access_token to POST /user/verify-otp (user_routes.py)
#   5. Backend calls verify_msg91_token() → MSG91 confirms validity
#   6. If valid → mark user phone_verified, return session info
#
# No OTP codes, no expiry logic, no retry counting stored in our DB —
# MSG91 handles all of that client-side. Our backend only ever sees
# the final access_token that MSG91 issues after the user successfully
# enters the correct code.
# ---------------------------------------------------------------------------

MSG91_VERIFY_URL = "https://api.msg91.com/api/v5/widget/verifyAccessToken"


async def verify_msg91_token(access_token: str) -> dict:
    """
    Calls MSG91's server-side token verification API.
    Returns the decoded token payload on success (contains phone number).
    Raises ValueError if the token is invalid or the API call fails.

    MSG91 verify endpoint:
        POST https://api.msg91.com/api/v5/widget/verifyAccessToken
        Headers: authkey: <MSG91_TOKEN_AUTH>
        Body:    { "access-token": "<token_from_frontend>" }

    Success response:  { "type": "success", "message": "Token is valid",
                         "widget_id": "...", "reqId": "...", "identifier": "<phone>" }
    Failure response:  { "type": "error", "message": "Token is invalid" }
    """
    import urllib.request
    import urllib.error
    import json

    auth_key = os.getenv("MSG91_TOKEN_AUTH", "").strip()
    if not auth_key:
        raise RuntimeError(
            "MSG91_TOKEN_AUTH is not set in .env — cannot verify OTP token."
        )

    payload = json.dumps({"access-token": access_token}).encode("utf-8")
    req = urllib.request.Request(
        MSG91_VERIFY_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "authkey": auth_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ValueError(f"MSG91 API returned HTTP {e.code}: {e.read().decode()}")
    except urllib.error.URLError as e:
        raise ValueError(f"MSG91 API unreachable: {e.reason}")

    if body.get("type") != "success":
        raise ValueError(body.get("message", "Token verification failed."))

    return body   # caller can extract body["identifier"] to get the phone number


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def create_user(name: str, dob: date, phone: str, email: str) -> dict:
    """
    Creates a user row. Does NOT mark phone_verified — call mark_phone_verified()
    after a successful verify_otp() in the registration flow.
    Raises asyncpg.UniqueViolationError if phone, email, or the derived
    user_id already exist.
    """
    from app import db as dbmod
    pool = dbmod._pool

    user_id = generate_user_id(name, dob)
    now = int(datetime.utcnow().timestamp())

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, name, dob, phone, email, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            user_id, name, dob, phone, email, now,
        )
        # Seed default preferences row at the same time
        await conn.execute(
            """
            INSERT INTO user_preferences (user_id) VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )

    return {"user_id": user_id, "name": name, "dob": dob.isoformat(), "phone": phone, "email": email}


async def mark_phone_verified(user_id: str) -> None:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET phone_verified = TRUE WHERE user_id = $1", user_id
        )


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


# ---------------------------------------------------------------------------
# Emergency contacts (max 3 per user, priority-ordered)
# ---------------------------------------------------------------------------

MAX_CONTACTS_PER_USER = 3


async def add_emergency_contact(
    user_id: str, name: str, phone: str, email: Optional[str],
    priority: int, notify_sms: bool, notify_whatsapp: bool, notify_email: bool
) -> dict:
    """
    Raises ValueError if the user already has MAX_CONTACTS_PER_USER contacts,
    or if `priority` is already taken (1, 2, 3 — each must be unique per user).
    """
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
                (user_id, name, phone, email, priority, notify_sms, notify_whatsapp, notify_email)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            RETURNING id, user_id, name, phone, email, priority,
                      notify_sms, notify_whatsapp, notify_email
            """,
            user_id, name, phone, email, priority,
            notify_sms, notify_whatsapp, notify_email,
        )
        return dict(row)


async def list_emergency_contacts(user_id: str) -> list[dict]:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, name, phone, email, priority,
                   notify_sms, notify_whatsapp, notify_email
            FROM emergency_contacts
            WHERE user_id = $1
            ORDER BY priority ASC
            """,
            user_id,
        )
        return [dict(r) for r in rows]


async def update_emergency_contact(contact_id: int, **fields) -> None:
    """
    Generic partial update. `fields` keys must be column names already
    validated by the caller (the FastAPI route uses a Pydantic model so
    this is never raw user input reaching here unchecked).
    """
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
# Devices
# ---------------------------------------------------------------------------

async def register_user_device(user_id: str, friendly_name: str) -> dict:
    """
    Generates a device_id, creates the devices row, AND provisions that
    device's Postgres circular-buffer log table by reusing db.py's existing
    ensure_device_table() — so a device registered here is immediately ready
    to receive /device/update calls with full logging.
    """
    from app import db as dbmod

    device_id = generate_device_id(user_id)
    now = int(datetime.utcnow().timestamp())

    pool = dbmod._pool
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO devices (device_id, user_id, friendly_name, status, created_at)
            VALUES ($1, $2, $3, 'offline', $4)
            """,
            device_id, user_id, friendly_name, now,
        )

    # Reuse the existing per-device log table provisioning from db.py
    await dbmod.ensure_device_table(device_id)

    return {"device_id": device_id, "user_id": user_id, "friendly_name": friendly_name}


async def list_user_devices(user_id: str) -> list[dict]:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM devices WHERE user_id = $1 ORDER BY created_at ASC", user_id
        )
        return [dict(r) for r in rows]


async def update_device_status(device_id: str, battery: Optional[int] = None,
                                last_seen: Optional[int] = None,
                                status: Optional[str] = None) -> None:
    """
    Called from the existing /device/update flow (main.py) to keep the
    devices table's battery/last_seen/status in sync with live broadcasts,
    so the User Dashboard's device list reflects real-time state.
    """
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
# Incidents (table + helpers now; full dispatch logic comes in Phase 4)
# ---------------------------------------------------------------------------

async def create_incident(device_id: str, user_id: str, em_id: str, started_at: int) -> str:
    from app import db as dbmod
    pool = dbmod._pool

    incident_id = generate_incident_id(device_id, started_at)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO incidents (incident_id, device_id, user_id, em_id, started_at, status)
            VALUES ($1, $2, $3, $4, $5, 'active')
            ON CONFLICT (incident_id) DO NOTHING
            """,
            incident_id, device_id, user_id, em_id, started_at,
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


async def list_user_incidents(user_id: str) -> list[dict]:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM incidents WHERE user_id = $1 ORDER BY started_at DESC", user_id
        )
        return [dict(r) for r in rows]