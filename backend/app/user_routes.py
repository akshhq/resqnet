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
    UserRegister, EmailOtpRequest, EmailOtpVerify,
    EmergencyContactIn, EmergencyContactUpdate,
    DeviceRegisterForUser, PreferencesUpdate,
)
from app import user_db

router = APIRouter(prefix="/user", tags=["user"])


# ---------------------------------------------------------------------------
# Registration + Login — email-based verification
#
# How this works with the frontend:
#   1. Register: user fills form → POST /user/register creates the DB row
#      AND immediately calls send_email_otp() — no separate trigger needed
#      from the frontend, unlike the old MSG91 flow.
#   2. Login: user enters just their email → POST /user/login checks the
#      account exists and is verified, then sends a fresh code the same way.
#   3. Either path ends the same way: user reads the code from their inbox,
#      submits it to POST /user/verify-otp with {email, code, purpose}.
#   4. Delivery itself happens out-of-band: send_email_otp() only enqueues
#      a row in email_queue. A Google Apps Script (external to this
#      codebase) polls GET /email-queue/pending and actually sends the
#      email via GmailApp, then reports back via mark-sent/mark-failed.
#      See EMAIL_QUEUE_INTEGRATION.md for that contract.
# ---------------------------------------------------------------------------

@router.post("/register", dependencies=[Depends(verify_api_key)])
async def register_user(data: UserRegister):
    """
    Creates the user row (verified=False) and immediately enqueues a
    registration verification email.
    """
    try:
        user = await user_db.create_user(data.name, data.dob, data.phone, data.email)
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this phone number, email, or name+DOB combination already exists."
        )

    try:
        await user_db.send_email_otp(data.email, purpose="registration", to_name=data.name)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))

    return {
        "status": "otp_sent",
        "user_id": user["user_id"],
        "message": "Account created. Check your email for a verification code.",
    }


@router.post("/login", dependencies=[Depends(verify_api_key)])
async def login_user(data: EmailOtpRequest):
    """
    Passwordless login, step 1: confirms the account exists and is
    verified, then sends a fresh code to that email.
    """
    user = await user_db.get_user_by_email(data.email)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                             detail="No account found for that email. Register first.")
    if not user["verified"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                             detail="This account has not completed registration verification yet.")

    try:
        await user_db.send_email_otp(data.email, purpose="login", to_name=user["name"])
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))

    return {"status": "otp_sent", "message": "Check your email for a login code."}


@router.post("/resend-otp", dependencies=[Depends(verify_api_key)])
async def resend_otp(data: EmailOtpRequest):
    """Re-sends a fresh code for either purpose ('registration' or 'login')."""
    user = await user_db.get_user_by_email(data.email)
    try:
        await user_db.send_email_otp(
            data.email, purpose=data.purpose,
            to_name=user["name"] if user else None,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    return {"status": "otp_sent"}


@router.post("/verify-otp", dependencies=[Depends(verify_api_key)])
async def verify_otp_route(data: EmailOtpVerify):
    """
    Verifies the code the user read from their email. On purpose=
    'registration' this also activates the account (verified=True).
    """
    ok = await user_db.verify_email_otp(data.email, data.code, purpose=data.purpose)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired code."
        )

    user = await user_db.get_user_by_email(data.email)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if data.purpose == "registration":
        await user_db.mark_verified(user["user_id"])

    return {
        "status": "verified",
        "user_id": user["user_id"],
        "message": "Verified.",
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