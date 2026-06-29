from pydantic import BaseModel, Field


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