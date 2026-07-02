"""Intégration — P1 « la preuve thermique » : conditions de cale exposées.

Couvre :
- l'agrégation ``services.hold_conditions.for_leg`` (relevés CFOTE_05) ;
- la restitution client (``/me/bookings/{ref}`` + ``carnet.pdf``) ;
- la restitution expéditeur (``/p/{token}/voyage``).

Appelle directement les coroutines de route (session aiosqlite in-memory) :
le rendu Jinja complet est exercé et toute erreur de template lève.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.booking import Booking
from app.models.client_account import ClientAccount
from app.models.leg import Leg
from app.models.noon_report import NoonReport, NoonReportHold
from app.models.port import Port
from app.models.vessel import Vessel
from app.services import hold_conditions


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/me/bookings/X")
    state = SimpleNamespace(lang="fr")


async def _leg(db) -> Leg:
    vessel = Vessel(code="ANEM", name="Anemos")
    pol = Port(locode="BRSSO", name="São Sebastião", country="BR", latitude=-23.8, longitude=-45.4)
    pod = Port(locode="FRFEC", name="Fécamp", country="FR", latitude=49.76, longitude=0.37)
    db.add_all([vessel, pol, pod])
    await db.flush()
    base = datetime(2026, 5, 1, tzinfo=UTC)
    leg = Leg(
        leg_code="1ABRFR6",
        vessel_id=vessel.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd_ref=base,
        eta_ref=base + timedelta(days=18),
        etd=base,
        eta=base + timedelta(days=18),
        atd=base + timedelta(hours=3),
    )
    db.add(leg)
    await db.flush()
    return leg


async def _seed_holds(db, leg: Leg, *, days: int = 3) -> None:
    base = leg.atd or leg.etd
    for day in range(days):
        report = NoonReport(
            leg_id=leg.id,
            recorded_at=base + timedelta(days=day + 1),
            latitude=-20.0 + day,
            longitude=-38.0 + day,
        )
        db.add(report)
        await db.flush()
        db.add(
            NoonReportHold(
                noon_report_id=report.id,
                location="Lower FWD hold",
                temp_midnight_c=18.0 + day,
                humidity_midnight_pct=60.0,
                temp_midday_c=19.0 + day,
                humidity_midday_pct=62.0,
            )
        )
        db.add(
            NoonReportHold(
                noon_report_id=report.id,
                location="Cellar",
                temp_midnight_c=16.0,
                humidity_midnight_pct=None,
                temp_midday_c=None,
                humidity_midday_pct=58.0,
            )
        )
    await db.flush()


async def _booking(db, leg: Leg, client: ClientAccount, *, status: str = "at_sea") -> Booking:
    booking = Booking(
        reference="BK-2026-0042",
        leg_id=leg.id,
        client_account_id=client.id,
        status=status,
    )
    db.add(booking)
    await db.flush()
    return booking


async def _client(db) -> ClientAccount:
    acc = ClientAccount(email="cafe@example.test", hashed_password="x", company_name="Torréf SA")
    db.add(acc)
    await db.flush()
    return acc


# ───────────────────── service hold_conditions ─────────────────────


@pytest.mark.asyncio
async def test_for_leg_aggregates_min_max_avg_per_hold(db):
    leg = await _leg(db)
    await _seed_holds(db, leg, days=3)

    cond = await hold_conditions.for_leg(db, leg.id)
    assert cond is not None
    # 3 rapports × 2 cales = 6 relevés exploitables
    assert cond.readings == 6
    # Global : temps = {18,19,19,20,20,21} (FWD) + {16,16,16} (Cellar)
    assert cond.temp_min == 16.0
    assert cond.temp_max == 21.0
    assert cond.humidity_min == 58.0
    assert cond.humidity_max == 62.0
    # Ordre des cales = ordre du formulaire officiel (Cellar avant Lower FWD)
    assert [h.location for h in cond.holds] == ["Cellar", "Lower FWD hold"]
    cellar = cond.holds[0]
    assert cellar.temp_min == cellar.temp_max == 16.0
    assert cellar.humidity_min == cellar.humidity_max == 58.0
    fwd = cond.holds[1]
    assert fwd.temp_min == 18.0
    assert fwd.temp_max == 21.0
    # Séries journalières : 1 point par noon report, polyligne générée
    assert len(cond.series) == 3
    assert cond.temp_points != ""
    assert cond.humidity_points != ""


@pytest.mark.asyncio
async def test_for_leg_returns_none_without_reports_or_readings(db):
    leg = await _leg(db)
    assert await hold_conditions.for_leg(db, leg.id) is None
    # Rapport sans relevés de cale exploitables → toujours None
    report = NoonReport(
        leg_id=leg.id,
        recorded_at=(leg.atd or leg.etd) + timedelta(days=1),
        latitude=0.0,
        longitude=0.0,
    )
    db.add(report)
    await db.flush()
    db.add(NoonReportHold(noon_report_id=report.id, location="Cellar"))
    await db.flush()
    assert await hold_conditions.for_leg(db, leg.id) is None


# ───────────────────── espace client /me ─────────────────────


@pytest.mark.asyncio
async def test_booking_detail_exposes_conditions_and_carnet(db):
    from app.routers.client_dashboard_router import booking_detail

    client = await _client(db)
    leg = await _leg(db)
    await _seed_holds(db, leg)
    booking = await _booking(db, leg, client, status="at_sea")

    resp = await booking_detail(_Req(), booking.reference, client=client, db=db)
    assert resp.status_code == 200
    assert resp.template.name == "client/booking_detail.html"
    assert resp.context["conditions"] is not None
    assert resp.context["conditions"].temp_max == 21.0
    assert resp.context["carnet_available"] is True
    assert resp.context["voyage_url"].endswith(f"/voyage/{booking.reference}")


@pytest.mark.asyncio
async def test_booking_detail_hides_conditions_before_departure(db):
    from app.routers.client_dashboard_router import booking_detail

    client = await _client(db)
    leg = await _leg(db)
    await _seed_holds(db, leg)
    booking = await _booking(db, leg, client, status="confirmed")

    resp = await booking_detail(_Req(), booking.reference, client=client, db=db)
    assert resp.status_code == 200
    assert resp.context["conditions"] is None
    assert resp.context["carnet_available"] is False


@pytest.mark.asyncio
async def test_booking_carnet_pdf_guards_and_happy_path(db, monkeypatch):
    from fastapi import HTTPException

    from app.routers.client_dashboard_router import booking_carnet_pdf

    client = await _client(db)
    leg = await _leg(db)
    booking = await _booking(db, leg, client, status="at_sea")

    # Client étranger → 404 (pas de fuite d'existence)
    intruder = ClientAccount(email="other@example.test", hashed_password="x", company_name="X")
    db.add(intruder)
    await db.flush()
    with pytest.raises(HTTPException):
        await booking_carnet_pdf(booking.reference, client=intruder, db=db)

    # Avant le départ → 404
    booking.status = "confirmed"
    await db.flush()
    with pytest.raises(HTTPException):
        await booking_carnet_pdf(booking.reference, client=client, db=db)

    # Voyage commencé → PDF servi (génération WeasyPrint monkeypatchée)
    booking.status = "at_sea"
    await db.flush()

    async def _fake_pdf(db_, leg_id, client_account_id=None):
        assert leg_id == leg.id
        assert client_account_id == client.id
        return b"%PDF-1.4 fake"

    monkeypatch.setattr("app.services.carnet_bord.generate_carnet_bord_pdf", _fake_pdf)
    resp = await booking_carnet_pdf(booking.reference, client=client, db=db)
    assert resp.status_code == 200
    assert resp.media_type == "application/pdf"
    assert resp.body == b"%PDF-1.4 fake"


@pytest.mark.asyncio
async def test_carnet_pdf_real_render(db):
    """Rendu réel WeasyPrint de bout en bout — exerce les 13 templates du
    carnet (couverture, chapitres, conclusion) et le contexte complet."""
    from app.services.carnet_bord import generate_carnet_bord_pdf

    leg = await _leg(db)
    await _seed_holds(db, leg)
    pdf = await generate_carnet_bord_pdf(db, leg.id)
    assert pdf.startswith(b"%PDF")


# ───────────────────── portail expéditeur /p/{token} ─────────────────────


@pytest.mark.asyncio
async def test_portal_voyage_exposes_conditions(db):
    from app.models.packing_list import PackingList
    from app.routers.cargo_portal_router import portal_voyage

    client = await _client(db)
    leg = await _leg(db)
    await _seed_holds(db, leg)
    booking = await _booking(db, leg, client)
    pl = PackingList(booking_id=booking.id, status="draft")
    db.add(pl)
    await db.flush()

    req = _Req()
    req.url = SimpleNamespace(path=f"/p/{pl.token}/voyage")
    resp = await portal_voyage(pl.token, req, db=db)
    assert resp.status_code == 200
    assert resp.template.name == "portal/voyage.html"
    assert resp.context["conditions"] is not None
    assert resp.context["conditions"].readings == 6
