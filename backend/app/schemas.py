from pydantic import BaseModel
from typing import Optional


class DeviceUpdate(BaseModel):
    device_id: str
    timestamp: int
    latitude: float
    longitude: float
    speed: float  # m/s
    battery: int  # %
    emergency: bool
