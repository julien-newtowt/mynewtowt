"""Pricing service — computes an indicative price for a booking.

Real grids live in `rate_grids`/`rate_grid_lines` (carried over from V2);
this V3 version implements the simple formula described in
docs/booking/01-cale-booking-platform.md §4.2, with two dynamic levers:

- Early-bird discount when ETD > 30 days and occupancy < 50%
- Late-seat surcharge when ETD < 7 days and occupancy > 85%

The full negotiated grid path is exercised through the optional
``client_account_segment`` argument: clients flagged ``key_account`` skip
dynamic adjustments because they have a contractual fixed rate that
overrides public pricing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.services.capacity import CapacityInfo

# Format coefficients applied to the per-pallet base price
PALLET_COEFS: dict[str, Decimal] = {
    "EPAL": Decimal("1.0"),
    "USPAL": Decimal("1.2"),
    "PORTPAL": Decimal("1.2"),
    "IBC": Decimal("1.3"),
    "BIGBAG": Decimal("1.25"),
    "BARRIQUE120": Decimal("1.5"),
    "BARRIQUE140": Decimal("2.0"),
}

DOCS_FEE_EUR = Decimal("50")
HAZARDOUS_MULTIPLIER = Decimal("1.25")
OVERSIZE_MULTIPLIER = Decimal("1.40")
DEFAULT_BASE_PRICE_EUR = Decimal("38")


@dataclass(frozen=True)
class PriceLine:
    pallet_format: str
    pallet_count: int
    unit_price_eur: Decimal
    subtotal_eur: Decimal


@dataclass(frozen=True)
class PriceQuote:
    base_price_per_palette_eur: Decimal
    lines: list[PriceLine]
    subtotal_eur: Decimal
    hazardous_surcharge_eur: Decimal
    oversize_surcharge_eur: Decimal
    docs_fee_eur: Decimal
    total_eur: Decimal
    applied_multipliers: list[str]


def compute_quote(
    *,
    base_price_per_palette_eur: Decimal | None,
    items: list[tuple[str, int]],          # [(format, count), ...]
    hazardous: bool,
    oversize: bool,
    etd: datetime,
    capacity: CapacityInfo,
    client_segment: str = "occasional",
) -> PriceQuote:
    """Return a price quote.

    Pure function: no DB calls, no IO. Callers compose the result with
    persistence and presentation layers.
    """
    base = base_price_per_palette_eur or DEFAULT_BASE_PRICE_EUR

    if client_segment != "key_account":
        base = _apply_dynamic(base, etd=etd, capacity=capacity)

    lines: list[PriceLine] = []
    subtotal = Decimal("0")
    for fmt, count in items:
        coef = PALLET_COEFS.get(fmt, Decimal("1.0"))
        unit = (base * coef).quantize(Decimal("0.01"))
        line_total = (unit * Decimal(count)).quantize(Decimal("0.01"))
        lines.append(
            PriceLine(
                pallet_format=fmt,
                pallet_count=count,
                unit_price_eur=unit,
                subtotal_eur=line_total,
            )
        )
        subtotal += line_total

    applied: list[str] = []
    hazardous_surcharge = Decimal("0")
    oversize_surcharge = Decimal("0")
    if hazardous:
        hazardous_surcharge = (subtotal * (HAZARDOUS_MULTIPLIER - 1)).quantize(Decimal("0.01"))
        applied.append("hazardous")
    if oversize:
        oversize_surcharge = (subtotal * (OVERSIZE_MULTIPLIER - 1)).quantize(Decimal("0.01"))
        applied.append("oversize")

    total = (subtotal + hazardous_surcharge + oversize_surcharge + DOCS_FEE_EUR).quantize(
        Decimal("0.01")
    )
    return PriceQuote(
        base_price_per_palette_eur=base,
        lines=lines,
        subtotal_eur=subtotal.quantize(Decimal("0.01")),
        hazardous_surcharge_eur=hazardous_surcharge,
        oversize_surcharge_eur=oversize_surcharge,
        docs_fee_eur=DOCS_FEE_EUR,
        total_eur=total,
        applied_multipliers=applied,
    )


def _apply_dynamic(
    base: Decimal, *, etd: datetime, capacity: CapacityInfo
) -> Decimal:
    days_to_etd = (etd - datetime.now(timezone.utc)).days
    occupancy = capacity.occupancy_pct

    if days_to_etd > 30 and occupancy < 50:
        return (base * Decimal("0.9")).quantize(Decimal("0.01"))
    if 0 < days_to_etd < 7 and occupancy > 85:
        return (base * Decimal("1.3")).quantize(Decimal("0.01"))
    return base
