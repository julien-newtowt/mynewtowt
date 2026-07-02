"""Tests d'intégration — routes du volet social du kit B2B2C (P12).

Appelle directement les coroutines de route (SQLite in-memory) : rendu SVG
complet exercé. Couvre : SVG 200 + validité + CO₂ en kg + « Anemos » + aucun
« % » ; ownership (autre client → 404) ; format inconnu → 404 ; pack ZIP.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.anemos_certificate import AnemosCertificate
from app.models.booking import Booking
from app.models.client_account import ClientAccount
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.routers.client_dashboard_router import booking_social_svg, booking_social_zip


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/me/bookings/X/social")
    state = SimpleNamespace(lang="fr")


async def _client(db, email="buyer@example.test") -> ClientAccount:
    acc = ClientAccount(email=email, hashed_password="x", company_name="ACME SA")
    db.add(acc)
    await db.flush()
    return acc


async def _booking(db, client, *, origin="colombie", co2=300, with_cert=True) -> Booking:
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
        reference="TUAW-SOC-1",
        leg_id=leg.id,
        client_account_id=client.id,
        status="delivered",
        coffee_origin=origin,
    )
    db.add(booking)
    await db.flush()
    if with_cert:
        db.add(
            AnemosCertificate(
                reference="ANEMOS-SOC-1",
                booking_id=booking.id,
                client_account_id=client.id,
                leg_id=leg.id,
                tonnage_transported_t=Decimal("10"),
                distance_nm=Decimal("5000"),
                co2_emitted_kg=Decimal("100"),
                co2_conventional_kg=Decimal("400"),
                co2_avoided_kg=Decimal(str(co2)),
            )
        )
        await db.flush()
    return booking


def _body(resp) -> str:
    return resp.body.decode("utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["square", "story", "landscape"])
async def test_social_svg_ok_valid_and_absolute_kg(db, fmt):
    client = await _client(db)
    booking = await _booking(db, client, origin="colombie", co2=300)
    resp = await booking_social_svg(_Req(), booking.reference, fmt, client=client, db=db)
    assert resp.status_code == 200
    assert resp.media_type == "image/svg+xml"
    svg = _body(resp)
    ET.fromstring(svg)  # SVG bien formé
    assert "300 kg" in svg  # CO₂ absolu, contigu
    assert "Anemos" in svg  # certificat nommé
    assert "%" not in svg  # ECGT : jamais de pourcentage


@pytest.mark.asyncio
async def test_social_svg_unknown_format_404(db):
    client = await _client(db)
    booking = await _booking(db, client)
    with pytest.raises(HTTPException) as exc:
        await booking_social_svg(_Req(), booking.reference, "banner", client=client, db=db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_social_svg_rejects_other_client(db):
    owner = await _client(db)
    booking = await _booking(db, owner)
    intruder = await _client(db, email="intruder@example.test")
    with pytest.raises(HTTPException) as exc:
        await booking_social_svg(_Req(), booking.reference, "square", client=intruder, db=db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_social_svg_cacao_origin_renders(db):
    client = await _client(db)
    booking = await _booking(db, client, origin="equateur", co2=260)  # verticale cacao
    resp = await booking_social_svg(_Req(), booking.reference, "square", client=client, db=db)
    svg = _body(resp)
    ET.fromstring(svg)
    assert "260 kg" in svg
    assert "Anemos" in svg
    assert "CACAO" in svg  # eyebrow commodité
    assert "%" not in svg


@pytest.mark.asyncio
async def test_social_svg_without_cert_is_qualitative(db):
    client = await _client(db)
    booking = await _booking(db, client, origin=None, with_cert=False)
    resp = await booking_social_svg(_Req(), booking.reference, "square", client=client, db=db)
    svg = _body(resp)
    ET.fromstring(svg)
    assert "Anemos" in svg
    assert "%" not in svg
    assert "kg" not in svg  # aucun chiffre inventé


@pytest.mark.asyncio
async def test_social_zip_contains_three_svgs(db):
    client = await _client(db)
    booking = await _booking(db, client, origin="colombie", co2=300)
    resp = await booking_social_zip(_Req(), booking.reference, client=client, db=db)
    assert resp.status_code == 200
    assert resp.media_type == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.body))
    names = zf.namelist()
    svgs = [n for n in names if n.endswith(".svg")]
    assert len(svgs) == 3
    assert any(n.endswith("LISEZMOI.txt") for n in names)
    for n in svgs:
        content = zf.read(n).decode("utf-8")
        ET.fromstring(content)
        assert "Anemos" in content
        assert "%" not in content


@pytest.mark.asyncio
async def test_social_zip_rejects_other_client(db):
    owner = await _client(db)
    booking = await _booking(db, owner)
    intruder = await _client(db, email="intruder2@example.test")
    with pytest.raises(HTTPException) as exc:
        await booking_social_zip(_Req(), booking.reference, client=intruder, db=db)
    assert exc.value.status_code == 404
