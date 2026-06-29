"""MRV-08 — vue détail d'un leg : table d'événements, badges qualité, agrégats.

Vérifie que `/mrv/legs/{leg_id}` agrège la consommation, le bunkering, la
distance et le cargo des événements du leg, compte les statuts qualité, et que
le gabarit expose ces éléments (bunkering / cargo / badges qualité).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.integration.conftest import FakeRequest
from tests.integration.test_mrv_reprise import _ev, _setup_leg


def test_leg_detail_template_has_aggregates_and_quality():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/mrv/leg_detail.html")[0]
    assert "Bunkering cumulé" in src
    assert "Cargo" in src
    for cls in ("pill-ok", "pill-warn", "pill-error"):
        assert cls in src


def test_leg_detail_route_registered():
    from app.routers import mrv_router

    paths = {r.path for r in mrv_router.router.routes}
    assert "/mrv/legs/{leg_id}" in paths


@pytest.mark.asyncio
async def test_leg_detail_aggregates(db, staff_user):
    from app.routers.mrv_router import mrv_leg_detail

    await _setup_leg(db)
    t = datetime(2026, 4, 2, 12, tzinfo=UTC)
    db.add(_ev(1, t, total_consumption_t=Decimal("1.500"), distance_nm=Decimal("100"), cargo_carried_t=Decimal("900")))
    db.add(_ev(1, t, kind="bunkering", bunkering_qty_t=Decimal("5.000")))
    db.add(
        _ev(
            1,
            t,
            total_consumption_t=Decimal("2.000"),
            distance_nm=Decimal("50"),
            quality_status="error",
            quality_notes="compteur incohérent",
        )
    )
    await db.flush()

    resp = await mrv_leg_detail(1, FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 200
    assert resp.template.name == "staff/mrv/leg_detail.html"

    totals = resp.context["totals"]
    assert totals["consumption_t"] == Decimal("3.500")
    assert totals["bunkering_t"] == Decimal("5.000")
    assert totals["distance_nm"] == Decimal("150")
    assert totals["cargo_t"] == Decimal("900")

    quality = resp.context["quality"]
    assert quality["error"] == 1
    assert quality["ok"] == 2  # bunkering + 1er event (quality_status None)
    assert len(resp.context["events"]) == 3
