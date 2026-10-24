import uuid
from datetime import time

from pydantic import BaseModel, Field


class PropertyIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    rules: str | None = Field(default=None, max_length=3000)
    type_id: uuid.UUID | None = None
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    suburb: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=50)
    postcode: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    timezone: str | None = Field(default=None, max_length=64)
    checkin_instructions: str | None = Field(default=None, max_length=3000)
    cancellation_template_key: str | None = Field(default=None, max_length=50)


class UnitIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=3000)
    price_per_24h: int | None = Field(default=None, ge=0, le=100_000_00)  # minor units
    cleaning_fee: int | None = Field(default=None, ge=0, le=10_000_00)
    capacity: int | None = Field(default=None, ge=1, le=100)
    quantity: int | None = Field(default=None, ge=1, le=200)
    bedrooms: int | None = Field(default=None, ge=0, le=50)
    bathrooms: int | None = Field(default=None, ge=0, le=50)
    beds: int | None = Field(default=None, ge=0, le=100)
    max_stay_hours: int | None = Field(default=None, ge=24, le=2160)
    checkin_from: time | None = None
    checkin_to: time | None = None
    stay_mode: str | None = Field(default=None, pattern="^(flexible|fixed)$")
    fixed_checkin_time: time | None = None
    fixed_checkout_time: time | None = None
    amenity_ids: list[uuid.UUID] | None = None


class OrderIn(BaseModel):
    ids: list[uuid.UUID]


class RoomIn(BaseModel):
    label: str = Field(min_length=1, max_length=50)


class BlockIn(BaseModel):
    start_local: str  # "2026-11-03T14:00" in the property's own timezone
    end_local: str
    room_id: uuid.UUID | None = None
    all_rooms: bool = False
    note: str | None = Field(default=None, max_length=300)


class ReasonIn(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class DecisionIn(BaseModel):
    decision: str
    note: str | None = Field(default=None, max_length=2000)


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    active: bool = True
