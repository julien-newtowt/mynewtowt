"""STO-10 — vue SVG top-down du plan d'arrimage.

`deck_layout(db, leg_id)` produit la grille d'occupation par pont (3 ponts ×
6 zones) consommée par le partial SVG `_deck_svg.html`.
"""

from __future__ import annotations

import pytest

from app.models.stowage import BLOCKS, DANGEROUS_ZONES, DECKS, HOLDS, StowageItem, StowagePlan
from tests.integration.conftest import _setup_leg


@pytest.mark.asyncio
async def test_deck_layout_structure_empty(db):
    """Sans plan : 3 ponts × 6 zones, toutes à 0, DG marquées."""
    from app.services.stowage import deck_layout

    leg = await _setup_leg(db)
    layout = await deck_layout(db, leg.id)

    assert set(layout) == set(DECKS)
    for deck in DECKS:
        zones = layout[deck]
        assert len(zones) == len(HOLDS) * len(BLOCKS) == 6
        for z in zones:
            assert z["pallet_count"] == 0
            assert z["fill_ratio"] == 0.0
            assert z["deck"] == deck
            assert z["zone"] == f"{deck}_{z['hold']}_{z['block']}"
    # les zones DG du référentiel sont marquées même à vide.
    flat = {z["zone"]: z for zs in layout.values() for z in zs}
    for dz in DANGEROUS_ZONES:
        assert flat[dz]["is_dangerous"] is True


@pytest.mark.asyncio
async def test_deck_layout_merges_occupation(db):
    """Avec des palettes : fill_ratio = pallets / capacité, plafonné à 1."""
    from app.services.stowage import deck_layout

    leg = await _setup_leg(db)
    plan = StowagePlan(leg_id=leg.id, status="draft")
    db.add(plan)
    await db.flush()
    # zone non dangereuse, on charge quelques palettes.
    target = "INF_AR_MIL"
    db.add(StowageItem(plan_id=plan.id, zone=target, pallet_count=3, pallet_format="EPAL"))
    await db.flush()

    layout = await deck_layout(db, leg.id)
    flat = {z["zone"]: z for zs in layout.values() for z in zs}
    cell = flat[target]
    assert cell["pallet_count"] == 3
    assert cell["capacity_epal"] >= 1
    assert 0.0 < cell["fill_ratio"] <= 1.0


def test_deck_svg_partial_renders():
    from app.templating import templates

    tpl = templates.env.get_template("staff/stowage/_deck_svg.html")
    zones = [
        {
            "zone": f"INF_{h}_{b}",
            "deck": "INF",
            "hold": h,
            "block": b,
            "pallet_count": 0,
            "capacity_epal": 50,
            "fill_ratio": 0.0,
            "is_dangerous": False,
        }
        for h in HOLDS
        for b in BLOCKS
    ]
    html = tpl.render(deck="INF", zones=zones)
    assert "<svg" in html
    assert html.count("<rect") == 6  # 6 zones par pont


def test_plan_template_includes_svg_view():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/stowage/plan.html")[0]
    assert "decks_layout" in src
    assert "_deck_svg.html" in src
