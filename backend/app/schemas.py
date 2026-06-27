from pydantic import BaseModel, Field
from typing import Optional


class DeviceUpdate(BaseModel):
    device_id: str
    timestamp: int
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    speed: float = Field(..., ge=0)      # m/s — cannot be negative
    battery: int = Field(..., ge=0, le=100)  # % — 0–100 only
    emergency: bool
    reset: bool = False