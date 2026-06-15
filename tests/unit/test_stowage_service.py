"""Tests for app.services.stowage — auto-assignment algorithm."""

from __future__ import annotations

from app.models.stowage import (
    DANGEROUS_ZONES,
    ZONE_LOADING_ORDER,
)
from app.services.stowage import parse_zone, suggest_assignments, zone_usage_summary
from app.services.stowage_specs import (
    PHOENIX_ZONE_CAPACITY,
    build_reference_specs,
    capacity_total,
)


def test_zone_order_starts_with_inf_ar_ar():
    assert ZONE_LOADING_ORDER[0] == "INF_AR_AR"
    assert "SUP_AV_AV" in ZONE_LOADING_ORDER
    assert len(ZONE_LOADING_ORDER) == 18


def test_dangerous_items_go_to_sup_av():
    items = [
        {
            "batch_id": 1,
            "pallet_format": "EPAL",
            "pallet_count": 5,
            "is_dangerous": True,
            "is_oversized": False,
        },
    ]
    result = suggest_assignments(items)
    assert result[0]["zone"] in DANGEROUS_ZONES


def test_oversized_items_go_to_sup_av():
    items = [
        {
            "batch_id": 1,
            "pallet_format": "EPAL",
            "pallet_count": 1,
            "is_dangerous": False,
            "is_oversized": True,
        },
    ]
    result = suggest_assignments(items)
    assert result[0]["zone"] in DANGEROUS_ZONES


def test_normal_items_avoid_sup_av_when_possible():
    items = [
        {
            "batch_id": i,
            "pallet_format": "EPAL",
            "pallet_count": 10,
            "is_dangerous": False,
            "is_oversized": False,
        }
        for i in range(5)
    ]
    result = suggest_assignments(items)
    for r in result:
        assert r["zone"] not in DANGEROUS_ZONES


def test_normal_items_fill_aft_first():
    items = [
        {
            "batch_id": 1,
            "pallet_format": "EPAL",
            "pallet_count": 1,
            "is_dangerous": False,
            "is_oversized": False,
        },
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


# ──────────────────────────────────── Référentiel Phoenix & specs


def test_reference_specs_cover_18_zones():
    specs = build_reference_specs("phoenix")
    assert len(specs) == 18
    assert set(specs) == set(ZONE_LOADING_ORDER)


def test_phoenix_total_capacity_is_978():
    # Étude Eupal du plan théorique : 978 palettes EPAL au total.
    assert sum(PHOENIX_ZONE_CAPACITY.values()) == 978
    assert capacity_total(build_reference_specs("phoenix")) == 978


def test_sup_deck_is_lighter_than_inf():
    specs = build_reference_specs("phoenix")
    # Pont supérieur : pas de palette 1,4 t, pas de gerbage lourd.
    assert specs["SUP_AR_AR"]["max_pallet_weight_kg"] == 1200.0
    assert specs["SUP_AR_AR"]["heavy_stack_allowed"] is False
    # Ponts intermédiaire & inférieur : 1,4 t + gerbage lourd admis.
    assert specs["MIL_AR_AR"]["max_pallet_weight_kg"] == 1400.0
    assert specs["INF_AR_AR"]["heavy_stack_allowed"] is True


def test_all_holds_segregated():
    specs = build_reference_specs("phoenix")
    assert all(s["segregated"] for s in specs.values())


def test_reference_specs_have_max_load():
    specs = build_reference_specs("phoenix")
    # Chaque zone Phoenix a un plafond de charge dérivé du plan.
    assert all(s["max_load_t"] and s["max_load_t"] > 0 for s in specs.values())


def test_parse_zone_valid_and_invalid():
    assert parse_zone("INF_AR_MIL") == ("INF", "AR", "MIL")
    assert parse_zone("OVERFLOW") == (None, None, None)
    assert parse_zone(None) == (None, None, None)
    assert parse_zone("BAD_X_Y") == (None, None, None)


def test_suggest_respects_per_zone_capacity():
    # INF_AR_AR plafonné à 5 EPAL : un lot de 6 ne tient pas et passe à la
    # zone suivante de l'ordre de chargement (pas de split d'un lot).
    capacities = dict.fromkeys(ZONE_LOADING_ORDER, 50)
    capacities["INF_AR_AR"] = 5
    items = [
        {
            "batch_id": 1,
            "pallet_format": "EPAL",
            "pallet_count": 6,
            "is_dangerous": False,
            "is_oversized": False,
        }
    ]
    result = suggest_assignments(items, capacities=capacities)
    assert result[0]["zone"] == "INF_AR_MIL"


def test_suggest_uses_first_zone_when_capacity_allows():
    capacities = dict.fromkeys(ZONE_LOADING_ORDER, 50)
    items = [
        {
            "batch_id": 1,
            "pallet_format": "EPAL",
            "pallet_count": 6,
            "is_dangerous": False,
            "is_oversized": False,
        }
    ]
    result = suggest_assignments(items, capacities=capacities)
    assert result[0]["zone"] == "INF_AR_AR"
