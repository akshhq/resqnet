import requests
import time
import threading
import random
import sys
import argparse

# Fix 1.10: read URL and device ID from CLI args so a second simulator
# instance can be started without editing source code.
# Usage: python simulator.py --url http://1.2.3.4:8000/device/update --id SIM_02
parser = argparse.ArgumentParser(description="ResQNet Device Simulator")
parser.add_argument(
    "--url",
    default="http://127.0.0.1:8000/device/update",
    help="Backend /device/update endpoint (default: http://127.0.0.1:8000/device/update)"
)
parser.add_argument(
    "--id",
    default="SIM_DEVICE_01",
    help="Device ID to send with every update (default: SIM_DEVICE_01)"
)
args = parser.parse_args()

BACKEND_URL = args.url
DEVICE_ID = args.id

latitude = 28.6139
longitude = 77.2090

battery = 100.0   # Fix 1.4: float so sub-integer drain is tracked accurately
low_battery_warned = False  # Fix 1.4: ensure the 20% warning fires only once
mode = "walking"
emergency = False
reset = False

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
    # Fix 1.11: use signed random offsets so the device can move in any
    # direction rather than always drifting northeast off the visible map.
    latitude += random.uniform(-0.0002, 0.0002)
    longitude += random.uniform(-0.0002, 0.0002)


def send_loop():
    global battery, reset, low_battery_warned

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
                "battery": round(battery),  # Fix 1.4: send rounded int; internal tracking stays float
                "emergency": emergency,
                "reset": reset
            }

        try:
            r = requests.post(BACKEND_URL, json=payload, timeout=2)
            print("Sent:", payload, "→", r.status_code)
        except Exception as e:
            print("Send failed:", e)

        with lock:
            reset = False

        # Fix 1.4: only drain if battery is above 0, and warn once at 20%
        if battery > 0:
            battery = max(battery - 0.05, 0)

        if battery <= 20 and not low_battery_warned:
            low_battery_warned = True
            print("⚠️  LOW BATTERY: device battery at or below 20%")

        time.sleep(1)


# FIX #6: Single-keypress input — no Enter required.
# Uses tty/termios on Unix/macOS, msvcrt on Windows.
def read_key():
    """Read a single character without waiting for Enter."""
    if sys.platform == "win32":
        import msvcrt
        return msvcrt.getwch()
    else:
        import tty
        import termios
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def input_loop():
    global emergency, mode, reset

    print("\n--- CONTROLS ---")
    print("p  → panic ON")
    print("r  → reset panic")
    print("1  → walking")
    print("2  → running")
    print("3  → vehicle")
    print("q  → quit\n")

    while True:
        key = read_key()

        with lock:
            if key == "p":
                emergency = True
                reset = False
                print("🚨 PANIC TRIGGERED")

            elif key == "r":
                emergency = False
                reset = True
                print("✅ PANIC RESET REQUESTED")

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
    print(f"  Device ID : {DEVICE_ID}")
    print(f"  Backend   : {BACKEND_URL}")
    threading.Thread(target=send_loop, daemon=True).start()
    # Fix 3.1: catch KeyboardInterrupt so Ctrl+C exits cleanly rather than
    # printing a traceback and leaving the backend with a dangling connection.
    try:
        input_loop()
    except KeyboardInterrupt:
        print("\nSimulator stopped.")
        sys.exit(0)