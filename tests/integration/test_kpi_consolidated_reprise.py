"""FIN-07 — vue KPI consolidée (agrégation Commerce/Flotte/Env/Exploitation)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.commercial import Client, Order
from app.models.finance import LegKPI
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel


async def _setup(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos", capacity_palettes=850))
    db.add(Port(id=1, locode="FRLEH", name="Le Havre", country="FR"))
    db.add(Port(id=2, locode="MQFDF", name="Fort-de-France", country="MQ"))
    await db.flush()
    base = datetime(2026, 3, 1, tzinfo=UTC)
    db.add(
        Leg(
            id=1,
            leg_code="1AFRMQ6",
            vessel_id=1,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=base,
            eta_ref=base + timedelta(days=15),
            etd=base,
            eta=base + timedelta(days=15),
            atd=base,
            ata=base + timedelta(days=15),
            distance_nm=Decimal("4000"),
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
            booked_palettes=100,
            total_eur=Decimal("9600.00"),
        )
    )
    db.add(
        LegKPI(
            leg_id=1,
            palettes_carried=100,
            tonnage_kg=Decimal("50000"),
            distance_nm=Decimal("4000"),
            on_time=True,
            co2_avoided_kg=Decimal("120000"),
            co2_emitted_kg=Decimal("8000"),
            do_consumed_t=Decimal("2.5"),
        )
    )
    await db.flush()
    return base


@pytest.mark.asyncio
async def test_consolidated_kpis_aggregates_sources(db):
    from app.services.kpi_consolidated import consolidated_kpis

    await _setup(db)
    data = await consolidated_kpis(db, year=2026)

    assert data["year"] == 2026
    assert data["leg_count"] == 1 and data["kpi_count"] == 1
    # Commerce — CA réalisé du carnet confirmé.
    assert data["commerce"]["ca_total_eur"] == Decimal("9600.00")
    # Environnement — agrégats depuis le LegKPI.
    assert data["env"]["co2_avoided_kg"] == Decimal("120000")
    assert data["env"]["co2_emitted_t"] == Decimal("8")
    assert data["env"]["nox_avoided_kg"] > 0  # cargo × distance × Δfacteur
    assert data["env"]["equiv"]["flights_paris_nyc"] > 0
    # Exploitation — leg réalisé + ponctualité.
    assert data["exploitation"]["completed"] == 1
    assert data["on_time_pct"] == 100.0
    # Assurance — exposition sinistres présente (FIN-06), même vide.
    assert "insurance" in data
    assert data["insurance"]["claim_count"] == 0
    assert data["insurance"]["net_company_total"] == Decimal(0)


@pytest.mark.asyncio
async def test_consolidated_year_scopes_realized_sections_only(db):
    """Env/Exploitation sont bornés à l'année ; Commerce reste global (par design)."""
    from app.services.kpi_consolidated import consolidated_kpis

    await _setup(db)
    data = await consolidated_kpis(db, year=2025)
    # Sections « réalisé » : vides pour 2025 (le leg est en 2026).
    assert data["leg_count"] == 0 and data["kpi_count"] == 0
    assert data["env"]["co2_avoided_kg"] == 0
    assert data["on_time_pct"] == 0.0
    # Commerce = carnet global → inchangé quelle que soit l'année sélectionnée.
    assert data["commerce"]["ca_total_eur"] == Decimal("9600.00")


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/kpi/consolidated")
    state = SimpleNamespace(notif_count=0, newtowt_agent_enabled=True, recent_notifications=[])


@pytest.mark.asyncio
async def test_consolidated_route_renders(db, staff_user):
    from app.routers.kpi_router import kpi_consolidated

    await _setup(db)
    resp = await kpi_consolidated(_Req(), year=2026, db=db, user=staff_user)
    assert resp.status_code == 200
    assert resp.context["data"]["commerce"]["ca_total_eur"] == Decimal("9600.00")
    assert resp.context["current_year"] == 2026
