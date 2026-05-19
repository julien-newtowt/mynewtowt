"""Tests for app.services.stowage — auto-assignment algorithm."""
from __future__ import annotations

from app.models.stowage import (
    DANGEROUS_ZONES, ZONE_LOADING_ORDER,
)
from app.services.stowage import suggest_assignments, zone_usage_summary


def test_zone_order_starts_with_inf_ar_ar():
    assert ZONE_LOADING_ORDER[0] == "INF_AR_AR"
    assert "SUP_AV_AV" in ZONE_LOADING_ORDER
    assert len(ZONE_LOADING_ORDER) == 18


def test_dangerous_items_go_to_sup_av():
    items = [
        {"batch_id": 1, "pallet_format": "EPAL", "pallet_count": 5,
         "is_dangerous": True, "is_oversized": False},
    ]
    result = suggest_assignments(items)
    assert result[0]["zone"] in DANGEROUS_ZONES


def test_oversized_items_go_to_sup_av():
    items = [
        {"batch_id": 1, "pallet_format": "EPAL", "pallet_count": 1,
         "is_dangerous": False, "is_oversized": True},
    ]
    result = suggest_assignments(items)
    assert result[0]["zone"] in DANGEROUS_ZONES


def test_normal_items_avoid_sup_av_when_possible():
    items = [
        {"batch_id": i, "pallet_format": "EPAL", "pallet_count": 10,
         "is_dangerous": False, "is_oversized": False}
        for i in range(5)
    ]
    result = suggest_assignments(items)
    for r in result:
        assert r["zone"] not in DANGEROUS_ZONES


def test_normal_items_fill_aft_first():
    items = [
        {"batch_id": 1, "pallet_format": "EPAL", "pallet_count": 1,
         "is_dangerous": False, "is_oversized": False},
    ]
    result = suggest_assignments(items)
    # First non-dangerous zone in load order is INF_AR_AR
    assert result[0]["zone"] == "INF_AR_AR"


def test_zone_usage_summary_aggregates_by_zone():
    placed = [
        {"zone": "INF_AR_AR", "pallet_format": "EPAL", "pallet_count": 10},
        {"zone": "INF_AR_AR", "pallet_format": "EPAL", "pallet_count": 5},
        {"zone": "INF_AR_MIL", "pallet_format": "USPAL", "pallet_count": 4},
    ]
    s = zone_usage_summary(placed)
    assert s["INF_AR_AR"] == 15
    # USPAL = 1.2 coefficient
    assert abs(s["INF_AR_MIL"] - 4.8) < 0.001
