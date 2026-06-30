"""Tests d'intégration — wizard de conversion (services adossés à la DB).

Couvre l'autocréation de compte (succès + email déjà existant) et la
sélection des devis éligibles à la relance J+1 (non convertis, non relancés).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.booking import Booking
from app.models.leg import Leg
from app.models.port import Port
from app.models.quote import Quote
from app.models.vessel import Vessel
from app.services import client_account as cas
from app.services import quote_followup


async def _make_leg(db) -> Leg:
    """Crée un leg minimal (vessel + 2 ports) pour les FK booking."""
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
    return leg


@pytest.mark.asyncio
async def test_guest_draft_has_no_account(db):
    """Cœur de la décision : un brouillon invité (client=None) est créé sans
    compte ; le compte est rattaché à la validation."""
    from app.services.booking import BookingItemInput, create_draft

    leg = await _make_leg(db)
    # Ports avec coordonnées → distance calculable pour la grille par défaut.
    pol = await db.get(Port, leg.departure_port_id)
    pod = await db.get(Port, leg.arrival_port_id)
    pol.latitude, pol.longitude = 49.49, 0.11
    pod.latitude, pod.longitude = -32.03, -52.10
    await db.flush()

    booking, _quote = await create_draft(
        db,
        client=None,
        leg=leg,
        items=[
            BookingItemInput(
                pallet_format="EPAL",
                pallet_count=4,
                cargo_description="Café vert",
                unit_weight_kg=Decimal("500"),
            )
        ],
        pickup_address=None,
        delivery_address=None,
        shipper_reference=None,
        notes=None,
        source_quote_reference="DEV-2026-XYZ",
    )
    assert booking.client_account_id is None
    assert booking.status == "draft"
    assert booking.channel == "client"
    assert booking.source_quote_reference == "DEV-2026-XYZ"
    assert booking.estimated_price_eur is not None


@pytest.mark.asyncio
async def test_create_account_success(db):
    account = await cas.create_account(
        db,
        email="Buyer@Example.com",
        password="longenoughpw12",
        company_name="Café du Port",
        contact_name="Marie",
        country="fr",
    )
    assert account.id is not None
    assert account.email == "buyer@example.com"  # normalisé
    assert account.country == "FR"
    assert account.is_verified is True
    assert account.hashed_password and account.hashed_password != "longenoughpw12"


@pytest.mark.asyncio
async def test_create_account_short_password_rejected(db):
    with pytest.raises(cas.AccountError):
        await cas.create_account(db, email="x@example.com", password="short", company_name="ACME")


@pytest.mark.asyncio
async def test_create_account_duplicate_email(db):
    await cas.create_account(
        db, email="dup@example.com", password="longenoughpw12", company_name="ACME"
    )
    with pytest.raises(cas.EmailAlreadyExists):
        await cas.create_account(
            db, email="DUP@example.com", password="longenoughpw12", company_name="ACME2"
        )
    # Lookup insensible à la casse.
    found = await cas.find_by_email(db, "dup@EXAMPLE.com")
    assert found is not None


def _quote(ref: str, *, created_at: datetime, email: str | None, status: str = "issued") -> Quote:
    return Quote(
        reference=ref,
        status=status,
        pol_locode="FRLEH",
        pod_locode="BRRIG",
        contact_email=email,
        palettes_total=10,
        total_eur=Decimal("1000"),
        valid_until=(created_at + timedelta(days=30)).date(),
        created_at=created_at,
        lang="fr",
    )


@pytest.mark.asyncio
async def test_followup_selects_only_eligible_quotes(db):
    now = datetime.now(UTC)
    # Éligible : émis hier, avec email, non converti, non relancé.
    db.add(_quote("DEV-2026-AAA", created_at=now - timedelta(days=1), email="a@ex.com"))
    # Trop récent (< 20 h) → exclu.
    db.add(_quote("DEV-2026-BBB", created_at=now - timedelta(hours=2), email="b@ex.com"))
    # Trop vieux (> 3 j) → exclu.
    db.add(_quote("DEV-2026-CCC", created_at=now - timedelta(days=5), email="c@ex.com"))
    # Sans email → exclu.
    db.add(_quote("DEV-2026-DDD", created_at=now - timedelta(days=1), email=None))
    # Déjà accepté (converti) → exclu.
    db.add(
        _quote(
            "DEV-2026-EEE", created_at=now - timedelta(days=1), email="e@ex.com", status="accepted"
        )
    )
    await db.flush()

    pending = await quote_followup.find_pending(db, now=now)
    refs = {q.reference for q in pending}
    assert refs == {"DEV-2026-AAA"}


@pytest.mark.asyncio
async def test_followup_excludes_quote_with_booking(db):
    now = datetime.now(UTC)
    db.add(_quote("DEV-2026-FFF", created_at=now - timedelta(days=1), email="f@ex.com"))
    leg = await _make_leg(db)
    # Une réservation rattachée au devis neutralise la relance.
    db.add(
        Booking(
            reference="BK-2026-0001",
            leg_id=leg.id,
            status="submitted",
            source_quote_reference="DEV-2026-FFF",
        )
    )
    await db.flush()

    pending = await quote_followup.find_pending(db, now=now)
    assert "DEV-2026-FFF" not in {q.reference for q in pending}


@pytest.mark.asyncio
async def test_followup_marks_sent_once(db):
    now = datetime.now(UTC)
    db.add(_quote("DEV-2026-GGG", created_at=now - timedelta(days=1), email="g@ex.com"))
    await db.flush()

    # SMTP non configuré en test → send=0, mais le devis est marqué relancé
    # (une seule tentative, pas de boucle de spam).
    result = await quote_followup.send_followups(db, now=now)
    assert result["candidates"] == 1

    again = await quote_followup.find_pending(db, now=now)
    assert "DEV-2026-GGG" not in {q.reference for q in again}
