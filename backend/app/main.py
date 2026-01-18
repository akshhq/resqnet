from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from app.schemas import DeviceUpdate
from app.context import classify_context, detect_speed_anomaly, calculate_risk
from app.storage import device_state, device_history
from app.websocket import ConnectionManager

app = FastAPI(title="ResQNet Backend", version="0.4")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",
        "http://127.0.0.1:5500"
    ],
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
            await websocket.receive_text()  # keep connection alive
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post("/device/update")
async def device_update(data: DeviceUpdate):
    prev = device_state.get(data.device_id, {})

    prev_speed = prev.get("speed", data.speed)
    emergency_locked = prev.get("emergency", False) or data.emergency

    anomaly = detect_speed_anomaly(prev_speed, data.speed)
    context = classify_context(data.speed)
    risk = calculate_risk(emergency_locked, anomaly)

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
    }

    # Save latest state
    device_state[data.device_id] = payload

    # Initialize history list if not present
    if data.device_id not in device_history:
        device_history[data.device_id] = []

    # Append to timeline
    device_history[data.device_id].append(payload)

    # print("DEVICE UPDATE RECEIVED:", payload["device_id"])

    # 🔴 LIVE BROADCAST
    await manager.broadcast(payload)

    return {"status": "broadcasted", "risk": risk}


@app.get("/device/{device_id}")
def get_device(device_id: str):
    return device_state.get(device_id, {"error": "Device not found"})

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
        "timestamp": 1700000000
    }
    await manager.broadcast(test_payload)
    return {"sent": True}
