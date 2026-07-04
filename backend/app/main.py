import json
import os
from collections import deque
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
load_dotenv()   # loads backend/.env if present — must run before os.getenv() calls

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from app.schemas import DeviceUpdate, DeviceRegister
from app.storage import (
    device_state,
    device_history,
    alert_state,
    escalation_state,
    registered_devices,
    HISTORY_MAXLEN
)
from app.context import (
    classify_context,
    detect_speed_anomaly,
    calculate_risk,
    should_alert,
    check_escalation
)
from app.websocket import ConnectionManager
from app.auth import verify_api_key, verify_ws_token, rate_limit_exceeded_handler
from app import db
from app import user_db
from app.user_routes import router as user_router
from app.email_queue_routes import router as email_queue_router
from app.session_proxy_routes import router as session_proxy_router


# ---------------------------------------------------------------------------
# Rate limiter (5.3)
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address, default_limits=[])


# ---------------------------------------------------------------------------
# Emergency notification config — the session-token Apps Script that emails
# responders (and, per contact, emergency contacts) a magic link to the
# responder dashboard. Server-to-server call, not client-facing.
# ---------------------------------------------------------------------------

SESSION_TOKEN_WEBAPP_URL = os.getenv("SESSION_TOKEN_WEBAPP_URL", "").strip()


async def _trigger_responder_alert(device_id: str, user_id: str, name: str,
                                    lat: float, lng: float) -> dict | None:
    """
    Calls the emergency session-token Apps Script's action=trigger, which:
      - generates a 6-char responder-dashboard token,
      - emails RESPONDER_EMAILS (fixed list) the magic link,
      - ALSO emails every address in contactEmails (this user's emergency
        contacts) the same link, per spec.
    Returns the Apps Script's JSON response (token, link, expiresAt) or
    None if the webapp URL isn't configured / the call fails — an
    emergency is never blocked on this succeeding.
    """
    if not SESSION_TOKEN_WEBAPP_URL:
        print("[WARNING] SESSION_TOKEN_WEBAPP_URL not set — skipping responder alert email.")
        return None

    contact_emails: list[str] = []
    try:
        contacts = await user_db.list_emergency_contacts(user_id)
        contact_emails = [c["email"] for c in contacts if c.get("notify_email") and c.get("email")]
    except Exception as e:
        print(f"[WARNING] Could not load emergency contacts for {user_id}: {e}")

    responder_emails: list[str] = []
    try:
        responders = await user_db.list_responders(user_id)
        responder_emails = [r["email"] for r in responders if r.get("email")]
    except Exception as e:
        print(f"[WARNING] Could not load responders for {user_id}: {e}")

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            res = await client.post(
                SESSION_TOKEN_WEBAPP_URL,
                content=json.dumps({
                    "action": "trigger",
                    "userID": user_id,
                    "deviceID": device_id,
                    "name": name,
                    "lat": lat,
                    "lng": lng,
                    "contactEmails": contact_emails,
                    "responderEmails": responder_emails,
                }),
                headers={"Content-Type": "text/plain;charset=utf-8"},
            )
            data = res.json()
            if not data.get("success"):
                print(f"[WARNING] Session-token Apps Script returned failure: {data.get('error')}")
                return None

            if data.get("emailErrors"):
                for err in data["emailErrors"]:
                    print(f"[WARNING] Apps Script Email dispatch failed: {err}")

            return data
    except Exception as e:
        print(f"[WARNING] Failed to reach session-token Apps Script: {e}")
        return None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    device_state.clear()
    device_history.clear()
    alert_state.clear()
    escalation_state.clear()
    registered_devices.clear()

    api_key = os.getenv("API_KEY", "").strip()
    print("=" * 52)
    print("  ResQNet Backend v0.7")
    print("=" * 52)
    if api_key:
        print(f"  Auth     : ENABLED  (API_KEY is set)")
        print(f"  Key hint : ...{api_key[-6:]}")
        print()
        print("  Simulator must use:")
        print("    python simulator.py --key <your_key>")
        print("  OR set API_KEY in your terminal before running.")
    else:
        print("  Auth     : DISABLED (no API_KEY in .env)")
        print("  All requests accepted — dev mode.")

    if not SESSION_TOKEN_WEBAPP_URL:
        print("  Responder alerts : DISABLED (SESSION_TOKEN_WEBAPP_URL not set)")

    await db.init_db()
    await user_db.init_user_tables()
    print("=" * 52)
    yield
    await db.close_db()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="ResQNet Backend", version="0.7", lifespan=lifespan)

# Attach rate-limit exceeded handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# CORS
_raw_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5500,http://127.0.0.1:5500,http://localhost:5501,http://127.0.0.1:5501,http://localhost:5502,http://127.0.0.1:5502"
)
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

print(f"  CORS origins allowed: {ALLOWED_ORIGINS}")

manager = ConnectionManager()

# ---------------------------------------------------------------------------
# Health check — unauthenticated, used by the frontend dashboards to detect
# whether the live Render backend is reachable (see frontend config.js).
# Falls back to a local backend automatically if this doesn't respond.
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

# User Dashboard routes (register/login/contacts/devices/preferences/incidents)
# and the email-queue polling contract used by the Apps Script sender.
app.include_router(user_router)
app.include_router(email_queue_router)
app.include_router(session_proxy_router)


# ---------------------------------------------------------------------------
# Device endpoints
# ---------------------------------------------------------------------------

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    # Must accept FIRST — you cannot close an unaccepted WebSocket.
    # verify_ws_token sends the close frame after accept if token is invalid.
    await websocket.accept()

    if not await verify_ws_token(websocket, token):
        return   # token invalid — already closed inside verify_ws_token

    manager.active_connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# 5.4 — Device registration endpoint
# ---------------------------------------------------------------------------

@app.post(
    "/device/register",
    dependencies=[Depends(verify_api_key)],
    summary="Register a device ID so it can send updates"
)
async def register_device(data: DeviceRegister):
    """
    Register a device before it can send updates.
    Only registered device IDs are accepted at /device/update.

    Request body:  { "device_id": "SIM_DEVICE_01" }
    """
    registered_devices.add(data.device_id)

    # Provision this device's Postgres circular-buffer log table now,
    # per spec: table creation happens at registration, not lazily.
    await db.ensure_device_table(data.device_id)

    print(f"[OK] Device registered: {data.device_id}  (total: {len(registered_devices)})")
    return {"registered": True, "device_id": data.device_id}


@app.get(
    "/device/registered",
    dependencies=[Depends(verify_api_key)],
    summary="List all registered device IDs"
)
def list_registered_devices():
    return {"devices": sorted(registered_devices)}


# ---------------------------------------------------------------------------
# 5.1 + 5.3 — Device update (auth + rate limited)
# ---------------------------------------------------------------------------

@app.post(
    "/device/update",
    dependencies=[Depends(verify_api_key)],
    summary="Receive a position + state update from a registered device"
)
@limiter.limit("60/minute")   # 5.3: max 1 update/second per IP
async def device_update(request: Request, data: DeviceUpdate):
    # 5.4: reject unregistered device IDs
    if data.device_id not in registered_devices:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Device '{data.device_id}' is not registered. "
                "POST to /device/register first."
            )
        )

    prev = device_state.get(data.device_id, {})
    prev_speed = prev.get("speed", data.speed)

    if data.reset:
        was_reset = True
        emergency_locked = False
        alert_state.pop(data.device_id, None)
        escalation_state.pop(data.device_id, None)
    else:
        was_reset = False
        emergency_locked = prev.get("emergency", False) or data.emergency

    anomaly    = detect_speed_anomaly(prev_speed, data.speed)
    context    = classify_context(data.speed)
    risk       = calculate_risk(emergency_locked, anomaly)
    escalation = check_escalation(
        data.device_id, emergency_locked, data.timestamp, escalation_state
    )

    if escalation:
        print(f"[ESCALATION] Device: {data.device_id} | Level: {escalation}")

    alert_triggered = False
    if should_alert(data.device_id, risk, data.timestamp, alert_state):
        alert_state[data.device_id] = data.timestamp
        alert_triggered = True
        print(f"[ALERT] Device: {data.device_id} | Risk: {risk} | Time: {data.timestamp}")

    payload = {
        "device_id":  data.device_id,
        "latitude":   data.latitude,
        "longitude":  data.longitude,
        "speed":      data.speed,
        "context":    context,
        "battery":    data.battery,
        "emergency":  emergency_locked,
        "risk":       risk,
        "timestamp":  data.timestamp,
        "alert":      alert_triggered,
        "escalation": escalation,
        "reset":      was_reset,
    }

    device_state[data.device_id] = payload

    if data.device_id not in device_history:
        device_history[data.device_id] = deque(maxlen=HISTORY_MAXLEN)
    device_history[data.device_id].append(payload)

    # Keep the Postgres `devices` row's live fields in sync too, so the
    # User Dashboard's device list reflects real-time battery/status
    # without needing its own polling loop. Silently skipped if this
    # device isn't a User Dashboard device (no matching row) or Postgres
    # logging is disabled.
    try:
        await user_db.update_device_status(
            data.device_id,
            battery=data.battery,
            last_seen=data.timestamp,
            status=("emergency" if emergency_locked else "online"),
        )
    except Exception:
        pass  # e.g. simulator/Trial_Dashboard devices with no `devices` row

    # ── Postgres logging ──────────────────────────────────────────────────
    await db.insert_normal_log(data.device_id, payload)

    was_emergency_before = prev.get("emergency", False)

    if emergency_locked and not was_emergency_before:
        # Emergency just started this tick
        em_id = await db.start_emergency_log(data.device_id, data.timestamp)
        await db.append_emergency_log(data.device_id, em_id, payload)

        # ── Notify responders + this device's emergency contacts ──────────
        # Look up which User Dashboard user owns this device (None for
        # simulator/Trial_Dashboard devices that were never registered via
        # /user/devices/register — those just skip notification).
        owner_user_id = None
        try:
            owner_user_id = await user_db.get_owner_user_id(data.device_id)
        except Exception:
            pass

        if owner_user_id:
            user_row = await user_db.get_user(owner_user_id)
            display_name = user_row["name"] if user_row else owner_user_id
            alert_result = await _trigger_responder_alert(
                data.device_id, owner_user_id, display_name,
                data.latitude, data.longitude,
            )
            responder_token = alert_result["token"] if alert_result else None
            await user_db.create_incident(
                data.device_id, owner_user_id, em_id, data.timestamp,
                responder_token=responder_token,
            )

    elif emergency_locked and was_emergency_before:
        # Emergency continuing — append this tick to the active emergency table
        active_em_id = await db.get_active_emergency_id(data.device_id)
        if active_em_id:
            await db.append_emergency_log(data.device_id, active_em_id, payload)

    elif was_reset and was_emergency_before:
        # Emergency just ended — close out the registry entry.
        active_em_id = await db.get_active_emergency_id(data.device_id)
        if active_em_id:
            await db.append_emergency_log(data.device_id, active_em_id, payload)
            await db.close_emergency_log(data.device_id, active_em_id, data.timestamp)

        try:
            incident = await user_db.get_active_incident_for_device(data.device_id)
            if incident:
                await user_db.close_incident(incident["incident_id"], data.timestamp)
        except Exception:
            pass

    await manager.broadcast(payload)
    return {"status": "broadcasted", "risk": risk}


# ---------------------------------------------------------------------------
# 5.1 — Read endpoints (auth protected)
# ---------------------------------------------------------------------------

@app.get(
    "/device/{device_id}",
    dependencies=[Depends(verify_api_key)]
)
def get_device(device_id: str):
    return device_state.get(device_id, {"error": "Device not found"})


@app.get(
    "/device/{device_id}/history",
    dependencies=[Depends(verify_api_key)]
)
@limiter.limit("30/minute")   # 5.3: history fetches are heavier, lower cap
def get_device_history(request: Request, device_id: str):
    return list(device_history.get(device_id, []))


# ---------------------------------------------------------------------------
# NOTE — User registration/login/contacts/devices/preferences/incidents all
# live in app/user_routes.py (mounted above via include_router). There used
# to be a duplicate, in-memory-only /user/register and /user/login defined
# directly on `app` here — removed, because it shadowed the real
# Postgres-backed router and meant every other user endpoint (contacts,
# devices, preferences, incidents) silently 404'd in production.
# ---------------------------------------------------------------------------