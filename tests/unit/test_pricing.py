"""Pricing service — pure function tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.capacity import CapacityInfo
from app.services.pricing import (
    DOCS_FEE_EUR,
    HAZARDOUS_MULTIPLIER,
    OVERSIZE_MULTIPLIER,
    compute_quote,
)


def _capacity(used: int = 0, total: int = 850) -> CapacityInfo:
    return CapacityInfo(
        leg_id=1,
        capacity_palettes=total,
        reserved_palettes=used,
        available_palettes=total - used,
    )


def test_simple_epal_quote() -> None:
    q = compute_quote(
        base_price_per_palette_eur=Decimal("38"),
        items=[("EPAL", 10)],
        hazardous=False,
        oversize=False,
        etd=datetime.now(timezone.utc) + timedelta(days=14),
        capacity=_capacity(used=300),
    )
    assert q.lines[0].subtotal_eur == Decimal("380.00")
    # 10 EPAL × 38 + 50 docs fee
    assert q.total_eur == Decimal("430.00")
    assert q.applied_multipliers == []


def test_hazardous_surcharge() -> None:
    q = compute_quote(
        base_price_per_palette_eur=Decimal("38"),
        items=[("EPAL", 10)],
        hazardous=True,
        oversize=False,
        etd=datetime.now(timezone.utc) + timedelta(days=14),
        capacity=_capacity(used=300),
    )
    # subtotal 380, hazardous +25% = 95 surcharge
    assert q.hazardous_surcharge_eur == Decimal("95.00")
    assert "hazardous" in q.applied_multipliers
    assert q.total_eur == Decimal("525.00")


def test_early_bird_discount() -> None:
    """Long lead time + low occupancy → -10% on base price."""
    q = compute_quote(
        base_price_per_palette_eur=Decimal("40"),
        items=[("EPAL", 10)],
        hazardous=False,
        oversize=False,
        etd=datetime.now(timezone.utc) + timedelta(days=40),
        capacity=_capacity(used=100),  # ~12% occupancy
    )
    assert q.base_price_per_palette_eur == Decimal("36.00")
    assert q.lines[0].unit_price_eur == Decimal("36.00")


def test_last_seat_surcharge() -> None:
    """Short lead time + high occupancy → +30% on base price."""
    q = compute_quote(
        base_price_per_palette_eur=Decimal("40"),
        items=[("EPAL", 10)],
        hazardous=False,
        oversize=False,
        etd=datetime.now(timezone.utc) + timedelta(days=3),
        capacity=_capacity(used=800),  # ~94% occupancy
    )
    assert q.base_price_per_palette_eur == Decimal("52.00")


def test_key_account_skips_dynamic_pricing() -> None:
    """Key accounts pay the contracted rate, no dynamic adjustment."""
    q = compute_quote(
        base_price_per_palette_eur=Decimal("40"),
        items=[("EPAL", 10)],
        hazardous=False,
        oversize=False,
        etd=datetime.now(timezone.utc) + timedelta(days=3),
        capacity=_capacity(used=800),
        client_segment="key_account",
    )
    assert q.base_price_per_palette_eur == Decimal("40")


def test_oversize_multiplier() -> None:
    q = compute_quote(
        base_price_per_palette_eur=Decimal("38"),
        items=[("BARRIQUE140", 5)],
        hazardous=False,
        oversize=True,
        etd=datetime.now(timezone.utc) + timedelta(days=14),
        capacity=_capacity(used=200),
    )
    # 5 × (38 × 2.0) = 380 subtotal, +40% surcharge = +152
    assert q.lines[0].unit_price_eur == Decimal("76.00")
    assert q.oversize_surcharge_eur == Decimal("152.00")
    assert q.total_eur == Decimal("582.00")
