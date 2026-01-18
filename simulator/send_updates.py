import time
import requests

URL = "http://127.0.0.1:8000/device/update"

device_id = "LIVE_MAP"

lat = 28.6100
lng = 77.2000
speed = 0.8

for i in range(10):
    payload = {
        "device_id": device_id,
        "timestamp": int(time.time()),
        "latitude": lat,
        "longitude": lng,
        "speed": speed,
        "battery": 80,
        "emergency": False
    }

    res = requests.post(URL, json=payload)
    print(res.json())

    # simulate movement
    lat += 0.0003
    lng += 0.0003
    speed += 0.4

    time.sleep(1)
