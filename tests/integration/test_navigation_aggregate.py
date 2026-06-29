"""EVO-08 — agrégat des métriques de navigation dans les KPI d'exploitation."""

from __future__ import annotations

import pytest

from tests.integration.test_mrv_reprise import _setup_leg


@pytest.mark.asyncio
async def test_navigation_aggregate_empty():
    from app.services.voyage_track import navigation_aggregate

    # Aucun leg → agrégat neutre (la carte est masquée côté template).
    agg = await navigation_aggregate(None, [])
    assert agg["legs_with_gps"] == 0
    assert agg["total_real_nm"] == 0.0
    assert agg["avg_elongation"] is None
    assert agg["avg_sog_kn"] is None


@pytest.mark.asyncio
async def test_navigation_aggregate_leg_without_gps(db):
    from app.models.leg import Leg
    from app.services.voyage_track import navigation_aggregate

    await _setup_leg(db)  # leg id=1, sans position GPS
    legs = [await db.get(Leg, 1)]
    agg = await navigation_aggregate(db, legs)
    assert agg["legs_with_gps"] == 0  # exclu de l'agrégat


def test_kpi_template_has_nav_aggregate():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/kpi/index.html")[0]
    assert "nav_aggregate" in src
    assert "Distance réelle cumulée" in src
    assert "Allongement moyen" in src
