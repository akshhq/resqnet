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
email_otp_codes      — short-lived verification codes, delivered by email
email_queue          — outbound email jobs; a Google Apps Script (owned and
                        written by the project author, not this codebase)
                        polls GET /email-queue/pending and sends each one
                        via GmailApp.sendEmail(), then reports back via
                        POST /email-queue/{id}/mark-sent or mark-failed.
                        See EMAIL_QUEUE_INTEGRATION.md for the full contract.

ID generation
─────────────
user_id   = "<Name>_<DOB>"           e.g. "Aksh_Kumar_19042005"
            spaces in name -> underscores, DOB formatted as DDMMYYYY
device_id = "<user_id>_<5-digit>"    e.g. "Aksh_Kumar_19042005_48213"

This file deliberately mirrors the patterns already established in db.py
(asyncpg pool, identifier safety, optional-by-default via DATABASE_URL) so
the two modules feel like one system rather than two different styles.
"""

import json
import os
import random
import re
import string
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


def generate_email_otp() -> str:
    """6-digit numeric verification code, delivered by email via the
    Apps Script + Gmail queue rather than SMS."""
    return "".join(random.choices(string.digits, k=6))


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
                verified        BOOLEAN NOT NULL DEFAULT FALSE,  -- verified via emailed code
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

        # ── Email verification codes (registration / login) ────────────────
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS email_otp_codes (
                email           TEXT NOT NULL,
                code            TEXT NOT NULL,
                purpose         TEXT NOT NULL DEFAULT 'registration',
                created_at      BIGINT NOT NULL,
                expires_at      BIGINT NOT NULL,
                verified        BOOLEAN NOT NULL DEFAULT FALSE,
                attempts        INTEGER NOT NULL DEFAULT 0
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_email_otp_email
            ON email_otp_codes (email, created_at DESC)
        """)

        # ── Outbound email job queue — polled by an external Google Apps
        #    Script (owned by the project author) which sends the actual
        #    email via GmailApp and reports back via mark-sent/mark-failed.
        #    See EMAIL_QUEUE_INTEGRATION.md for the exact HTTP contract.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS email_queue (
                id              BIGSERIAL PRIMARY KEY,
                to_email        TEXT NOT NULL,
                to_name         TEXT,
                template_type   TEXT NOT NULL,     -- 'email_otp' | 'emergency_alert' (Phase 4+)
                payload         JSONB NOT NULL,     -- template-specific fields, see integration doc
                status          TEXT NOT NULL DEFAULT 'pending',  -- pending / sent / failed
                created_at      BIGINT NOT NULL,
                sent_at         BIGINT,
                error           TEXT
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_email_queue_status
            ON email_queue (status, created_at ASC)
        """)

    print("  User tables : ENABLED  (users, contacts, devices, preferences, "
          "incidents, email_otp_codes, email_queue)")


# ---------------------------------------------------------------------------
# Email job queue — generic enqueue helper + Apps Script polling endpoints
#
# Any part of this codebase that needs to send an email (registration OTP
# now, emergency alerts in Phase 4) calls enqueue_email(). The row sits in
# `email_queue` with status='pending' until an external Google Apps Script
# (written and owned by the project author) polls GET /email-queue/pending,
# sends it via GmailApp.sendEmail(), and reports back via
# POST /email-queue/{id}/mark-sent or mark-failed.
#
# This backend never talks to Google directly — no service account, no
# Sheets API credentials, no SMTP config. The Apps Script is the only thing
# that knows how to send mail; this table is just its work queue.
# ---------------------------------------------------------------------------

async def enqueue_email(to_email: str, to_name: Optional[str],
                         template_type: str, payload: dict) -> int:
    """
    Inserts a pending row into email_queue. Returns the new row's id.
    payload is stored as JSONB — pass a plain dict, it's serialised here.
    """
    from app import db as dbmod
    if not db_enabled():
        # Postgres logging disabled entirely — email queue is inert too.
        # Fail loudly rather than silently pretending the email was queued.
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
    """Called by GET /email-queue/pending (email_queue_routes.py)."""
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
            # asyncpg returns JSONB as a str — decode it for the API response
            # so the Apps Script receives a real JSON object, not a JSON string.
            d["payload"] = json.loads(d["payload"]) if isinstance(d["payload"], str) else d["payload"]
            out.append(d)
        return out


async def mark_email_sent(email_id: int) -> bool:
    """Called by POST /email-queue/{id}/mark-sent. Returns False if the id
    doesn't exist (caller should 404)."""
    from app import db as dbmod
    pool = dbmod._pool
    now = int(datetime.utcnow().timestamp())
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE email_queue SET status = 'sent', sent_at = $2 WHERE id = $1",
            email_id, now,
        )
        return result.endswith("1")   # "UPDATE 1" vs "UPDATE 0"


async def mark_email_failed(email_id: int, error: str) -> bool:
    """Called by POST /email-queue/{id}/mark-failed."""
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE email_queue SET status = 'failed', error = $2 WHERE id = $1",
            email_id, error,
        )
        return result.endswith("1")


# ---------------------------------------------------------------------------
# Email-based OTP — verification codes delivered via the email queue above
#
# Replaces the earlier MSG91 phone-OTP flow. Same expiry/attempt-limiting
# design as before, just addressed by email instead of phone, and the
# delivery mechanism is enqueue_email() instead of an SMS provider call.
# ---------------------------------------------------------------------------

OTP_EXPIRY_SECONDS = 300   # 5 minutes
OTP_MAX_ATTEMPTS   = 5


async def send_email_otp(email: str, purpose: str = "registration",
                          to_name: Optional[str] = None) -> None:
    """
    Generates a 6-digit code, stores it in email_otp_codes, and enqueues
    an 'email_otp' job for the Apps Script sender to deliver.
    """
    from app import db as dbmod
    if not db_enabled():
        raise RuntimeError("Postgres not configured — cannot issue an email OTP.")

    code = generate_email_otp()
    now = int(datetime.utcnow().timestamp())
    expires = now + OTP_EXPIRY_SECONDS

    pool = dbmod._pool
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO email_otp_codes (email, code, purpose, created_at, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            email, code, purpose, now, expires,
        )

    await enqueue_email(
        to_email=email,
        to_name=to_name,
        template_type="email_otp",
        payload={
            "code": code,
            "purpose": purpose,
            "expires_in_seconds": OTP_EXPIRY_SECONDS,
        },
    )


async def verify_email_otp(email: str, code: str, purpose: str = "registration") -> bool:
    """
    Checks the most recent unverified, unexpired code for this email+purpose.
    Increments attempts on every check; locks out after OTP_MAX_ATTEMPTS.
    Identical logic to the original phone-OTP implementation, just keyed
    by email instead of phone.
    """
    from app import db as dbmod
    if not db_enabled():
        raise RuntimeError("Postgres not configured.")

    pool = dbmod._pool
    now = int(datetime.utcnow().timestamp())

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT code, expires_at, verified, attempts
            FROM email_otp_codes
            WHERE email = $1 AND purpose = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            email, purpose,
        )

        if row is None:
            return False
        if row["verified"]:
            return False   # already used
        if row["attempts"] >= OTP_MAX_ATTEMPTS:
            return False   # locked out
        if now > row["expires_at"]:
            return False   # expired

        await conn.execute(
            """
            UPDATE email_otp_codes SET attempts = attempts + 1
            WHERE email = $1 AND purpose = $2 AND created_at = (
                SELECT created_at FROM email_otp_codes
                WHERE email = $1 AND purpose = $2
                ORDER BY created_at DESC LIMIT 1
            )
            """,
            email, purpose,
        )

        if row["code"] != code:
            return False

        await conn.execute(
            """
            UPDATE email_otp_codes SET verified = TRUE
            WHERE email = $1 AND purpose = $2 AND created_at = (
                SELECT created_at FROM email_otp_codes
                WHERE email = $1 AND purpose = $2
                ORDER BY created_at DESC LIMIT 1
            )
            """,
            email, purpose,
        )
        return True


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def create_user(name: str, dob: date, phone: str, email: str) -> dict:
    """
    Creates a user row. Does NOT mark verified — call mark_verified()
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


async def mark_verified(user_id: str) -> None:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET verified = TRUE WHERE user_id = $1", user_id
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


async def get_user_by_email(email: str) -> Optional[dict]:
    from app import db as dbmod
    pool = dbmod._pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email)
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