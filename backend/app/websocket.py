"""
user_routes.py — FastAPI routes for the User Dashboard.

Kept in a separate APIRouter (rather than piling everything into main.py)
so the device-simulation endpoints and the user-account endpoints stay
clearly separated as the project grows. Mounted in main.py with:

    from app.user_routes import router as user_router
    app.include_router(user_router)
"""

from datetime import datetime
from typing import List
from fastapi import WebSocket
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import verify_api_key
from app.schemas import (
    UserRegister,
    EmergencyContactIn, EmergencyContactUpdate,
    DeviceRegisterForUser, PreferencesUpdate,
)
from app import user_db

router = APIRouter(prefix="/user", tags=["user"])


# ---------------------------------------------------------------------------
# Registration + Login
#
# Registration OTP is handled ENTIRELY by an external Google Apps Script
# web app (OTP_Registration_Backend.gs) — its own Sheet, its own regId,
# its own OTP round-trip. This backend is not involved in that exchange at
# all. The frontend flow is:
#
#   1. Frontend calls the Apps Script's action=register directly (not this
#      backend) — Apps Script emails an OTP and returns a regId.
#   2. Frontend calls the Apps Script's action=verify with the code the user
#      typed — THIS is the authoritative proof of email ownership.
#   3. Only once that succeeds does the frontend call THIS backend's
#      POST /user/register — which creates the actual ResQNet domain
#      record (user_id, hashed password) with verified=True immediately,
#      since email ownership was already proven in step 2.
#
# Login is separate and uses a password — deliberately NOT OTP. This is a
# temporary stopgap; Firebase will eventually own login entirely, at which
# point POST /user/login and verify_login() can be retired.
# ---------------------------------------------------------------------------


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
 
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
 
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
 
    # FIX #4: Per-connection error handling so a single dropped client
    # doesn't abort the broadcast loop and starve remaining connections.
    # Dead connections are collected and removed after the loop.
    async def broadcast(self, message: dict):
        dead: List[WebSocket] = []
 
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                print(f"⚠️  Broadcast failed for a connection, removing. Reason: {e}")
                dead.append(connection)
 
        for conn in dead:
            self.disconnect(conn)
 
 
@router.post("/register", dependencies=[Depends(verify_api_key)])
async def register_user(data: UserRegister):
    """
    Creates the ResQNet user record. Call this ONLY after the frontend has
    already completed the Apps Script's OTP verify step — this endpoint
    performs no OTP check of its own; it trusts that the caller already did.
    """
    try:
        user = await user_db.create_user(
            data.name, data.dob, data.phone, data.email, data.password
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this phone number or email already exists."
        )

    return {
        "status": "registered",
        "user_id": user["user_id"],
        "message": "Account created.",
    }





# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------

@router.get("/{user_id}", dependencies=[Depends(verify_api_key)])
async def get_user_profile(user_id: str):
    user = await user_db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    contacts = await user_db.list_emergency_contacts(user_id)
    devices  = await user_db.list_user_devices(user_id)
    prefs    = await user_db.get_preferences(user_id)

    # asyncpg Record -> dict already done in user_db; dates need str conversion
    user["dob"] = user["dob"].isoformat() if hasattr(user["dob"], "isoformat") else user["dob"]

    # NEVER return the password hash to any client, even hashed. Strip it
    # unconditionally before this dict leaves the backend.
    user.pop("password_hash", None)

    return {
        "user": user,
        "emergency_contacts": contacts,
        "devices": devices,
        "preferences": prefs,
    }


# ---------------------------------------------------------------------------
# Emergency contacts (max 3, priority-ordered)
# ---------------------------------------------------------------------------

@router.post("/{user_id}/contacts", dependencies=[Depends(verify_api_key)])
async def add_contact(user_id: str, data: EmergencyContactIn):
    user = await user_db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    try:
        contact = await user_db.add_emergency_contact(
            user_id, data.name, data.email, data.phone, data.priority,
            data.notify_email, data.notify_sms, data.notify_whatsapp,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail=f"Priority {data.priority} is already assigned to another contact. "
                   f"Each of the 3 contacts must have a unique priority (1, 2, or 3)."
        )

    return contact


@router.get("/{user_id}/contacts", dependencies=[Depends(verify_api_key)])
async def get_contacts(user_id: str):
    return await user_db.list_emergency_contacts(user_id)


@router.patch("/contacts/{contact_id}", dependencies=[Depends(verify_api_key)])
async def edit_contact(contact_id: int, data: EmergencyContactUpdate):
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields provided to update.")
    try:
        await user_db.update_emergency_contact(contact_id, **fields)
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail="That priority is already taken by another contact for this user."
        )
    return {"status": "updated"}


@router.delete("/contacts/{contact_id}", dependencies=[Depends(verify_api_key)])
async def delete_contact(contact_id: int):
    await user_db.remove_emergency_contact(contact_id)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

@router.post("/devices/register", dependencies=[Depends(verify_api_key)])
async def register_device_for_user(data: DeviceRegisterForUser):
    user = await user_db.get_user(data.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    device = await user_db.register_user_device(data.user_id, data.friendly_name)

    # Also register in the in-memory registered_devices set used by the
    # existing simulator-facing /device/update auth check (storage.py),
    # so this device can immediately start sending updates.
    from app.storage import registered_devices
    registered_devices.add(device["device_id"])

    return device


@router.get("/{user_id}/devices", dependencies=[Depends(verify_api_key)])
async def get_user_devices(user_id: str):
    return await user_db.list_user_devices(user_id)


@router.delete("/devices/{device_id}", dependencies=[Depends(verify_api_key)])
async def delete_device(device_id: str):
    await user_db.remove_device(device_id)
    from app.storage import registered_devices
    registered_devices.discard(device_id)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

@router.get("/{user_id}/preferences", dependencies=[Depends(verify_api_key)])
async def get_preferences(user_id: str):
    prefs = await user_db.get_preferences(user_id)
    if prefs is None:
        raise HTTPException(status_code=404, detail="Preferences not found for this user.")
    return prefs


@router.patch("/{user_id}/preferences", dependencies=[Depends(verify_api_key)])
async def update_preferences(user_id: str, data: PreferencesUpdate):
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields provided to update.")
    await user_db.update_preferences(user_id, **fields)
    return {"status": "updated"}


# ---------------------------------------------------------------------------
# Incident history
# ---------------------------------------------------------------------------

@router.get("/{user_id}/incidents", dependencies=[Depends(verify_api_key)])
async def get_incidents(user_id: str):
    return await user_db.list_user_incidents(user_id)


# ---------------------------------------------------------------------------
# Incident resolution — called by the responder dashboard's resolve action,
# in ADDITION to the session-token Apps Script's own action=resolve. The
# two systems don't share a database: the Apps Script closes the Sheet row
# (which kills the magic link), and this closes the Postgres `incidents`
# row (which is what the User Dashboard's incident history reads from).
# No X-API-Key dependency here — the responder page has no login, so the
# token itself (already validated against the Apps Script) is the
# credential, matching the pattern used there.
# ---------------------------------------------------------------------------

from datetime import datetime as _datetime


@router.post("/incidents/resolve-by-token")
async def resolve_incident_by_token(body: dict):
    token = str(body.get("token", "")).strip().upper()
    if not token:
        raise HTTPException(status_code=400, detail="Missing token")

    ended_at = int(_datetime.utcnow().timestamp())
    incident_id = await user_db.close_incident_by_token(token, ended_at)
    if not incident_id:
        raise HTTPException(status_code=404, detail="No active incident found for that token")

    return {"status": "resolved", "incident_id": incident_id}