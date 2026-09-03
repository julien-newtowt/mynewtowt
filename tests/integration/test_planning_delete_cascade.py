"""Suppression d'un leg en cascade — ouverte aux seuls legs futurs.

Demande des Opérations le 2026-09-03 : « bug à la suppression d'un leg —
Suppression refusée, dépendances : 1 réservations. Rajoute la possibilité de
supprimer les dépendances liées », puis « cette possibilité ne doit être
ouverte que sur des legs futurs ».

Cette contrainte est ce qui rend la fonctionnalité défendable : sur un leg
futur, les dépendances sont des **intentions de planification** ; sur un leg
parti, elles décrivent des **faits**, et les détruire réécrirait l'histoire du
voyage.

Trois garde-fous, tous couverts ici :

1. La cascade est fermée hors leg futur — appareillé, arrivé, terminé, annulé,
   d'archive TOWT, ou ETD passée.
2. Les registres jamais supprimables bloquent dans **les deux** modes : argent
   (ADR-011/013), MRV, ISPS, et les offres tarifaires dont l'historique est
   chaîné en SHA-256. Conséquence heureuse : la *booking note* pendant de
   ``rate_offers``, une réservation issue d'une offre validée est protégée par
   construction.
3. Un artefact probant accroché à une réservation ferme la cascade : numéro de
   BL tiré d'une séquence non recyclable, certificat Anemos publié, facture
   client. Supprimer la réservation les emporterait
   (``ondelete="CASCADE"`` sur les packing lists).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.booking import Booking
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.planning import PlanningError, create_leg, delete_leg, leg_deletion_report

FUTUR = datetime.now(UTC) + timedelta(days=60)
PASSE = datetime.now(UTC) - timedelta(days=60)


async def _seed(db):
    db.add(Vessel(id=1, code="1", name="Anemos"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()


async def _leg(db, *, etd=FUTUR):
    return await create_leg(
        db,
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd=etd,
        eta=etd + timedelta(days=20),
    )


async def _client(db):
    from app.models.commercial import Client

    c = Client(id=1, name="Café du Port", client_type="chargeur")
    db.add(c)
    await db.flush()
    return c


async def _booking(db, leg, *, reference="BK-TEST-1"):
    bk = Booking(
        reference=reference,
        leg_id=leg.id,
        status="submitted",
        total_palettes=2,
        total_weight_kg=Decimal("500.00"),
        total_cubage_m3=Decimal("3.000"),
    )
    db.add(bk)
    await db.flush()
    return bk


# ───────────────────── le défaut signalé, et sa correction ─────────────────


@pytest.mark.asyncio
async def test_simple_delete_still_refuses_a_booking(db):
    """Sans cascade, le comportement d'avant est inchangé : la réservation bloque."""
    await _seed(db)
    leg = await _leg(db)
    await _booking(db, leg)

    with pytest.raises(PlanningError) as exc:
        await delete_leg(db, leg)
    assert "réservations" in str(exc.value)


@pytest.mark.asyncio
async def test_cascade_deletes_a_future_leg_and_its_booking(db):
    """Le cas signalé : leg futur + 1 réservation → cascade autorisée."""
    await _seed(db)
    leg = await _leg(db)
    await _booking(db, leg)
    leg_id = leg.id

    report = await leg_deletion_report(db, leg)
    assert report.can_cascade is True
    assert report.hard_blocking == []
    assert any("réservations" in t for t in report.cascade_targets)

    await delete_leg(db, leg, cascade=True)

    assert await db.scalar(select(func.count()).select_from(Leg).where(Leg.id == leg_id)) == 0
    assert (
        await db.scalar(select(func.count()).select_from(Booking).where(Booking.leg_id == leg_id))
        == 0
    )


# ───────────────────── garde-fou 1 : legs futurs seulement ─────────────────


@pytest.mark.asyncio
async def test_cascade_refused_on_a_departed_leg(db):
    """ATD posé = le voyage a eu lieu : ses dépendances sont des faits."""
    await _seed(db)
    leg = await _leg(db)
    await _booking(db, leg)
    leg.atd = datetime.now(UTC) - timedelta(days=1)
    await db.flush()

    with pytest.raises(PlanningError) as exc:
        await delete_leg(db, leg, cascade=True)
    assert "legs à venir" in str(exc.value)

    report = await leg_deletion_report(db, leg)
    assert report.can_cascade is False


@pytest.mark.asyncio
async def test_cascade_refused_when_etd_has_passed(db):
    """Un leg planifié jamais appareillé s'instruit, il ne s'efface pas."""
    await _seed(db)
    leg = await _leg(db, etd=PASSE)
    await _booking(db, leg)

    with pytest.raises(PlanningError) as exc:
        await delete_leg(db, leg, cascade=True)
    assert "ETD est passée" in str(exc.value)


@pytest.mark.asyncio
async def test_cascade_refused_on_a_cancelled_leg(db):
    await _seed(db)
    leg = await _leg(db)
    await _booking(db, leg)
    leg.status = "cancelled"
    await db.flush()

    with pytest.raises(PlanningError):
        await delete_leg(db, leg, cascade=True)


# ──────── garde-fou 2 : les registres non supprimables bloquent toujours ────


@pytest.mark.asyncio
async def test_money_register_blocks_even_in_cascade(db):
    """Un mouvement de caisse bloque la cascade — le grand livre n'a pas de DELETE."""
    from app.models.onboard_cashbox import CashboxMovement, OnboardCashbox

    await _seed(db)
    leg = await _leg(db)
    await _booking(db, leg)
    box = OnboardCashbox(vessel_id=1)
    db.add(box)
    await db.flush()
    db.add(
        CashboxMovement(
            cashbox_id=box.id,
            leg_id=leg.id,
            amount=Decimal("12.00"),
            currency="EUR",
            category="vente_a_bord",
            description="vente test",
            occurred_at=datetime.now(UTC),
        )
    )
    await db.flush()

    report = await leg_deletion_report(db, leg)
    assert report.can_cascade is False
    assert any("mouvements de caisse" in b for b in report.hard_blocking)

    with pytest.raises(PlanningError) as exc:
        await delete_leg(db, leg, cascade=True)
    assert "annulez le leg" in str(exc.value)


@pytest.mark.asyncio
async def test_rate_offer_blocks_even_in_cascade(db):
    """L'offre tarifaire porte une chaîne SHA-256 : elle n'est jamais détruite.

    Elle protège du même coup la *booking note*, qui pend de ``rate_offers``.
    """
    from app.models.commercial import RateOffer

    await _seed(db)
    await _client(db)
    leg = await _leg(db)
    db.add(
        RateOffer(
            reference="OFF-TEST-1",
            client_id=1,
            title="Offre de test",
            leg_id=leg.id,
            status="en_cours",
        )
    )
    await db.flush()

    report = await leg_deletion_report(db, leg)
    assert any("offres tarifaires" in b for b in report.hard_blocking)
    assert report.can_cascade is False


# ──────── garde-fou 3 : un artefact probant ferme la cascade ────────


@pytest.mark.asyncio
async def test_bl_number_closes_the_cascade(db):
    """Supprimer la réservation emporterait la packing list — donc le numéro de BL."""
    from app.models.packing_list import PackingList, PackingListBatch

    await _seed(db)
    leg = await _leg(db)
    bk = await _booking(db, leg)
    pl = PackingList(booking_id=bk.id, leg_id=leg.id)
    db.add(pl)
    await db.flush()
    db.add(PackingListBatch(packing_list_id=pl.id, bl_number="TUAW-2026-0001"))
    await db.flush()

    report = await leg_deletion_report(db, leg)
    assert report.can_cascade is False
    assert any("numéro de BL" in r for r in report.cascade_refusals)

    with pytest.raises(PlanningError) as exc:
        await delete_leg(db, leg, cascade=True)
    assert "font foi" in str(exc.value)


# ──────── les FK nullables vers la réservation sont déliées ────────


@pytest.mark.asyncio
async def test_cascade_unlinks_order_from_the_deleted_booking(db):
    """``Order.booking_id`` est nullable : on le délie au lieu de bloquer."""
    from app.models.commercial import Order

    await _seed(db)
    await _client(db)
    leg = await _leg(db)
    bk = await _booking(db, leg)
    order = Order(reference="CMD-TEST-1", client_id=1, leg_id=leg.id, booking_id=bk.id)
    db.add(order)
    await db.flush()
    order_id = order.id

    await delete_leg(db, leg, cascade=True)

    survivor = await db.get(Order, order_id)
    assert survivor is not None, "la commande devait survivre au leg"
    assert survivor.booking_id is None
    assert survivor.leg_id is None
