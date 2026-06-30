"""Tests d'intégration — kit B2B2C & espace marque (Vague 3).

Appelle directement les coroutines de route (session aiosqlite in-memory) :
le rendu Jinja complet est exercé et toute erreur de template lève. Couvre la
page kit (récit + CO₂ + QR), le terroir self-service et le co-branding.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.anemos_certificate import AnemosCertificate
from app.models.booking import Booking
from app.models.client_account import ClientAccount
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.routers.client_dashboard_router import (
    booking_kit,
    booking_kit_save,
    brand_logo_delete,
    brand_save,
    brand_space,
)


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/me/bookings/X/kit")
    state = SimpleNamespace(lang="fr")


async def _client(db) -> ClientAccount:
    acc = ClientAccount(email="buyer@example.test", hashed_password="x", company_name="ACME SA")
    db.add(acc)
    await db.flush()
    return acc


async def _booking(db, client, *, origin=None, with_cert=True) -> Booking:
    vessel = Vessel(code="ANEM", name="Anemos")
    pol = Port(locode="FRLEH", name="Le Havre", country="FR")
    pod = Port(locode="BRRIG", name="Rio Grande", country="BR")
    db.add_all([vessel, pol, pod])
    await db.flush()
    now = datetime.now(UTC)
    leg = Leg(
        leg_code="1ANFRBR6",
        vessel_id=vessel.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd_ref=now,
        eta_ref=now + timedelta(days=20),
        etd=now,
        eta=now + timedelta(days=20),
        is_bookable=True,
    )
    db.add(leg)
    await db.flush()
    booking = Booking(
        reference="TUAW-TEST-1",
        leg_id=leg.id,
        client_account_id=client.id,
        status="delivered",
        coffee_origin=origin,
        coffee_region="Huila" if origin else None,
        coffee_producer="Coop El Cóndor" if origin else None,
    )
    db.add(booking)
    await db.flush()
    if with_cert:
        db.add(
            AnemosCertificate(
                reference="ANEMOS-TEST-1",
                booking_id=booking.id,
                client_account_id=client.id,
                leg_id=leg.id,
                tonnage_transported_t=Decimal("10"),
                distance_nm=Decimal("5000"),
                co2_emitted_kg=Decimal("100"),
                co2_conventional_kg=Decimal("400"),
                co2_avoided_kg=Decimal("300"),
            )
        )
        await db.flush()
    return booking


# ───────────────────────── page kit ─────────────────────────
@pytest.mark.asyncio
async def test_kit_page_with_cert_and_origin(db):
    client = await _client(db)
    booking = await _booking(db, client, origin="colombie")
    resp = await booking_kit(_Req(), booking.reference, client=client, db=db)
    assert resp.status_code == 200
    assert resp.template.name == "client/kit.html"
    ctx = resp.context
    assert ctx["origin"] == "colombie"
    assert ctx["co2_kg"] == 300  # depuis le certificat (co2_avoided_kg)
    assert ctx["cert"] is not None
    assert "Huila, Colombie" in ctx["story_long"]
    assert "l'Anemos" in ctx["story_long"]
    assert "300 kg" in ctx["story_short"]
    assert ctx["verify_url"].endswith("/verify/ANEMOS-TEST-1")


@pytest.mark.asyncio
async def test_kit_page_without_cert_or_origin(db):
    client = await _client(db)
    booking = await _booking(db, client, origin=None, with_cert=False)
    resp = await booking_kit(_Req(), booking.reference, client=client, db=db)
    assert resp.status_code == 200
    assert resp.context["origin"] is None
    assert resp.context["co2_kg"] is None
    assert resp.context["story_long"] is None


@pytest.mark.asyncio
async def test_kit_page_rejects_other_client(db):
    owner = await _client(db)
    booking = await _booking(db, owner, origin="mexique")
    intruder = ClientAccount(
        email="intruder@example.test", hashed_password="x", company_name="Other"
    )
    db.add(intruder)
    await db.flush()
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await booking_kit(_Req(), booking.reference, client=intruder, db=db)


# ───────────────────────── terroir self-service ─────────────────────────
@pytest.mark.asyncio
async def test_kit_save_persists_valid_terroir(db):
    client = await _client(db)
    booking = await _booking(db, client, origin=None)
    resp = await booking_kit_save(
        booking.reference,
        coffee_origin="GUATEMALA",  # casse tolérée
        coffee_region="Huehuetenango",
        coffee_producer="Coop X",
        client=client,
        db=db,
    )
    assert resp.status_code == 303
    await db.refresh(booking)
    assert booking.coffee_origin == "guatemala"
    assert booking.coffee_region == "Huehuetenango"
    assert booking.coffee_producer == "Coop X"


@pytest.mark.asyncio
async def test_kit_save_rejects_unknown_origin(db):
    client = await _client(db)
    booking = await _booking(db, client, origin="colombie")
    await booking_kit_save(
        booking.reference,
        coffee_origin="brazil",
        coffee_region="",
        coffee_producer="",
        client=client,
        db=db,
    )
    await db.refresh(booking)
    assert booking.coffee_origin is None  # origine inconnue rejetée
    assert booking.coffee_region is None


# ───────────────────────── espace marque ─────────────────────────
@pytest.mark.asyncio
async def test_brand_space_renders(db):
    client = await _client(db)
    resp = await brand_space(_Req(), client=client)
    assert resp.status_code == 200
    assert resp.template.name == "client/brand.html"


@pytest.mark.asyncio
async def test_brand_save_sets_name_and_delete_logo(db):
    client = await _client(db)
    resp = await brand_save(_Req(), brand_name="ACME Coffee", logo=None, client=client, db=db)
    assert resp.status_code == 303
    await db.refresh(client)
    assert client.brand_name == "ACME Coffee"
    # Suppression de logo idempotente même sans logo.
    client.brand_logo_path = "brand/1/x.png"
    await db.flush()
    await brand_logo_delete(client=client, db=db)
    await db.refresh(client)
    assert client.brand_logo_path is None
