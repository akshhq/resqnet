"""
ResQNet Device Simulator
Realistic human-movement model using heading + smoothed speed.
"""

import math
import os
import random
import requests
import sys
import argparse
import threading
import time

# Auto-load backend/.env so the API key is always found,
# even if the user didn't set the env var in this terminal.
try:
    from dotenv import load_dotenv
    for _p in [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend', '.env'),
        os.path.join(os.getcwd(), 'backend', '.env'),
    ]:
        if os.path.exists(_p):
            load_dotenv(_p)
            break
except ImportError:
    pass  # dotenv not installed — relies on env var or --key flag

# ---------------------------------------------------------------------------
# CLI args
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="ResQNet Device Simulator")
parser.add_argument(
    "--url",
    default="https://resqnet-gti8.onrender.com/device/update",
    help="Backend /device/update endpoint (default: https://resqnet-gti8.onrender.com/device/update)"
)
parser.add_argument(
    "--id",
    default="SIM_DEVICE_01",
    help="Device ID to send with every update (default: SIM_DEVICE_01)"
)
parser.add_argument(
    "--demo",
    action="store_true",
    help="Run the scripted demo scenario automatically instead of interactive mode"
)
parser.add_argument(
    "--lat",
    type=float,
    default=28.6139,
    help="Starting latitude (default: 28.6139 — New Delhi)"
)
parser.add_argument(
    "--lng",
    type=float,
    default=77.2090,
    help="Starting longitude (default: 77.2090 — New Delhi)"
)
parser.add_argument(
    "--key",
    default=os.getenv("API_KEY", ""),
    help="API key for backend auth. Reads API_KEY env var if not set."
)
args = parser.parse_args()

BACKEND_URL = args.url
DEVICE_ID   = args.id
API_KEY     = args.key

# Header sent with every HTTP request (5.1)
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

# ---------------------------------------------------------------------------
# Movement model constants
# ---------------------------------------------------------------------------

# Earth-scale conversion at the starting latitude.
# Used to translate metres/second into degree offsets per tick.
METERS_PER_DEG_LAT = 111_000
METERS_PER_DEG_LNG = 111_000 * math.cos(math.radians(args.lat))

# Per-mode speed profiles  (mean m/s, std dev, min, max)
SPEED_PROFILES = {
    "stationary": (0.0,  0.05, 0.0,  0.1),   # tiny GPS jitter only
    "walking":    (1.2,  0.25, 0.4,  1.8),   # normal walking pace
    "running":    (3.0,  0.40, 1.8,  4.0),   # jogging / sprinting
    "vehicle":    (11.0, 2.00, 5.0, 16.7),   # ~40 km/h urban traffic
}

# How sharply each mode can turn per second (gaussian std dev, degrees)
TURN_RATE = {
    "stationary": 30.0,   # person fidgets / rotates on the spot
    "walking":    12.0,   # gentle curves, occasional sharp turns
    "running":    8.0,    # fairly straight with gradual curves
    "vehicle":    4.0,    # smooth road-following curves
}

# Probability of a brief pause each tick (only for walking/stationary)
PAUSE_CHANCE = {
    "stationary": 0.0,
    "walking":    0.03,   # ~3 % chance per second — traffic lights, looking at phone
    "running":    0.005,
    "vehicle":    0.0,
}

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

latitude      = args.lat
longitude     = args.lng
heading       = random.uniform(0, 360)   # degrees, 0 = north, clockwise
speed_smooth  = 1.2                      # current smoothed speed (m/s)
paused        = False                    # brief stop (traffic light, etc.)
pause_ticks   = 0                        # ticks remaining in current pause

mode          = "walking"
emergency     = False
reset_flag    = False

battery           = 100.0
low_battery_warned = False

lock = threading.Lock()

# ---------------------------------------------------------------------------
# Movement engine
# ---------------------------------------------------------------------------

def _next_speed(mode_name: str) -> float:
    """Return a target speed sampled from the mode's profile."""
    mean, std, lo, hi = SPEED_PROFILES[mode_name]
    return max(lo, min(hi, random.gauss(mean, std)))


def move_realistic() -> float:
    """
    Advance position by one second tick using a heading-based model.

    Returns the actual speed (m/s) sent this tick — used for the payload.

    Key ideas:
    - Heading drifts smoothly each tick (gaussian turn) so paths curve
      naturally rather than teleporting in random directions.
    - Speed is smoothed with a lerp so acceleration/deceleration is gradual.
    - Brief pauses are injected for walking to simulate traffic lights /
      stopping to look at a phone.
    - GPS jitter (< 1 m) is added on top of real movement, matching what
      real consumer GPS hardware produces.
    """
    global latitude, longitude, heading, speed_smooth, paused, pause_ticks

    # --- Pause logic (walking only) ---
    if paused:
        pause_ticks -= 1
        if pause_ticks <= 0:
            paused = False
        # Still jitter slightly while paused (GPS noise)
        latitude  += random.gauss(0, 0.000003)
        longitude += random.gauss(0, 0.000003)
        return random.uniform(0.0, 0.15)   # near-zero speed while paused

    if random.random() < PAUSE_CHANCE.get(mode, 0.0):
        paused      = True
        pause_ticks = random.randint(2, 8)   # pause 2–8 seconds
        return 0.0

    # --- Heading drift ---
    turn_std = TURN_RATE.get(mode, 10.0)
    heading  = (heading + random.gauss(0, turn_std)) % 360

    # --- Speed smoothing (lerp toward target) ---
    target      = _next_speed(mode)
    speed_smooth += (target - speed_smooth) * 0.25   # 25 % lerp per tick

    # --- Convert heading + speed to lat/lng delta ---
    rad  = math.radians(heading)
    dlat = (speed_smooth * math.cos(rad)) / METERS_PER_DEG_LAT
    dlng = (speed_smooth * math.sin(rad)) / METERS_PER_DEG_LNG

    # --- GPS noise (< 1 m, always present) ---
    dlat += random.gauss(0, 0.000004)
    dlng += random.gauss(0, 0.000004)

    latitude  += dlat
    longitude += dlng

    return round(speed_smooth, 3)


# ---------------------------------------------------------------------------
# Send loop
# ---------------------------------------------------------------------------

def send_loop():
    global battery, reset_flag, low_battery_warned

    while True:
        with lock:
            actual_speed = move_realistic()

            payload = {
                "device_id": DEVICE_ID,
                "timestamp": int(time.time()),
                "latitude":  round(latitude,  7),
                "longitude": round(longitude, 7),
                "speed":     actual_speed,
                "battery":   round(battery),
                "emergency": emergency,
                "reset":     reset_flag,
            }

        try:
            r = requests.post(BACKEND_URL, json=payload, headers=HEADERS, timeout=5.0)
            _log(f"speed={actual_speed:.2f} m/s  heading={heading:.0f}°  "
                 f"mode={mode}  bat={round(battery)}%  "
                 f"em={emergency}  → {r.status_code}")
        except requests.exceptions.ConnectionError:
            _log("Backend unreachable — retrying next tick")
        except requests.exceptions.Timeout:
            _log("Request timed out")
        except Exception as e:
            _log(f"Unexpected error: {e}")

        with lock:
            reset_flag = False

        if battery > 0:
            battery = max(battery - 0.05, 0)

        if battery <= 20 and not low_battery_warned:
            low_battery_warned = True
            print("⚠️  LOW BATTERY: device battery at or below 20%")

        time.sleep(1)


def _log(msg: str):
    print(f"[{DEVICE_ID}] {msg}")


# ---------------------------------------------------------------------------
# Interactive controls
# ---------------------------------------------------------------------------

def read_key() -> str:
    """Read a single character without waiting for Enter."""
    if sys.platform == "win32":
        import msvcrt
        return msvcrt.getwch()
    else:
        import tty
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def input_loop():
    global emergency, mode, reset_flag, heading

    print("\n--- CONTROLS ---")
    print("p  → panic ON")
    print("r  → reset panic")
    print("0  → stationary")
    print("1  → walking")
    print("2  → running")
    print("3  → vehicle")
    print("t  → sharp turn (randomise heading)")
    print("q  → quit\n")

    while True:
        key = read_key()
        with lock:
            if key == "p":
                emergency  = True
                reset_flag = False
                print("🚨 PANIC TRIGGERED")

            elif key == "r":
                emergency  = False
                reset_flag = True
                print("✅ PANIC RESET REQUESTED")

            elif key == "0":
                mode = "stationary"
                print("Mode: stationary")

            elif key == "1":
                mode = "walking"
                print("Mode: walking")

            elif key == "2":
                mode = "running"
                print("Mode: running")

            elif key == "3":
                mode = "vehicle"
                print("Mode: vehicle")

            elif key == "t":
                heading = random.uniform(0, 360)
                print(f"↩  Sharp turn — new heading {heading:.0f}°")

            elif key == "q":
                print("Exiting simulator")
                sys.exit(0)


# ---------------------------------------------------------------------------
# Demo mode (scripted scenario)
# ---------------------------------------------------------------------------

def demo_loop():
    """
    Fix 3.2: scripted demo scenario — no keypresses needed.

    Timeline:
      0s   → walking (normal)
      10s  → panic triggered
      15s  → mode switches to running (speed anomaly visible)
      30s  → backend fires "escalated" automatically
      90s  → backend fires "critical" automatically
      110s → reset sent, returns to normal
      120s → simulator exits cleanly

    Run with:  python simulator.py --demo
    """
    STEPS = [
        (0,   lambda: None,                              "▶  Walking — normal state"),
        (10,  lambda: _demo_set(panic=True),             "🚨 Panic triggered"),
        (15,  lambda: _demo_set(new_mode="running"),     "🏃 Switched to running"),
        (110, lambda: _demo_set(do_reset=True),          "✅ Reset sent — returning to normal"),
        (120, lambda: sys.exit(0),                       "🏁 Demo complete — exiting"),
    ]

    print("\n--- DEMO MODE ---")
    print("Scenario runs automatically. No keypresses needed.")
    print("walk 10s → panic → run → escalate@30s → critical@90s → reset@110s\n")

    start       = time.time()
    step_index  = 0

    while True:
        elapsed = time.time() - start
        if step_index < len(STEPS) and elapsed >= STEPS[step_index][0]:
            _, action, label = STEPS[step_index]
            print(f"[{int(elapsed):>3}s] {label}")
            action()
            step_index += 1
        time.sleep(0.5)


def _demo_set(panic=False, do_reset=False, new_mode=None):
    global emergency, reset_flag, mode
    with lock:
        if panic:
            emergency  = True
            reset_flag = False
        if do_reset:
            emergency  = False
            reset_flag = True
        if new_mode:
            mode = new_mode


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Starting ResQNet Device Simulator")
    print(f"  Device ID  : {DEVICE_ID}")
    print(f"  Backend    : {BACKEND_URL}")
    print(f"  Start pos  : {args.lat}, {args.lng}")
    print(f"  Auth       : {'API key set' if API_KEY else 'NO KEY — backend must have auth disabled'}")
    print(f"  Mode       : {'DEMO' if args.demo else 'INTERACTIVE'}\n")

    # 5.4: auto-register this device before the send loop starts
    _register_url = BACKEND_URL.replace("/device/update", "/device/register")
    try:
        reg = requests.post(
            _register_url,
            json={"device_id": DEVICE_ID},
            headers=HEADERS,
            timeout=5
        )
        if reg.status_code == 200:
            print(f"✅ Device registered: {DEVICE_ID}")
        else:
            print(f"⚠️  Registration returned {reg.status_code}: {reg.text}")
    except Exception as e:
        print(f"⚠️  Could not register device (backend down?): {e}")

    threading.Thread(target=send_loop, daemon=True).start()

    try:
        if args.demo:
            demo_loop()
        else:
            input_loop()
    except KeyboardInterrupt:
        print("\nSimulator stopped.")
        sys.exit(0)