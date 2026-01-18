import requests
import time
import threading
import random
import sys

BACKEND_URL = "http://127.0.0.1:8000/device/update"

DEVICE_ID = "SIM_DEVICE_01"

# Initial position (Delhi)
latitude = 28.6139
longitude = 77.2090

battery = 100
emergency = False
mode = "walking"  # walking | running | vehicle

lock = threading.Lock()


def get_speed():
    if mode == "walking":
        return random.uniform(0.8, 1.4)
    elif mode == "running":
        return random.uniform(2.0, 3.2)
    elif mode == "vehicle":
        return random.uniform(8.0, 15.0)
    return 0.0


def move():
    global latitude, longitude
    latitude += random.uniform(0.00005, 0.0002)
    longitude += random.uniform(0.00005, 0.0002)


def send_loop():
    global battery

    while True:
        with lock:
            speed = get_speed()
            move()

            payload = {
                "device_id": DEVICE_ID,
                "timestamp": int(time.time()),
                "latitude": latitude,
                "longitude": longitude,
                "speed": speed,
                "battery": int(battery),
                "emergency": emergency
            }

        try:
            r = requests.post(BACKEND_URL, json=payload, timeout=2)
            print("Sent:", payload, "→", r.status_code)
        except Exception as e:
            print("Send failed:", e)

        battery = max(battery - 0.05, 0)
        time.sleep(1)


def input_loop():
    global emergency, mode

    print("\n--- CONTROLS ---")
    print("p  → panic ON")
    print("r  → reset panic")
    print("1  → walking")
    print("2  → running")
    print("3  → vehicle")
    print("q  → quit\n")

    while True:
        key = sys.stdin.read(1)

        with lock:
            if key == "p":
                emergency = True
                print("🚨 PANIC TRIGGERED")
            elif key == "r":
                emergency = False
                print("✅ PANIC RESET")
            elif key == "1":
                mode = "walking"
                print("Mode: walking")
            elif key == "2":
                mode = "running"
                print("Mode: running")
            elif key == "3":
                mode = "vehicle"
                print("Mode: vehicle")
            elif key == "q":
                print("Exiting simulator")
                sys.exit(0)


if __name__ == "__main__":
    print("Starting ResQNet Device Simulator")
    threading.Thread(target=send_loop, daemon=True).start()
    input_loop()
