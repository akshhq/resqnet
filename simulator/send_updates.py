import os
import time
import requests

BACKEND_URL = os.getenv("RESQNET_BACKEND_URL", "https://resqnet-gti8.onrender.com")
URL = f"{BACKEND_URL.rstrip('/')}/device/update"

# 5.1: read API key from environment
API_KEY = os.getenv("API_KEY", "")
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

device_id = "LIVE_MAP"

lat = 28.6100
lng = 77.2000
speed = 0.8
MAX_SPEED = 3.0  # FIX #11: cap speed so device stays in "running" range, not "vehicle"

for i in range(10):
    payload = {
        "device_id": device_id,
        "timestamp": int(time.time()),
        "latitude": lat,
        "longitude": lng,
        "speed": speed,
        "battery": 80,
        "emergency": False,
        "reset": False
    }

    # FIX #10: error handling so the loop continues if the backend is down
    try:
        res = requests.post(URL, json=payload, headers=HEADERS, timeout=3)
        print(res.json())
    except requests.exceptions.ConnectionError:
        print(f"[{i+1}/10] Backend unreachable — retrying next tick")
    except requests.exceptions.Timeout:
        print(f"[{i+1}/10] Request timed out")
    except Exception as e:
        print(f"[{i+1}/10] Unexpected error: {e}")

    # Simulate movement
    lat += 0.0003
    lng += 0.0003
    speed = min(speed + 0.4, MAX_SPEED)  # FIX #11: capped growth

    time.sleep(1)