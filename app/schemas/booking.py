"""Booking DTOs — for API + internal use."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

PALLET_FORMATS = {
    "EPAL", "USPAL", "PORTPAL", "IBC", "BIGBAG", "BARRIQUE120", "BARRIQUE140"
}


class BookingItemIn(BaseModel):
    pallet_format: str = Field(..., examples=["EPAL"])
    pallet_count: int = Field(..., gt=0, le=1000)
    cargo_description: str = Field(..., min_length=2, max_length=500)
    unit_weight_kg: Decimal | None = Field(None, ge=0, le=10000)
    stackable: bool = True
    hazardous: bool = False
    imdg_class: str | None = Field(None, max_length=20)
    un_number: str | None = Field(None, max_length=10)
    hs_code: str | None = Field(None, max_length=20)

    @field_validator("pallet_format")
    @classmethod
    def _format_known(cls, v: str) -> str:
        if v not in PALLET_FORMATS:
            raise ValueError(f"Unknown pallet format '{v}'")
        return v


class BookingCreateIn(BaseModel):
    leg_id: int
    items: list[BookingItemIn] = Field(..., min_length=1, max_length=20)
    pickup_address: str | None = Field(None, max_length=500)
    delivery_address: str | None = Field(None, max_length=500)
    shipper_reference: str | None = Field(None, max_length=100)
    notes: str | None = Field(None, max_length=1000)


class BookingItemOut(BaseModel):
    pallet_format: str
    pallet_count: int
    cargo_description: str
    unit_weight_kg: Decimal | None
    stackable: bool
    hazardous: bool


class BookingOut(BaseModel):
    reference: str
    status: str
    leg_id: int
    total_palettes: int
    total_weight_kg: Decimal
    hazardous: bool
    oversize: bool
    estimated_price_eur: Decimal | None
    confirmed_price_eur: Decimal | None
    submitted_at: datetime | None
    confirmed_at: datetime | None
    created_at: datetime
    items: list[BookingItemOut] = []


class CapacityOut(BaseModel):
    leg_id: int
    capacity_palettes: int
    reserved_palettes: int
    available_palettes: int
    occupancy_pct: float
