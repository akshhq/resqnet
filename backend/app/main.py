import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.schemas import DeviceUpdate
from app.storage import (
    device_state,
    device_history,
    alert_state,
    escalation_state
)
from app.context import (
    classify_context,
    detect_speed_anomaly,
    calculate_risk,
    should_alert,
    check_escalation
)
from app.websocket import ConnectionManager


# FIX #7: lifespan replaces the deprecated @app.on_event("startup")
@asynccontextmanager
async def lifespan(app: FastAPI):
    device_state.clear()
    device_history.clear()
    alert_state.clear()
    escalation_state.clear()
    print("🔄 Runtime state cleared")
    yield
    # (teardown logic can go here if needed in future)


app = FastAPI(title="ResQNet Backend", version="0.4", lifespan=lifespan)


# FIX #12: CORS origins read from environment variable so they're easy to
# change across dev / staging / production without touching source code.
# Default covers local dev with Live Server.
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


@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post("/device/update")
async def device_update(data: DeviceUpdate):
    prev = device_state.get(data.device_id, {})
    prev_speed = prev.get("speed", data.speed)

    if data.reset:
        emergency_locked = False
        alert_state.pop(data.device_id, None)
        escalation_state.pop(data.device_id, None)
    else:
        emergency_locked = prev.get("emergency", False) or data.emergency

    anomaly = detect_speed_anomaly(prev_speed, data.speed)
    context = classify_context(data.speed)
    risk = calculate_risk(emergency_locked, anomaly)

    escalation = check_escalation(
        data.device_id,
        emergency_locked,
        data.timestamp,
        escalation_state
    )

    if escalation:
        print(f"🚨 ESCALATION | Device: {data.device_id} | Level: {escalation}")

    alert_triggered = False

    if should_alert(data.device_id, risk, data.timestamp, alert_state):
        alert_state[data.device_id] = data.timestamp
        alert_triggered = True
        print(
            f"🚨 ALERT | Device: {data.device_id} | Risk: {risk} | Time: {data.timestamp}"
        )

    payload = {
        "device_id": data.device_id,
        "latitude": data.latitude,
        "longitude": data.longitude,
        "speed": data.speed,
        "context": context,
        "battery": data.battery,
        "emergency": emergency_locked,
        "risk": risk,
        "timestamp": data.timestamp,
        "alert": alert_triggered,
        "escalation": escalation
    }

    device_state[data.device_id] = payload

    if data.device_id not in device_history:
        device_history[data.device_id] = []

    device_history[data.device_id].append(payload)

    await manager.broadcast(payload)

    return {"status": "broadcasted", "risk": risk}


@app.get("/device/{device_id}")
def get_device(device_id: str):
    return device_state.get(device_id, {"error": "Device not found"})


# FIX #2: History endpoint that replay() in script.js actually calls
@app.get("/device/{device_id}/history")
def get_device_history(device_id: str):
    return device_history.get(device_id, [])


@app.get("/test/broadcast")
async def test_broadcast():
    test_payload = {
        "device_id": "TEST",
        "latitude": 28.61,
        "longitude": 77.20,
        "speed": 1.0,
        "context": "walking",
        "battery": 90,
        "emergency": False,
        "risk": "normal",
        "timestamp": 1700000000,
        "alert": False,
        "escalation": None
    }
    await manager.broadcast(test_payload)
    return {"sent": True}