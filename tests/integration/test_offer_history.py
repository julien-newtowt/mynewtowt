"""Historique des offres commerciales — complétude et détection d'altération.

L'historique est la pièce mobilisable si un client conteste un prix. Ces tests
vérifient qu'il enregistre bien ce qui a changé, qu'il conserve un état
rejouable, et surtout que **toute retouche de l'historique lui-même est
détectable** — sans quoi il n'aurait aucune valeur probante.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.commercial import Client, RateOffer, RateOfferRevision
from app.services.offer_history import (
    list_revisions,
    record_revision,
    snapshot_offer,
    verify_chain,
)


async def _offer(db) -> RateOffer:
    client = Client(name="Cacao Négoce", client_type="freight_forwarder")
    db.add(client)
    await db.flush()
    offer = RateOffer(
        reference="RO-2026-0001",
        client_id=client.id,
        title="Transat café vert",
        status="draft",
        estimated_palettes=120,
        proposed_rate_eur=Decimal("310.00"),
        total_eur=Decimal("37200.00"),
        valid_until=date(2026, 9, 30),
    )
    db.add(offer)
    await db.flush()
    return offer


@pytest.mark.asyncio
async def test_creation_is_recorded_with_a_full_snapshot(db):
    offer = await _offer(db)
    revision = await record_revision(db, offer, action="created", actor_name="Yasmin")

    assert revision is not None
    assert revision.sequence == 1
    assert revision.previous_hash is None
    assert revision.changes == []  # une création n'a pas de « avant »
    assert revision.snapshot["proposed_rate_eur"] == "310.00"
    assert revision.snapshot["status"] == "draft"


@pytest.mark.asyncio
async def test_price_change_is_recorded_field_by_field(db):
    offer = await _offer(db)
    await record_revision(db, offer, action="created")

    before = snapshot_offer(offer)
    offer.proposed_rate_eur = Decimal("285.00")
    offer.total_eur = Decimal("34200.00")
    await db.flush()
    revision = await record_revision(db, offer, action="updated", before=before)

    assert revision is not None
    changed = {c["field"]: (c["old"], c["new"]) for c in revision.changes}
    assert changed["proposed_rate_eur"] == ("310.00", "285.00")
    assert changed["total_eur"] == ("37200.00", "34200.00")
    assert "title" not in changed  # inchangé → absent du diff


@pytest.mark.asyncio
async def test_update_without_any_change_records_nothing(db):
    """Un historique qui consigne des non-événements devient illisible."""
    offer = await _offer(db)
    await record_revision(db, offer, action="created")

    before = snapshot_offer(offer)
    assert await record_revision(db, offer, action="updated", before=before) is None
    assert len(await list_revisions(db, offer.id)) == 1


@pytest.mark.asyncio
async def test_state_change_is_recorded_even_without_field_change(db):
    """Envoi, validation, annulation : toujours tracés, même sans autre changement."""
    offer = await _offer(db)
    await record_revision(db, offer, action="created")

    before = snapshot_offer(offer)
    offer.status = "sent"
    await db.flush()
    revision = await record_revision(db, offer, action="sent", before=before)

    assert revision is not None and revision.sequence == 2
    assert revision.changes[0]["field"] == "status"


@pytest.mark.asyncio
async def test_chain_is_valid_across_several_revisions(db):
    offer = await _offer(db)
    await record_revision(db, offer, action="created")
    for rate in ("300.00", "290.00", "280.00"):
        before = snapshot_offer(offer)
        offer.proposed_rate_eur = Decimal(rate)
        await db.flush()
        await record_revision(db, offer, action="updated", before=before)

    revisions = await list_revisions(db, offer.id)
    assert [r.sequence for r in revisions] == [1, 2, 3, 4]
    # Chaque révision pointe sur l'empreinte de la précédente.
    for previous, current in zip(revisions, revisions[1:], strict=False):
        assert current.previous_hash == previous.content_hash

    ok, reason = await verify_chain(db, offer.id)
    assert ok and reason is None


@pytest.mark.asyncio
async def test_tampering_with_a_snapshot_is_detected(db):
    """Réécrire un prix dans l'historique doit se voir — c'est tout l'enjeu."""
    offer = await _offer(db)
    await record_revision(db, offer, action="created")
    before = snapshot_offer(offer)
    offer.proposed_rate_eur = Decimal("285.00")
    await db.flush()
    await record_revision(db, offer, action="updated", before=before)

    assert (await verify_chain(db, offer.id))[0] is True

    # Quelqu'un réécrit le prix consigné, en base, sans passer par l'application.
    revisions = await list_revisions(db, offer.id)
    revisions[1].snapshot_json = revisions[1].snapshot_json.replace("285.00", "410.00")
    await db.flush()

    ok, reason = await verify_chain(db, offer.id)
    assert ok is False
    assert "altéré" in reason


@pytest.mark.asyncio
async def test_removing_a_revision_from_the_middle_is_detected(db):
    """Supprimer une révision gênante casse la chaîne des suivantes."""
    offer = await _offer(db)
    await record_revision(db, offer, action="created")
    for rate in ("300.00", "280.00"):
        before = snapshot_offer(offer)
        offer.proposed_rate_eur = Decimal(rate)
        await db.flush()
        await record_revision(db, offer, action="updated", before=before)

    revisions = await list_revisions(db, offer.id)
    await db.delete(revisions[1])
    await db.flush()

    ok, reason = await verify_chain(db, offer.id)
    assert ok is False
    assert reason is not None


@pytest.mark.asyncio
async def test_two_revisions_cannot_claim_the_same_rank(db):
    """La contrainte d'unicité empêche d'insérer une révision « à côté » de la chaîne."""
    from sqlalchemy.exc import IntegrityError

    offer = await _offer(db)
    first = await record_revision(db, offer, action="created")
    assert first is not None

    db.add(
        RateOfferRevision(
            offer_id=offer.id,
            sequence=1,  # rang déjà pris
            action="updated",
            snapshot_json="{}",
            content_hash="0" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_unknown_action_is_refused(db):
    offer = await _offer(db)
    with pytest.raises(ValueError, match="action"):
        await record_revision(db, offer, action="bidouille")


def test_revision_history_table_is_never_purgeable():
    """L'historique ne doit être ni vidable ni purgeable par rétention."""
    from app.services.admin_data import ALLOWED_PURGE_TABLES, NEVER_PURGE_TABLES

    assert "rate_offer_revisions" in NEVER_PURGE_TABLES
    assert "rate_offer_revisions" not in ALLOWED_PURGE_TABLES
