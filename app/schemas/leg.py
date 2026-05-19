"""Leg DTOs — public + staff variants."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class LegPublic(BaseModel):
    """What a prospect / client can see about a leg."""

    leg_id: int
    leg_code: str
    vessel_name: str
    departure_locode: str
    departure_name: str
    arrival_locode: str
    arrival_name: str
    etd: datetime
    eta: datetime
    public_capacity_palettes: int | None
    available_palettes: int
    public_price_per_palette_eur: Decimal | None
    booking_close_at: datetime | None
