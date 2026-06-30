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
    UserRegister, Msg91TokenVerify,
    EmergencyContactIn, EmergencyContactUpdate,
    DeviceRegisterForUser, PreferencesUpdate,
)
from app import user_db

router = APIRouter(prefix="/user", tags=["user"])


# ---------------------------------------------------------------------------
# Registration + MSG91 OTP verification
#
# How this works with the frontend:
#   1. User fills registration form → frontend calls POST /user/register
#      (this creates the DB row but does NOT send any OTP)
#   2. Frontend loads MSG91 widget JS → calls window.sendOtp(phone)
#      → MSG91 sends the SMS directly, no backend involvement
#   3. User enters code → window.verifyOtp(code) → MSG91 returns access_token
#   4. Frontend calls POST /user/verify-otp with that access_token
#   5. Backend verifies the token with MSG91's server API → activates account
# ---------------------------------------------------------------------------

@router.post("/register", dependencies=[Depends(verify_api_key)])
async def register_user(data: UserRegister):
    """
    Creates the user row with phone_verified=False.
    OTP is handled entirely client-side via MSG91 widget JS —
    this endpoint does NOT send any SMS.
    """
    try:
        user = await user_db.create_user(data.name, data.dob, data.phone, data.email)
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this phone number, email, or name+DOB combination already exists."
        )

    return {
        "status": "registered",
        "user_id": user["user_id"],
        "message": "Account created. Verify your phone number to activate it.",
    }


@router.post("/verify-otp", dependencies=[Depends(verify_api_key)])
async def verify_otp_route(data: Msg91TokenVerify):
    """
    Receives the access_token that MSG91's widget JS returns after the user
    successfully enters their OTP code. Calls MSG91's server-side API to
    confirm the token is genuine, then marks the user's phone as verified.
    """
    try:
        result = await user_db.verify_msg91_token(data.access_token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))

    # MSG91 returns the phone number (identifier) in the response —
    # use it to find and activate the right user.
    phone = result.get("identifier", "").strip()
    if not phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MSG91 did not return a phone number in the token payload."
        )

    # Normalise: MSG91 strips the leading + so "+919876543210" comes back as "919876543210"
    # Our DB stores numbers with the + prefix. Try both.
    user = await user_db.get_user_by_phone(f"+{phone}") or await user_db.get_user_by_phone(phone)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No registered user found for phone {phone}. Register first."
        )

    await user_db.mark_phone_verified(user["user_id"])

    return {
        "status": "verified",
        "user_id": user["user_id"],
        "message": "Phone verified. Account is now active.",
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
            user_id, data.name, data.phone, data.email, data.priority,
            data.notify_sms, data.notify_whatsapp, data.notify_email,
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