"""TRK-02 — vue KPI navigation agrégée par année (tous legs à GPS)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.claim import VesselPosition
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel


async def _setup(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="SNDKR", name="Dakar", country="SN", latitude=14.0, longitude=-17.0))
    await db.flush()
    base = datetime(2026, 3, 1, tzinfo=UTC)
    db.add(
        Leg(
            id=1,
            leg_code="1ANE6",
            vessel_id=1,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=base,
            eta_ref=base + timedelta(days=3),
            etd=base,
            eta=base + timedelta(days=3),
            atd=base,
            ata=base + timedelta(days=3),
            distance_nm=Decimal("100"),
        )
    )
    db.add_all(
        [
            VesselPosition(
                vessel_id=1,
                recorded_at=base + timedelta(hours=6),
                latitude=49.0,
                longitude=0.0,
                sog_kn=6.0,
                source="test",
            ),
            VesselPosition(
                vessel_id=1,
                recorded_at=base + timedelta(hours=18),
                latitude=48.0,
                longitude=-2.0,
                sog_kn=10.0,
                source="test",
            ),
        ]
    )
    await db.flush()


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/performance/navigation/kpis")
    state = SimpleNamespace(notif_count=0, newtowt_agent_enabled=True, recent_notifications=[])


@pytest.mark.asyncio
async def test_navigation_kpis_route_renders(db, staff_user):
    from app.routers.navigation_router import navigation_kpis

    await _setup(db)
    resp = await navigation_kpis(_Req(), vessel=None, year=2026, db=db, user=staff_user)
    assert resp.status_code == 200
    rows = resp.context["rows"]
    assert len(rows) == 1
    r = rows[0]
    assert r["leg"].leg_code == "1ANE6"
    assert r["avg_sog_kn"] == 8.0 and r["max_sog_kn"] == 10.0
    totals = resp.context["totals"]
    assert totals["leg_count"] == 1 and totals["points"] == 2
    assert totals["real_elongation"] is not None


@pytest.mark.asyncio
async def test_navigation_kpis_unknown_vessel_falls_back_to_fleet(db, staff_user):
    from app.routers.navigation_router import navigation_kpis

    await _setup(db)
    # Code navire inconnu → agrégat flotte + onglet « Tous » actif (selected_vessel "").
    resp = await navigation_kpis(_Req(), vessel="ZZZ", year=2026, db=db, user=staff_user)
    assert resp.status_code == 200
    assert resp.context["selected_vessel"] == ""
    assert len(resp.context["rows"]) == 1  # le leg de flotte reste agrégé


@pytest.mark.asyncio
async def test_navigation_kpis_empty_year(db, staff_user):
    from app.routers.navigation_router import navigation_kpis

    await _setup(db)
    # Année sans aucun leg à GPS → tableau vide mais rendu OK.
    resp = await navigation_kpis(_Req(), vessel=None, year=2024, db=db, user=staff_user)
    assert resp.status_code == 200
    assert resp.context["rows"] == []
    assert resp.context["totals"]["leg_count"] == 0
