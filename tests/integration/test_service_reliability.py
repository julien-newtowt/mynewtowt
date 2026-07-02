"""Intégration — P10 « publier la fiabilité » (taux de service / ponctualité).

Couvre :
- le service ``service_reliability`` (fenêtre 24 h, échantillon minimal,
  plancher d'alerte, calcul global et par route) ;
- la landing (section « Nos départs tenus » conditionnelle à l'échantillon) ;
- la fiche route (ponctualité de la ligne) ;
- l'alerte interne exec quand le taux passe sous 90 %.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services import service_reliability


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/")
    state = SimpleNamespace(lang="fr")


async def _ports_vessel(db):
    vessel = Vessel(code="ANEM", name="Anemos")
    pol = Port(locode="BRSSO", name="São Sebastião", country="BR", latitude=-23.8, longitude=-45.4)
    pod = Port(locode="FRFEC", name="Fécamp", country="FR", latitude=49.76, longitude=0.37)
    db.add_all([vessel, pol, pod])
    await db.flush()
    return vessel, pol, pod


async def _leg(db, vessel, pol, pod, *, seq, delay_hours=None, arrived=True):
    """Crée un leg ; ``delay_hours`` = écart ATA − ETA_ref (None = non arrivé)."""
    base = datetime(2026, 3, 1, tzinfo=UTC) + timedelta(days=seq * 20)
    eta_ref = base + timedelta(days=18)
    leg = Leg(
        leg_code=f"{seq}ABRFR6",
        vessel_id=vessel.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd_ref=base,
        eta_ref=eta_ref,
        etd=base,
        eta=eta_ref,
        atd=base + timedelta(hours=2),
        ata=(eta_ref + timedelta(hours=delay_hours)) if arrived else None,
    )
    db.add(leg)
    await db.flush()
    return leg


# ───────────────────── service pur ─────────────────────


@pytest.mark.asyncio
async def test_overall_counts_on_time_within_window(db):
    service_reliability.invalidate_cache()
    vessel, pol, pod = await _ports_vessel(db)
    # 8 traversées arrivées : 6 dans les 24 h (dont une en avance), 2 hors.
    on_time_deltas = [0, 5, -3, 12, -18, 23]  # |Δ| < 24 h
    late_deltas = [30, -48]  # hors fenêtre
    for i, d in enumerate(on_time_deltas + late_deltas):
        await _leg(db, vessel, pol, pod, seq=i + 1, delay_hours=d)
    # + 1 traversée non arrivée (ignorée)
    await _leg(db, vessel, pol, pod, seq=99, arrived=False)

    stats = await service_reliability.overall(db)
    assert stats.completed == 8
    assert stats.on_time == 6
    assert stats.pct == 75.0
    assert stats.is_publishable is True  # ≥ 5
    assert stats.is_below_floor is True  # 75 % < 90 %


@pytest.mark.asyncio
async def test_small_sample_not_publishable(db):
    service_reliability.invalidate_cache()
    vessel, pol, pod = await _ports_vessel(db)
    for i in range(3):  # < MIN_PUBLIC_SAMPLE
        await _leg(db, vessel, pol, pod, seq=i + 1, delay_hours=0)
    stats = await service_reliability.overall(db)
    assert stats.completed == 3
    assert stats.pct == 100.0
    assert stats.is_publishable is False  # trop peu pour publier
    assert stats.is_below_floor is False  # ni alerte sur échantillon insuffisant


@pytest.mark.asyncio
async def test_empty_db_returns_none_pct(db):
    service_reliability.invalidate_cache()
    stats = await service_reliability.overall(db)
    assert stats.completed == 0
    assert stats.pct is None
    assert stats.is_publishable is False
    assert stats.is_below_floor is False


@pytest.mark.asyncio
async def test_for_route_filters_by_od_pair(db):
    service_reliability.invalidate_cache()
    vessel, pol, pod = await _ports_vessel(db)
    other = Port(locode="USNYC", name="New York", country="US")
    db.add(other)
    await db.flush()
    # 5 sur la ligne pol→pod (toutes à l'heure), 1 sur pol→other (en retard)
    for i in range(5):
        await _leg(db, vessel, pol, pod, seq=i + 1, delay_hours=1)
    await _leg(db, vessel, pol, other, seq=50, delay_hours=100)
    route = await service_reliability.for_route(db, pol.id, pod.id)
    assert route.completed == 5
    assert route.pct == 100.0
    assert route.is_publishable is True


# ───────────────────── landing ─────────────────────


@pytest.mark.asyncio
async def test_landing_shows_reliability_when_publishable(db):
    from app.routers.public_router import landing

    service_reliability.invalidate_cache()
    from app.services import social_proof

    social_proof.invalidate_counters_cache()
    vessel, pol, pod = await _ports_vessel(db)
    for i in range(6):
        await _leg(db, vessel, pol, pod, seq=i + 1, delay_hours=0)

    resp = await landing(_Req(), db=db)
    body = resp.body.decode()
    assert "Nos départs tenus" in body
    assert "100" in body
    service_reliability.invalidate_cache()


@pytest.mark.asyncio
async def test_landing_hides_reliability_on_small_sample(db):
    from app.routers.public_router import landing

    service_reliability.invalidate_cache()
    from app.services import social_proof

    social_proof.invalidate_counters_cache()
    vessel, pol, pod = await _ports_vessel(db)
    await _leg(db, vessel, pol, pod, seq=1, delay_hours=0)

    resp = await landing(_Req(), db=db)
    assert "Nos départs tenus" not in resp.body.decode()
    service_reliability.invalidate_cache()


# ───────────────────── alerte interne exec ─────────────────────


@pytest.mark.asyncio
async def test_dashboard_alert_when_below_floor(db):
    from app.services.dashboard_alerts import compute_alerts

    service_reliability.invalidate_cache()
    vessel, pol, pod = await _ports_vessel(db)
    # 5 arrivées, 2 à l'heure → 40 % < 90 %, échantillon suffisant.
    for i, d in enumerate([0, 1, 40, 50, 60]):
        await _leg(db, vessel, pol, pod, seq=i + 1, delay_hours=d)
    alerts = await compute_alerts(db, 2026)
    service_alerts = [a for a in alerts if a["family"] == "service"]
    assert len(service_alerts) == 1
    assert service_alerts[0]["severity"] == "danger"
    assert "40 %" in service_alerts[0]["title"]


@pytest.mark.asyncio
async def test_dashboard_no_alert_when_above_floor(db):
    from app.services.dashboard_alerts import compute_alerts

    service_reliability.invalidate_cache()
    vessel, pol, pod = await _ports_vessel(db)
    for i in range(6):  # tous à l'heure → 100 %
        await _leg(db, vessel, pol, pod, seq=i + 1, delay_hours=0)
    alerts = await compute_alerts(db, 2026)
    assert not [a for a in alerts if a["family"] == "service"]
    service_reliability.invalidate_cache()
