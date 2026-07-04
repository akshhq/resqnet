"""
user_routes.py — FastAPI routes for the User Dashboard.

Kept in a separate APIRouter (rather than piling everything into main.py)
so the device-simulation endpoints and the user-account endpoints stay
clearly separated as the project grows. Mounted in main.py with:

    from app.user_routes import router as user_router
    app.include_router(user_router)
"""

from datetime import datetime

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
# Auth is now Firebase. The frontend (app.js) signs up/in via Firebase,
# then calls POST /user/register with the Firebase UID as user_id to create
# the backend profile. No OTP round-trip through this backend; no passwords
# stored. See app.js for the client-side flow.
# ---------------------------------------------------------------------------

@router.post("/register", dependencies=[Depends(verify_api_key)])
async def register_user(data: UserRegister):
    """
    Creates the ResQNet backend profile for a Firebase-authenticated user.
    Called by the frontend after Firebase sign-up succeeds. user_id must
    be the Firebase UID. No password is accepted or stored.
    """
    try:
        user = await user_db.create_user(
            data.user_id, data.name, data.dob, data.phone, data.email
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this phone number, email, or name+DOB combination already exists."
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