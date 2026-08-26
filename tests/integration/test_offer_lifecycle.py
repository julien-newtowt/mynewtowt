"""Cycle de vie de l'offre commerciale et réservation de volume (lot 4).

Deux règles métier structurantes :

* une offre est **échue** dès que sa validité est dépassée **ou** que le navire
  est parti (ATD), et l'échéance libère le volume réservé ;
* une offre en cours ou validée **réserve du volume** dans le chargement
  prévisionnel du leg — sans jamais le compter deux fois quand elle donne
  naissance à une commande.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.commercial import Client, Order, RateOffer
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.capacity import get_available_capacity
from app.services.offer_lifecycle import (
    OfferTransitionError,
    cancel_offer,
    expire_due_offers,
    is_expired,
    validate_offer,
)


async def _fixture(db, *, atd: datetime | None = None) -> tuple[Client, Leg]:
    db.add_all(
        [
            Port(id=1, locode="FRLEH", name="Le Havre", country="FR"),
            Port(id=2, locode="BRSSZ", name="Santos", country="BR"),
            Vessel(id=1, code="ANE", name="Anemos", capacity_palettes=500),
        ]
    )
    client = Client(name="Café du Port", client_type="shipper")
    db.add(client)
    await db.flush()
    leg = Leg(
        leg_code="1AFRBR6",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=datetime(2026, 6, 1, tzinfo=UTC),
        eta_ref=datetime(2026, 6, 25, tzinfo=UTC),
        etd=datetime(2026, 6, 1, tzinfo=UTC),
        eta=datetime(2026, 6, 25, tzinfo=UTC),
        atd=atd,
        is_bookable=True,
    )
    db.add(leg)
    await db.flush()
    return client, leg


async def _offer(db, client, leg, *, palettes=100, valid_until=None, status="en_cours"):
    offer = RateOffer(
        reference=f"RO-2026-{palettes:04d}",
        client_id=client.id,
        leg_id=leg.id,
        title="Transat café vert",
        status=status,
        estimated_palettes=palettes,
        valid_until=valid_until or date(2026, 5, 15),
    )
    db.add(offer)
    await db.flush()
    return offer


# ───────────────────────────── Échéance ─────────────────────────────


@pytest.mark.asyncio
async def test_offer_expires_when_validity_passed(db):
    client, leg = await _fixture(db)
    offer = await _offer(db, client, leg, valid_until=date(2026, 4, 30))

    assert is_expired(offer, leg, on_date=date(2026, 5, 1)) is True
    expired = await expire_due_offers(db, on_date=date(2026, 5, 1))
    assert [o.id for o in expired] == [offer.id]
    assert offer.status == "echue"
    assert offer.expired_at is not None


@pytest.mark.asyncio
async def test_offer_expires_when_vessel_has_sailed_even_if_still_valid(db):
    """Navire parti = offre sans objet, même dans la fenêtre de validité."""
    client, leg = await _fixture(db, atd=datetime(2026, 6, 2, tzinfo=UTC))
    offer = await _offer(db, client, leg, valid_until=date(2026, 12, 31))

    assert is_expired(offer, leg, on_date=date(2026, 6, 3)) is True
    expired = await expire_due_offers(db, on_date=date(2026, 6, 3))
    assert [o.id for o in expired] == [offer.id]
    assert offer.status == "echue"


@pytest.mark.asyncio
async def test_offer_still_in_validity_on_a_moored_vessel_is_untouched(db):
    client, leg = await _fixture(db)
    offer = await _offer(db, client, leg, valid_until=date(2026, 5, 31))

    assert await expire_due_offers(db, on_date=date(2026, 5, 1)) == []
    assert offer.status == "en_cours"


@pytest.mark.asyncio
async def test_expiry_sweep_is_idempotent(db):
    client, leg = await _fixture(db)
    await _offer(db, client, leg, valid_until=date(2026, 4, 30))

    first = await expire_due_offers(db, on_date=date(2026, 5, 1))
    second = await expire_due_offers(db, on_date=date(2026, 5, 1))
    assert len(first) == 1
    assert second == []  # rien à rebasculer


# ──────────────────────── Validation / annulation ────────────────────────


@pytest.mark.asyncio
async def test_validate_then_cancel_is_refused(db):
    """Une offre close ne se rouvre pas — ni pour l'annuler, ni pour la valider."""
    client, leg = await _fixture(db)
    offer = await _offer(db, client, leg)

    await validate_offer(db, offer, actor_name="Yasmin")
    assert offer.status == "valide" and offer.validated_at is not None

    with pytest.raises(OfferTransitionError):
        await cancel_offer(db, offer)


@pytest.mark.asyncio
async def test_cancel_records_the_reason(db):
    client, leg = await _fixture(db)
    offer = await _offer(db, client, leg)

    await cancel_offer(db, offer, reason="Le client a renoncé", actor_name="Yasmin")
    assert offer.status == "annule"
    assert offer.cancelled_reason == "Le client a renoncé"

    with pytest.raises(OfferTransitionError):
        await validate_offer(db, offer)


@pytest.mark.asyncio
async def test_expired_offer_cannot_be_validated(db):
    client, leg = await _fixture(db)
    offer = await _offer(db, client, leg, valid_until=date(2026, 4, 30))
    await expire_due_offers(db, on_date=date(2026, 5, 1))

    with pytest.raises(OfferTransitionError):
        await validate_offer(db, offer)


# ─────────────────── Réservation du volume prévisionnel ───────────────────


@pytest.mark.asyncio
async def test_offer_reserves_volume_on_the_leg(db):
    client, leg = await _fixture(db)
    before = await get_available_capacity(db, leg.id)
    assert before.available_palettes == 500

    await _offer(db, client, leg, palettes=120)
    after = await get_available_capacity(db, leg.id)
    assert after.reserved_palettes == 120
    assert after.available_palettes == 380


@pytest.mark.asyncio
async def test_cancelled_and_expired_offers_release_the_volume(db):
    client, leg = await _fixture(db)
    offer = await _offer(db, client, leg, palettes=120)
    assert (await get_available_capacity(db, leg.id)).reserved_palettes == 120

    await cancel_offer(db, offer, reason="désistement")
    assert (await get_available_capacity(db, leg.id)).reserved_palettes == 0


@pytest.mark.asyncio
async def test_validated_offer_keeps_reserving(db):
    client, leg = await _fixture(db)
    offer = await _offer(db, client, leg, palettes=120)
    await validate_offer(db, offer)
    assert (await get_available_capacity(db, leg.id)).reserved_palettes == 120


@pytest.mark.asyncio
async def test_offer_converted_to_order_is_not_counted_twice(db):
    """Le volume passe à la commande — il ne doit pas rester compté sur l'offre."""
    client, leg = await _fixture(db)
    offer = await _offer(db, client, leg, palettes=120)
    await validate_offer(db, offer)

    order = Order(
        reference="ORD-2026-0001",
        client_id=client.id,
        offer_id=offer.id,
        leg_id=leg.id,
        status="confirmed",
        booked_palettes=120,
        rate_per_palette_eur=Decimal("300.00"),
    )
    db.add(order)
    await db.flush()

    capacity = await get_available_capacity(db, leg.id)
    # 120 et non 240 : la commande porte le volume, l'offre ne le compte plus.
    assert capacity.reserved_palettes == 120
    assert capacity.available_palettes == 380


@pytest.mark.asyncio
async def test_several_offers_accumulate_on_the_same_leg(db):
    client, leg = await _fixture(db)
    await _offer(db, client, leg, palettes=120)
    await _offer(db, client, leg, palettes=80)

    capacity = await get_available_capacity(db, leg.id)
    assert capacity.reserved_palettes == 200
    assert capacity.available_palettes == 300


@pytest.mark.asyncio
async def test_expiry_frees_capacity_for_the_next_client(db):
    """Enchaînement complet : réservation, échéance, capacité rendue."""
    client, leg = await _fixture(db)
    await _offer(db, client, leg, palettes=450, valid_until=date(2026, 4, 30))
    assert (await get_available_capacity(db, leg.id)).available_palettes == 50

    await expire_due_offers(db, on_date=date(2026, 5, 1))
    assert (await get_available_capacity(db, leg.id)).available_palettes == 500
