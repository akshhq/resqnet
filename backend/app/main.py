import os
from collections import deque
from contextlib import asynccontextmanager

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


# ---------------------------------------------------------------------------
# Rate limiter (5.3)
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address, default_limits=[])


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
    print("  ResQNet Backend v0.6")
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

    await db.init_db()
    print("=" * 52)
    yield
    await db.close_db()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="ResQNet Backend", version="0.5", lifespan=lifespan)

# Attach rate-limit exceeded handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# CORS
_raw_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5500,http://127.0.0.1:5500"
)
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = ConnectionManager()


# ---------------------------------------------------------------------------
# 5.2 — WebSocket endpoint with token auth
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

    print(f"✅ Device registered: {data.device_id}  (total: {len(registered_devices)})")
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
        print(f"🚨 ESCALATION | Device: {data.device_id} | Level: {escalation}")

    alert_triggered = False
    if should_alert(data.device_id, risk, data.timestamp, alert_state):
        alert_state[data.device_id] = data.timestamp
        alert_triggered = True
        print(f"🚨 ALERT | Device: {data.device_id} | Risk: {risk} | Time: {data.timestamp}")

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

    # ── Postgres logging ──────────────────────────────────────────────────
    # 1. Every tick always goes into the device's normal circular-buffer
    #    table (500-row cap, enqueue+dequeue once full).
    # 2. If this tick is part of an active emergency, it ALSO gets appended
    #    to that emergency's dedicated table (uncapped).
    # 3. Emergency start (False→True transition): create the emergency
    #    table, pre-seed it with 5 min of pre-trigger context.
    # 4. Emergency end (reset): mark the emergency table closed with an
    #    end timestamp. The table itself is never deleted.
    await db.insert_normal_log(data.device_id, payload)

    was_emergency_before = prev.get("emergency", False)

    if emergency_locked and not was_emergency_before:
        # Emergency just started this tick
        em_id = await db.start_emergency_log(data.device_id, data.timestamp)
        await db.append_emergency_log(data.device_id, em_id, payload)

    elif emergency_locked and was_emergency_before:
        # Emergency continuing — append this tick to the active emergency table
        active_em_id = await db.get_active_emergency_id(data.device_id)
        if active_em_id:
            await db.append_emergency_log(data.device_id, active_em_id, payload)

    elif was_reset and was_emergency_before:
        # Emergency just ended — close out the registry entry.
        # The final tick itself was already an "emergency" tick a moment ago
        # (was_emergency_before=True), so log this reset tick too before closing.
        active_em_id = await db.get_active_emergency_id(data.device_id)
        if active_em_id:
            await db.append_emergency_log(data.device_id, active_em_id, payload)
            await db.close_emergency_log(data.device_id, active_em_id, data.timestamp)

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