"""
seed_simulator_user.py — one-time setup for a dedicated demo account.

Creates:
  - a "Simulator" user (fixed name/phone/email/password below) via the
    real POST /user/register endpoint — this is a genuine account in the
    users table, not a mock. It bypasses the Apps Script OTP step
    deliberately: the backend's /user/register never checks OTP itself
    (that proof happens client-side, in the User Dashboard, before it
    calls this endpoint) — see user_routes.py's module docstring.
  - one device under that user, via POST /user/devices/register, which
    also provisions its Postgres circular-buffer log table and adds it
    to the in-memory registered_devices set so it can immediately start
    receiving /device/update calls.

Run once per environment (local Postgres / Neon):

    python simulator/seed_simulator_user.py --url https://resqnet-gti8.onrender.com --key <API_KEY>

Then feed the printed device_id straight into the existing simulator:

    python simulator/simulator.py --demo --id <printed_device_id> --key <API_KEY>

Re-running this script is safe: if the Simulator user already exists
(409 Conflict), it looks the user up instead of failing, then registers
a fresh device under that same existing account.
"""

import argparse
import os
import sys

import requests

try:
    from dotenv import load_dotenv
    for _p in [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", ".env"),
        os.path.join(os.getcwd(), "backend", ".env"),
    ]:
        if os.path.exists(_p):
            load_dotenv(_p)
            break
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Fixed simulator identity — deliberately obvious/fake so it's never
# mistaken for a real user in the dashboard or Neon table browser.
# ---------------------------------------------------------------------------

SIM_NAME     = "Simulator One"
SIM_DOB      = "2000-01-01"
SIM_PHONE    = "0000000001"
SIM_EMAIL    = "simulator@resqnet.demo"
SIM_PASSWORD = "SimulatorDemo123!"
SIM_DEVICE_FRIENDLY_NAME = "Simulator Device 01"

# Same derivation rule as user_db.generate_user_id() — kept in sync so this
# script can look the account up even without re-registering it.
EXPECTED_USER_ID = "simulator_one_0000000001"


def main():
    parser = argparse.ArgumentParser(description="Seed a demo Simulator user + device")
    parser.add_argument("--url", default="https://resqnet-gti8.onrender.com",
                         help="Backend base URL (default: production Render URL)")
    parser.add_argument("--key", default=os.getenv("API_KEY", ""),
                         help="API key for backend auth. Reads API_KEY env var if not set.")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    headers = {"X-API-Key": args.key} if args.key else {}

    # ── 1. Register (or find) the Simulator user ────────────────────────
    print(f"→ Registering simulator user at {base}/user/register ...")
    res = requests.post(f"{base}/user/register", json={
        "name": SIM_NAME,
        "dob": SIM_DOB,
        "phone": SIM_PHONE,
        "email": SIM_EMAIL,
        "password": SIM_PASSWORD,
    }, headers=headers, timeout=10)

    if res.status_code == 200:
        user_id = res.json()["user_id"]
        print(f"✅ Created simulator user: {user_id}")
    elif res.status_code == 409:
        user_id = EXPECTED_USER_ID
        print(f"ℹ️  Simulator user already exists — reusing: {user_id}")
    else:
        print(f"❌ Registration failed ({res.status_code}): {res.text}")
        sys.exit(1)

    # ── 2. Register one device under that user ───────────────────────────
    print(f"→ Registering a device for {user_id} ...")
    res = requests.post(f"{base}/user/devices/register", json={
        "user_id": user_id,
        "friendly_name": SIM_DEVICE_FRIENDLY_NAME,
    }, headers=headers, timeout=10)

    if res.status_code != 200:
        print(f"❌ Device registration failed ({res.status_code}): {res.text}")
        sys.exit(1)

    device = res.json()
    device_id = device["device_id"]
    print(f"✅ Created simulator device: {device_id}")

    print()
    print("=" * 60)
    print("  Simulator account ready")
    print("=" * 60)
    print(f"  user_id    : {user_id}")
    print(f"  email      : {SIM_EMAIL}")
    print(f"  password   : {SIM_PASSWORD}")
    print(f"  device_id  : {device_id}")
    print()
    print("  Log into the User Dashboard with the email/password above to")
    print("  see this account's device list, contacts, and incidents live.")
    print()
    print("  Now drive live data into it with:")
    print(f"    python simulator/simulator.py --demo --id {device_id} "
          f"--url {base}/device/update --key <API_KEY>")
    print("=" * 60)


if __name__ == "__main__":
    main()
