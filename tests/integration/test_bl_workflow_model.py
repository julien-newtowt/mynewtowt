"""Machine à états du BL — les invariants sont tenus PAR LA BASE, pas par le formulaire.

Cf. `docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md` §4.2.

L'enjeu de ces tests : un connaissement est un **titre de propriété**. La règle
« une validation du staff ne se présente jamais comme venant du client » ne peut pas
reposer sur un contrôle de route — il suffirait d'un second chemin d'écriture, d'un
import ou d'un script pour la contourner. Elle est donc posée en `CHECK`.

⚠️ Ces tests n'ont de valeur que parce que les contraintes sont **réellement
appliquées sous SQLite** (les FK et CHECK le sont dans ce projet, cf. `conftest`).
Sans cela ils passeraient à vide.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError


async def _batch(db, **kw):
    """Un lot rattaché à une packing list valide (rail commande)."""
    from app.models.commercial import Client, Order
    from app.models.packing_list import PackingList, PackingListBatch

    if not await db.get(Client, 1):
        db.add(Client(id=1, name="ACME", client_type="shipper"))
        await db.flush()
        db.add(Order(id=1, reference="CMD-1", client_id=1))
        await db.flush()
        db.add(PackingList(id=1, order_id=1))
        await db.flush()
    b = PackingListBatch(packing_list_id=1, batch_number=kw.pop("batch_number", 1), **kw)
    db.add(b)
    await db.flush()
    return b


@pytest.mark.asyncio
async def test_a_fresh_batch_has_no_bl_and_revision_one(db):
    """Défauts : aucun BL, révision 1. `NULL` = aucun BL encore généré."""
    b = await _batch(db)
    assert b.bl_state is None
    assert b.bl_number is None
    assert b.bl_revision == 1


@pytest.mark.asyncio
async def test_client_alone_may_validate(db):
    from app.models.client_account import ClientAccount

    db.add(ClientAccount(id=1, email="c@example.test", hashed_password="x", company_name="ACME"))
    await db.flush()
    b = await _batch(db, bl_state="client_validated", bl_client_validated_by_id=1)
    assert b.bl_client_validated_by_id == 1
    assert b.bl_validated_on_behalf_by_id is None


@pytest.mark.asyncio
async def test_staff_alone_may_validate_on_behalf(db):
    """Repli prévu : un booking sans compte client (FK nullable côté booking)."""
    from app.models.user import User

    # ⚠️ Pas d'`id` en dur : la fixture `db` provisionne déjà un utilisateur, et
    # `id=1` entrerait en collision. On laisse la base l'attribuer.
    staff = User(
        username="ops-bl", email="ops-bl@newtowt.test", hashed_password="x", role="operation"
    )
    db.add(staff)
    await db.flush()
    b = await _batch(db, bl_state="client_validated", bl_validated_on_behalf_by_id=staff.id)
    assert b.bl_validated_on_behalf_by_id == staff.id
    assert b.bl_client_validated_by_id is None


@pytest.mark.asyncio
async def test_both_validators_at_once_is_refused_by_the_database(db):
    """🔴 Le cœur de la contrainte : jamais client ET staff simultanément.

    Sans ce `CHECK`, une validation du staff pourrait cohabiter avec une
    validation client et le document afficherait « validé par le client » sur une
    décision qui ne vient pas de lui. C'est la seule assertion de ce fichier qui
    protège une affirmation juridique.
    """
    from app.models.client_account import ClientAccount
    from app.models.user import User

    client = ClientAccount(email="c-bl@example.test", hashed_password="x", company_name="ACME")
    staff = User(
        username="ops-bl2", email="ops-bl2@newtowt.test", hashed_password="x", role="operation"
    )
    db.add_all([client, staff])
    await db.flush()

    with pytest.raises(IntegrityError):
        await _batch(
            db,
            bl_state="client_validated",
            bl_client_validated_by_id=client.id,
            bl_validated_on_behalf_by_id=staff.id,
        )


@pytest.mark.asyncio
async def test_no_validator_at_all_stays_allowed(db):
    """Les deux `NULL` sont permis : aucune validation n'est encore intervenue."""
    b = await _batch(db, bl_state="draft")
    assert b.bl_client_validated_by_id is None
    assert b.bl_validated_on_behalf_by_id is None


@pytest.mark.asyncio
async def test_revision_cannot_be_zero_or_negative(db):
    """Une révision numérotée annule la précédente : elle ne décroît jamais."""
    with pytest.raises(IntegrityError):
        await _batch(db, bl_revision=0)


@pytest.mark.asyncio
async def test_signature_fields_follow_the_sofevent_pattern(db):
    """Le patron de signature est décalqué de `SofEvent`, pas réinventé.

    Fige la présence des quatre champs : sans le hash, une altération après
    signature serait indétectable.
    """
    from app.models.packing_list import PackingListBatch
    from app.models.sof_event import SofEvent

    for suffix in ("signed_at", "signed_by_id", "signed_by_name"):
        assert hasattr(SofEvent, suffix), f"patron de reference incomplet : {suffix}"
        assert hasattr(PackingListBatch, f"bl_{suffix}")
    assert hasattr(PackingListBatch, "bl_signature_hash")


@pytest.mark.asyncio
async def test_a_revision_can_supersede_another_batch(db):
    """`bl_superseded_by_id` est une FK vers un autre lot du même registre."""
    first = await _batch(db, batch_number=1, bl_state="master_signed")
    second = await _batch(db, batch_number=2, bl_state="draft", bl_revision=2)
    first.bl_superseded_by_id = second.id
    await db.flush()
    assert first.bl_superseded_by_id == second.id


def test_migration_declares_a_reversible_downgrade():
    """La migration doit être révocable : `downgrade` retire tout ce qu'`upgrade` pose."""
    import importlib.util as u

    spec = u.spec_from_file_location("m", "migrations/versions/20260814_0114_bl_workflow_states.py")
    m = u.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.down_revision == "20260807_0113", "doit s'enchaîner sur la fusion de main"
    import pathlib

    body = pathlib.Path(spec.origin).read_text(encoding="utf-8")
    added = body.count("op.add_column")
    dropped = body.count("op.drop_column")
    assert added == dropped, f"{added} colonnes ajoutées, {dropped} retirées"
