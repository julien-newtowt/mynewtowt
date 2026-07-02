"""Intégration — P2 « page publique de voyage » (destination du QR B2B2C).

Couvre :
- l'opt-in / opt-out client (``POST /me/bookings/{ref}/voyage-public``) ;
- la page publique ``/voyage/{ref}`` (publiée / non publiée / PII absente) ;
- le filtre vie privée des photos ;
- l'événement analytics ``voyage_page_view`` ;
- le basculement du QR du kit B2B2C vers la page de voyage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.analytics_event import ANALYTICS_EVENTS, AnalyticsEvent
from app.models.anemos_certificate import AnemosCertificate
from app.models.booking import Booking
from app.models.claim import VesselPosition
from app.models.client_account import ClientAccount
from app.models.leg import Leg
from app.models.noon_report import NoonReport, NoonReportHold
from app.models.port import Port
from app.models.vessel import Vessel
from app.models.voyage_photo import VoyagePhoto


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/voyage/BK-2026-0042")
    state = SimpleNamespace(lang="fr")


async def _fixture(db, *, published: bool = True, status: str = "at_sea"):
    account = ClientAccount(
        email="cafe@example.test",
        hashed_password="x",
        company_name="Torréf SA",
        brand_name="Café du Vent",
    )
    vessel = Vessel(code="ANEM", name="Anemos")
    pol = Port(locode="BRSSO", name="São Sebastião", country="BR", latitude=-23.8, longitude=-45.4)
    pod = Port(locode="FRFEC", name="Fécamp", country="FR", latitude=49.76, longitude=0.37)
    db.add_all([account, vessel, pol, pod])
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
    booking = Booking(
        reference="BK-2026-0042",
        leg_id=leg.id,
        client_account_id=account.id,
        status=status,
        voyage_public=published,
        coffee_origin="colombie",
        coffee_region="Huila",
        coffee_producer="Coop El Cóndor",
    )
    db.add(booking)
    db.add(
        AnemosCertificate(
            reference="ANEMOS-BK-2026-0042",
            booking_id=None,  # rattaché après flush (id requis)
            client_account_id=account.id,
            leg_id=leg.id,
            tonnage_transported_t=Decimal("12"),
            distance_nm=Decimal("5200"),
            co2_emitted_kg=Decimal("120"),
            co2_conventional_kg=Decimal("520"),
            co2_avoided_kg=Decimal("400"),
        )
    )
    await db.flush()
    cert = (
        await db.execute(
            select(AnemosCertificate).where(AnemosCertificate.reference == "ANEMOS-BK-2026-0042")
        )
    ).scalar_one()
    cert.booking_id = booking.id
    # Trace GPS pendant la fenêtre du voyage
    for day in range(3):
        db.add(
            VesselPosition(
                vessel_id=vessel.id,
                recorded_at=base + timedelta(days=day + 1),
                latitude=-20.0 + day * 5,
                longitude=-38.0 + day * 5,
                sog_kn=8.0,
                cog_deg=45.0,
            )
        )
    # Relevés de cale
    report = NoonReport(
        leg_id=leg.id,
        recorded_at=base + timedelta(days=1),
        latitude=-20.0,
        longitude=-38.0,
    )
    db.add(report)
    await db.flush()
    db.add(
        NoonReportHold(
            noon_report_id=report.id,
            location="Lower FWD hold",
            temp_midnight_c=19.0,
            humidity_midnight_pct=60.0,
            temp_midday_c=20.0,
            humidity_midday_pct=61.0,
        )
    )
    # Photos : une publique (chargement), une exclue (équipage)
    db.add(
        VoyagePhoto(
            leg_id=leg.id,
            batch_id="loading",
            category="cargo_loading",
            label="Chargement à São Sebastião",
            file_path="voyage/1/loading.jpg",
            file_mime="image/jpeg",
        )
    )
    db.add(
        VoyagePhoto(
            leg_id=leg.id,
            batch_id="crew",
            category="crew_group",
            label="L'équipage",
            file_path="voyage/1/crew.jpg",
            file_mime="image/jpeg",
        )
    )
    await db.flush()
    return account, leg, booking, cert


def test_voyage_page_view_is_whitelisted():
    assert "voyage_page_view" in ANALYTICS_EVENTS


# ───────────────────── opt-in / opt-out client ─────────────────────


@pytest.mark.asyncio
async def test_voyage_public_toggle_on_off_and_ownership(db):
    from fastapi import HTTPException

    from app.routers.client_dashboard_router import booking_voyage_public_toggle

    account, _leg, booking, _cert = await _fixture(db, published=False)

    resp = await booking_voyage_public_toggle(
        booking.reference, enabled="on", client=account, db=db
    )
    assert resp.status_code == 303
    await db.refresh(booking)
    assert booking.voyage_public is True

    resp = await booking_voyage_public_toggle(booking.reference, enabled="", client=account, db=db)
    assert resp.status_code == 303
    await db.refresh(booking)
    assert booking.voyage_public is False

    intruder = ClientAccount(email="other@example.test", hashed_password="x", company_name="X")
    db.add(intruder)
    await db.flush()
    with pytest.raises(HTTPException):
        await booking_voyage_public_toggle(booking.reference, enabled="on", client=intruder, db=db)


# ───────────────────── page publique ─────────────────────


@pytest.mark.asyncio
async def test_voyage_page_renders_published_booking(db):
    from app.routers.voyage_router import voyage_page

    account, leg, booking, cert = await _fixture(db, published=True)
    resp = await voyage_page(booking.reference, _Req(), db=db)
    assert resp.status_code == 200
    assert resp.template.name == "public/voyage.html"
    ctx = resp.context
    assert ctx["found"] is True
    assert ctx["co2_kg"] == 400
    assert ctx["vessel"].name == "Anemos"
    assert len(ctx["track"]) == 3
    assert ctx["conditions"] is not None
    assert ctx["story"] and "Huila" in ctx["story"]
    assert ctx["brand_name"] == "Café du Vent"
    # Filtre vie privée : la photo d'équipage n'apparaît jamais
    assert [p.batch_id for p in ctx["photos"]] == ["loading"]
    # PII : ni email ni société dans la page rendue
    body = resp.body.decode()
    assert "cafe@example.test" not in body
    assert "Torréf SA" not in body

    # Événement analytics « scan QR » posé
    events = (
        (await db.execute(select(AnalyticsEvent).where(AnalyticsEvent.event == "voyage_page_view")))
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].reference == booking.reference
    assert events[0].channel == "public"


@pytest.mark.asyncio
async def test_voyage_page_neutral_404_when_unpublished_or_unknown(db):
    from app.routers.voyage_router import voyage_page

    _account, _leg, booking, _cert = await _fixture(db, published=False)
    resp = await voyage_page(booking.reference, _Req(), db=db)
    assert resp.status_code == 404
    assert resp.context["found"] is False

    resp = await voyage_page("BK-9999-0000", _Req(), db=db)
    assert resp.status_code == 404

    # Publiée mais voyage pas commencé → même rendu neutre
    booking.voyage_public = True
    booking.status = "confirmed"
    await db.flush()
    resp = await voyage_page(booking.reference, _Req(), db=db)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_voyage_photo_route_guards(db):
    from fastapi import HTTPException

    from app.models.voyage_photo import VoyagePhoto as VP
    from app.routers.voyage_router import voyage_photo

    _account, leg, booking, _cert = await _fixture(db, published=True)
    photos = (await db.execute(select(VP).where(VP.leg_id == leg.id))).scalars().all()
    crew_photo = next(p for p in photos if p.batch_id == "crew")

    # Photo d'équipage → 404 (jamais publique)
    with pytest.raises(HTTPException):
        await voyage_photo(booking.reference, crew_photo.id, _Req(), db=db)

    # Photo inexistante → 404
    with pytest.raises(HTTPException):
        await voyage_photo(booking.reference, 99999, _Req(), db=db)

    # Réservation non publiée → 404 même pour une photo publique
    booking.voyage_public = False
    await db.flush()
    public_photo = next(p for p in photos if p.batch_id == "loading")
    with pytest.raises(HTTPException):
        await voyage_photo(booking.reference, public_photo.id, _Req(), db=db)


# ───────────────────── kit B2B2C : cible du QR ─────────────────────


@pytest.mark.asyncio
async def test_kit_qr_targets_voyage_page_when_published(db):
    from app.routers.client_dashboard_router import booking_kit

    account, _leg, booking, _cert = await _fixture(db, published=True)
    req = _Req()
    req.url = SimpleNamespace(path=f"/me/bookings/{booking.reference}/kit")
    resp = await booking_kit(req, booking.reference, client=account, db=db)
    assert resp.status_code == 200
    assert resp.context["voyage_url"].endswith(f"/voyage/{booking.reference}")
    assert resp.context["share_url"] == resp.context["voyage_url"]

    booking.voyage_public = False
    await db.flush()
    resp = await booking_kit(req, booking.reference, client=account, db=db)
    assert resp.context["voyage_url"] is None
    assert resp.context["share_url"] == resp.context["verify_url"]
