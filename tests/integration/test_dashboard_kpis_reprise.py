"""ADM-03 — reprise des KPI métier du dashboard staff."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.commercial import Client, Order
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel


async def _setup(db, *, reserved=100, distance=5000):
    db.add(Vessel(id=1, code="ANE", name="Anemos", capacity_palettes=850))
    db.add(Port(id=1, locode="FRLEH", name="Le Havre", country="FR"))
    db.add(Port(id=2, locode="MQFDF", name="Fort-de-France", country="MQ"))
    await db.flush()
    now = datetime.now(UTC)
    db.add(
        Leg(
            id=1,
            leg_code="1AFRMQ6",
            vessel_id=1,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=now + timedelta(days=30),
            eta_ref=now + timedelta(days=45),
            etd=now + timedelta(days=30),
            eta=now + timedelta(days=45),
            is_bookable=True,
            distance_nm=distance,
        )
    )
    c = Client(name="ACME", client_type="shipper")
    db.add(c)
    await db.flush()
    db.add(
        Order(
            reference="ORD-2026-0001",
            client_id=c.id,
            leg_id=1,
            status="confirmed",
            booked_palettes=reserved,
            total_eur=Decimal("9600.00"),
        )
    )
    await db.flush()
    return now


@pytest.mark.asyncio
async def test_ca_previsionnel(db):
    from app.services.dashboard_kpis import ca_previsionnel

    await _setup(db)
    assert await ca_previsionnel(db) == Decimal("9600.00")


@pytest.mark.asyncio
async def test_ca_previsionnel_ignores_draft(db):
    from app.services.dashboard_kpis import ca_previsionnel

    c = Client(name="X", client_type="shipper")
    db.add(c)
    await db.flush()
    db.add(Order(reference="D", client_id=c.id, status="draft", total_eur=Decimal("500")))
    await db.flush()
    assert await ca_previsionnel(db) == Decimal("0")


@pytest.mark.asyncio
async def test_fleet_kpis(db):
    from app.services.dashboard_kpis import fleet_kpis

    now = await _setup(db, reserved=100)
    k = await fleet_kpis(db, now)
    assert k["reserved"] == 100
    assert k["capacity"] == 850
    assert k["occupancy_pct"] == round(100 * 100 / 850, 1)
    assert k["co2_avoided_kg"] > 0  # distance + tonnage réservée → CO₂ évité


@pytest.mark.asyncio
async def test_fleet_kpis_skips_unbookable(db):
    """Un leg non réservable n'est pas compté (occupation 0)."""
    from app.services.dashboard_kpis import fleet_kpis

    db.add(Vessel(id=1, code="ANE", name="Anemos", capacity_palettes=850))
    db.add(Port(id=1, locode="FRLEH", name="Le Havre", country="FR"))
    db.add(Port(id=2, locode="MQFDF", name="Fort-de-France", country="MQ"))
    await db.flush()
    now = datetime.now(UTC)
    db.add(
        Leg(
            id=1,
            leg_code="1X",
            vessel_id=1,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=now + timedelta(days=10),
            eta_ref=now + timedelta(days=20),
            etd=now + timedelta(days=10),
            eta=now + timedelta(days=20),
            is_bookable=False,
        )
    )
    await db.flush()
    k = await fleet_kpis(db, now)
    assert k["capacity"] == 0 and k["occupancy_pct"] == 0.0


@pytest.mark.asyncio
async def test_upcoming_departures(db):
    from app.services.dashboard_kpis import upcoming_departures

    now = await _setup(db)
    deps = await upcoming_departures(db, now, limit=8)
    assert len(deps) == 1
    assert deps[0]["leg_code"] == "1AFRMQ6"
    assert deps[0]["vessel"] == "Anemos"


@pytest.mark.asyncio
async def test_dashboard_route_renders_kpis(db, staff_user):
    from app.routers.staff_dashboard_router import dashboard

    await _setup(db)

    class _FullReq:
        headers: dict[str, str] = {}
        cookies: dict[str, str] = {}
        query_params: dict[str, str] = {}
        client = SimpleNamespace(host="127.0.0.1")
        url = SimpleNamespace(path="/dashboard")
        state = SimpleNamespace(notif_count=0, newtowt_agent_enabled=True, recent_notifications=[])

    resp = await dashboard(_FullReq(), user=staff_user, db=db)
    assert resp.status_code == 200
    assert resp.context["ca_forecast"] == Decimal("9600.00")
    assert resp.context["fleet_kpis"]["reserved"] == 100
    assert len(resp.context["upcoming_departures"]) == 1
