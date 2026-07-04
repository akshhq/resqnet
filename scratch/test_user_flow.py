import time
import requests

BASE_URL = "http://127.0.0.1:8000"

def run_tests():
    print("--- STARTING RESQNET END-TO-END INTEGRATION TEST ---")
    
    # 1. Register User
    user_id = f"test_user_{int(time.time())}"
    print(f"1. Registering user: {user_id}...")
    phone_num = str(int(time.time()))[-10:]
    reg_payload = {
        "user_id": user_id,
        "name": "Jane Doe",
        "dob": "1995-05-15",
        "phone": phone_num,
        "email": f"{user_id}@example.com"
    }
    res = requests.post(f"{BASE_URL}/user/register", json=reg_payload)
    assert res.status_code == 200, f"Failed: {res.text}"
    print("   User registered successfully!")

    # 2. Get User Profile
    print("2. Fetching user profile...")
    res = requests.get(f"{BASE_URL}/user/{user_id}")
    assert res.status_code == 200, f"Failed: {res.text}"
    profile = res.json()
    assert profile["user"]["user_id"] == user_id
    print("   Profile matches successfully!")

    # 3. Add Emergency Contact
    print("3. Adding emergency contact...")
    contact_payload = {
        "name": "John Doe",
        "email": "john@example.com",
        "phone": "8888888888",
        "priority": 1,
        "notify_email": True,
        "notify_sms": False,
        "notify_whatsapp": False
    }
    res = requests.post(f"{BASE_URL}/user/{user_id}/contacts", json=contact_payload)
    assert res.status_code == 200, f"Failed: {res.text}"
    print("   Contact added successfully!")

    # 4. Get Contacts List
    print("4. Fetching contacts list...")
    res = requests.get(f"{BASE_URL}/user/{user_id}/contacts")
    assert res.status_code == 200, f"Failed: {res.text}"
    contacts = res.json()
    assert len(contacts) == 1
    assert contacts[0]["name"] == "John Doe"
    print("   Contacts list verified successfully!")

    # 5. Register Device
    print("5. Registering device...")
    dev_payload = {
        "user_id": user_id,
        "friendly_name": "Test Wearable"
    }
    res = requests.post(f"{BASE_URL}/user/devices/register", json=dev_payload)
    assert res.status_code == 200, f"Failed: {res.text}"
    device = res.json()
    device_id = device["device_id"]
    print(f"   Device registered successfully: {device_id}")

    # 6. Get Preferences
    print("6. Getting preferences...")
    res = requests.get(f"{BASE_URL}/user/{user_id}/preferences")
    assert res.status_code == 200, f"Failed: {res.text}"
    prefs = res.json()
    assert prefs["notify_on_emergency"] is True
    print("   Preferences checked successfully!")

    # 7. Update device location ticks (emergency alert)
    print("7. Simulating active emergency device update...")
    update_payload = {
        "device_id": device_id,
        "timestamp": int(time.time()),
        "latitude": 28.6139,
        "longitude": 77.2090,
        "speed": 1.2,
        "battery": 90,
        "emergency": True,
        "reset": False
    }
    res = requests.post(f"{BASE_URL}/device/update", json=update_payload)
    assert res.status_code == 200, f"Failed: {res.text}"
    print("   Emergency update processed successfully!")

    # 8. Fetch Incidents List
    print("8. Checking incidents list...")
    res = requests.get(f"{BASE_URL}/user/{user_id}/incidents")
    assert res.status_code == 200, f"Failed: {res.text}"
    incidents = res.json()
    assert len(incidents) == 1
    assert incidents[0]["status"] == "active"
    assert incidents[0]["device_id"] == device_id
    print("   Active incident found successfully!")

    # 9. Send device reset tick
    print("9. Simulating panic reset...")
    reset_payload = {
        "device_id": device_id,
        "timestamp": int(time.time()),
        "latitude": 28.6139,
        "longitude": 77.2090,
        "speed": 0.0,
        "battery": 90,
        "emergency": False,
        "reset": True
    }
    res = requests.post(f"{BASE_URL}/device/update", json=reset_payload)
    assert res.status_code == 200, f"Failed: {res.text}"
    print("   Reset processed successfully!")

    # 10. Fetch Incidents List again to verify resolution
    print("10. Checking resolved incidents list...")
    res = requests.get(f"{BASE_URL}/user/{user_id}/incidents")
    assert res.status_code == 200, f"Failed: {res.text}"
    incidents = res.json()
    assert len(incidents) == 1
    assert incidents[0]["status"] == "resolved"
    print("   Incident resolved successfully!")

    print("\n--- ALL TESTS COMPLETED SUCCESSFULLY! ---")

if __name__ == "__main__":
    run_tests()
