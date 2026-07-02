from pydantic import BaseModel, Field, EmailStr, field_validator
from datetime import date
from typing import Optional


class DeviceUpdate(BaseModel):
    device_id: str
    timestamp: int
    latitude:  float = Field(..., ge=-90,  le=90)
    longitude: float = Field(..., ge=-180, le=180)
    speed:     float = Field(..., ge=0)        # m/s — cannot be negative
    battery:   int   = Field(..., ge=0, le=100) # % — 0–100 only
    emergency: bool
    reset:     bool = False


class DeviceRegister(BaseModel):
    """Used by POST /device/register (5.4)."""
    device_id: str = Field(..., min_length=1, max_length=64)


# ---------------------------------------------------------------------------
# User Dashboard — registration, OTP, contacts, devices, preferences
# ---------------------------------------------------------------------------

class UserRegister(BaseModel):
    name:  str = Field(..., min_length=2, max_length=100)
    dob:   date
    phone: str = Field(..., min_length=8, max_length=16)
    email: EmailStr

    @field_validator("phone")
    @classmethod
    def phone_digits_only(cls, v: str) -> str:
        cleaned = v.strip().replace(" ", "").replace("-", "")
        if not cleaned.lstrip("+").isdigit():
            raise ValueError("Phone number must contain only digits, spaces, dashes, or a leading +")
        return cleaned

    @field_validator("dob")
    @classmethod
    def dob_not_future(cls, v: date) -> date:
        if v >= date.today():
            raise ValueError("Date of birth must be in the past")
        return v


class EmailOtpRequest(BaseModel):
    """Used by POST /user/login and POST /user/resend-otp."""
    email: EmailStr
    purpose: str = Field(default="login", max_length=32)


class EmailOtpVerify(BaseModel):
    """
    Sent by the frontend after the user reads their emailed code and types
    it in. purpose distinguishes a fresh registration (activates the
    account) from an ordinary login (just needs a valid, matching code).
    """
    email:   EmailStr
    code:    str = Field(..., min_length=4, max_length=8)
    purpose: str = Field(default="registration", max_length=32)


class EmailQueueMarkFailed(BaseModel):
    """Body for POST /email-queue/{id}/mark-failed — called by the Apps
    Script sender when GmailApp.sendEmail() throws."""
    error: str = Field(..., max_length=2000)


class EmergencyContactIn(BaseModel):
    name:     str = Field(..., min_length=1, max_length=100)
    phone:    str = Field(..., min_length=8, max_length=16)
    email:    Optional[EmailStr] = None
    priority: int = Field(..., ge=1, le=3)
    notify_sms:      bool = True
    notify_whatsapp: bool = True
    notify_email:    bool = True


class EmergencyContactUpdate(BaseModel):
    """All fields optional — only provided fields are updated."""
    name:     Optional[str] = Field(None, min_length=1, max_length=100)
    phone:    Optional[str] = Field(None, min_length=8, max_length=16)
    email:    Optional[EmailStr] = None
    priority: Optional[int] = Field(None, ge=1, le=3)
    notify_sms:      Optional[bool] = None
    notify_whatsapp: Optional[bool] = None
    notify_email:    Optional[bool] = None


class DeviceRegisterForUser(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)
    friendly_name: str = Field(..., min_length=1, max_length=64)


class PreferencesUpdate(BaseModel):
    notify_on_emergency:    Optional[bool] = None
    notify_on_escalation:   Optional[bool] = None
    notify_on_low_battery:  Optional[bool] = None
    quiet_hours_enabled:    Optional[bool] = None
    quiet_hours_start:      Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_hours_end:        Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    language:               Optional[str] = Field(None, min_length=2, max_length=8)