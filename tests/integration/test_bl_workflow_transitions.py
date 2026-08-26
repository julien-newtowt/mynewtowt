"""Transitions d'état du BL — machine à états, gel, régression, journalisation.

Cf. `docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md` §4.1 et §4.3.

Ce que ces tests protègent, dans l'ordre d'importance :

1. **le gel après signature** — après signature un connaissement engage le
   transporteur ; une édition silencieuse à ce stade est une falsification ;
2. **la règle de régression** — une validation client obtenue sur un contenu qui a
   changé depuis ne doit plus être présentée comme valide ;
3. **la détection d'altération** — le hash n'a de valeur que si quelqu'un le
   vérifie, et que « non signé » ne se confond pas avec « intact » ;
4. **la double journalisation** — `activity_logs` pour le lecteur externe,
   `PackingListAudit` pour le dossier.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.services import bl_workflow as w


async def _ctx(db):
    """Packing list + lot + acteurs. Aucun `id` en dur : la fixture en provisionne."""
    from app.models.client_account import ClientAccount
    from app.models.commercial import Client, Order
    from app.models.packing_list import PackingList, PackingListBatch
    from app.models.user import User

    db.add(Client(id=1, name="ACME", client_type="shipper"))
    await db.flush()
    db.add(Order(id=1, reference="CMD-1", client_id=1))
    await db.flush()
    pl = PackingList(order_id=1)
    db.add(pl)
    await db.flush()
    batch = PackingListBatch(
        packing_list_id=pl.id,
        batch_number=1,
        pallet_count=4,
        shipper_name="Belco",
        consignee_name="Belco France",
        description_of_goods="Café vert",
    )
    db.add(batch)
    staff = User(
        username="ops-w", email="ops-w@newtowt.test", hashed_password="x", role="operation"
    )
    master = User(username="cdt-w", email="cdt-w@newtowt.test", hashed_password="x", role="marins")
    client = ClientAccount(email="cli-w@example.test", hashed_password="x", company_name="Belco")
    db.add_all([staff, master, client])
    await db.flush()
    return pl, batch, staff, master, client


async def _logs(db, action: str | None = None):
    from app.models.activity_log import ActivityLog

    stmt = select(ActivityLog).where(ActivityLog.module == "cargo")
    if action:
        stmt = stmt.where(ActivityLog.action == action)
    return list((await db.execute(stmt)).scalars().all())


# ───────────────────────── machine à états ─────────────────────────


def test_the_state_machine_is_written_not_inferred():
    """La table des transitions est explicite : elle se relit, un `index()` non."""
    assert w.ALLOWED_TRANSITIONS[None] == ("draft",)
    assert w.ALLOWED_TRANSITIONS["draft"] == ("client_validated",)
    assert w.ALLOWED_TRANSITIONS["final"] == ()
    # Le retour à draft depuis client_validated est la règle de RÉGRESSION.
    assert "draft" in w.ALLOWED_TRANSITIONS["client_validated"]


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (None, "client_validated"),  # on ne saute pas le draft
        (None, "master_signed"),
        ("draft", "master_signed"),  # on ne signe pas un draft non validé
        ("draft", "final"),
        ("client_validated", "final"),  # on n'émet pas sans signature
        ("final", "draft"),  # un final ne redevient pas modifiable
        ("master_signed", "client_validated"),
    ],
)
def test_illegal_transitions_are_refused(current, target):
    with pytest.raises(w.InvalidTransition):
        w.assert_transition(current, target)


def test_an_unknown_state_is_refused():
    with pytest.raises(w.InvalidTransition):
        w.assert_transition("draft", "approuve_peut_etre")


# ───────────────────────── cycle nominal ─────────────────────────


@pytest.mark.asyncio
async def test_the_happy_path_traces_every_transition(db):
    pl, batch, staff, master, client = await _ctx(db)

    number = await w.generate_draft(db, pl=pl, batch=batch, leg=None, user=staff)
    assert batch.bl_state == "draft"
    assert number and batch.bl_number == number
    # Comble un trou d'audit : l'émission actuelle ne dit jamais QUI a émis.
    assert batch.bl_issued_by_id == staff.id
    assert batch.bl_issued_by_name

    await w.validate_by_client(db, batch=batch, client=client)
    assert batch.bl_state == "client_validated"
    assert batch.bl_client_validated_by_id == client.id
    assert batch.bl_validated_on_behalf_by_id is None

    h = await w.sign_by_master(db, batch=batch, user=master)
    assert batch.bl_state == "master_signed"
    assert len(h) == 64 and batch.bl_signature_hash == h

    await w.issue_final(db, batch=batch, user=staff)
    assert batch.bl_state == "final"

    for action in (
        "bl_draft_generated",
        "bl_client_validated",
        "bl_master_signed",
        "bl_issued_final",
    ):
        assert len(await _logs(db, action)) == 1, f"{action} non tracée"


@pytest.mark.asyncio
async def test_both_pistes_are_written_not_just_one(db):
    """Les deux journaux servent deux lecteurs différents — les deux sont exigés."""
    from app.models.packing_list import PackingListAudit

    pl, batch, staff, _m, _c = await _ctx(db)
    await w.generate_draft(db, pl=pl, batch=batch, leg=None, user=staff)

    assert await _logs(db, "bl_draft_generated"), "activity_logs vide"
    audits = list((await db.execute(select(PackingListAudit))).scalars().all())
    assert any(a.field == "bl_state" for a in audits), "PackingListAudit vide"


# ───────────────────────── validation : client XOR staff ─────────────────────────


@pytest.mark.asyncio
async def test_staff_may_validate_on_behalf_and_it_says_so(db):
    """Le repli est tracé COMME TEL — jamais présenté comme venant du client."""
    pl, batch, staff, _m, _c = await _ctx(db)
    await w.generate_draft(db, pl=pl, batch=batch, leg=None, user=staff)

    await w.validate_by_client(db, batch=batch, on_behalf_user=staff)
    assert batch.bl_validated_on_behalf_by_id == staff.id
    assert batch.bl_client_validated_by_id is None

    detail = (await _logs(db, "bl_client_validated"))[0].detail or ""
    assert "POUR LE COMPTE" in detail, "le repli doit être explicite dans la trace"


@pytest.mark.asyncio
async def test_neither_or_both_validators_is_refused(db):
    pl, batch, staff, _m, client = await _ctx(db)
    await w.generate_draft(db, pl=pl, batch=batch, leg=None, user=staff)

    with pytest.raises(w.ValidatorConflict):
        await w.validate_by_client(db, batch=batch)
    with pytest.raises(w.ValidatorConflict):
        await w.validate_by_client(db, batch=batch, client=client, on_behalf_user=staff)


# ───────────────────────── gel et régression ─────────────────────────


@pytest.mark.asyncio
async def test_editing_after_signature_is_refused(db):
    """🔴 Le gel. Après signature, la correction passe par une révision."""
    pl, batch, staff, master, client = await _ctx(db)
    await w.generate_draft(db, pl=pl, batch=batch, leg=None, user=staff)
    await w.validate_by_client(db, batch=batch, client=client)
    await w.sign_by_master(db, batch=batch, user=master)

    assert w.is_frozen(batch) is True
    with pytest.raises(w.BlFrozen):
        await w.invalidate_validation_on_edit(db, batch=batch, actor_name="ops")


@pytest.mark.asyncio
async def test_editing_after_validation_returns_to_draft(db):
    """🔴 La régression. Une validation porte sur un contenu, pas sur un dossier."""
    pl, batch, staff, _m, client = await _ctx(db)
    await w.generate_draft(db, pl=pl, batch=batch, leg=None, user=staff)
    await w.validate_by_client(db, batch=batch, client=client)

    changed = await w.invalidate_validation_on_edit(db, batch=batch, actor_name="portal:PL1")
    assert changed is True
    assert batch.bl_state == "draft"
    assert batch.bl_client_validated_by_id is None
    assert batch.bl_client_validated_at is None

    detail = (await _logs(db, "bl_validation_invalidated"))[0].detail or ""
    assert "annulée" in detail


@pytest.mark.asyncio
async def test_editing_a_plain_draft_changes_nothing(db):
    """Pas de validation à annuler : l'appel est inoffensif, et le dit."""
    pl, batch, staff, _m, _c = await _ctx(db)
    await w.generate_draft(db, pl=pl, batch=batch, leg=None, user=staff)
    assert await w.invalidate_validation_on_edit(db, batch=batch, actor_name="ops") is False
    assert batch.bl_state == "draft"


@pytest.mark.asyncio
async def test_a_second_draft_on_the_same_batch_is_refused(db):
    pl, batch, staff, _m, _c = await _ctx(db)
    await w.generate_draft(db, pl=pl, batch=batch, leg=None, user=staff)
    with pytest.raises(w.InvalidTransition):
        await w.generate_draft(db, pl=pl, batch=batch, leg=None, user=staff)


# ───────────────────────── intégrité de la signature ─────────────────────────


@pytest.mark.asyncio
async def test_altering_signed_content_is_detected(db):
    """Le hash n'a de valeur que si une altération le fait tomber."""
    pl, batch, staff, master, client = await _ctx(db)
    await w.generate_draft(db, pl=pl, batch=batch, leg=None, user=staff)
    await w.validate_by_client(db, batch=batch, client=client)
    await w.sign_by_master(db, batch=batch, user=master)

    assert w.signature_is_intact(batch) is True
    batch.pallet_count = 400  # falsification du contenu signé
    assert w.signature_is_intact(batch) is False


@pytest.mark.asyncio
async def test_final_issue_is_refused_on_altered_content(db):
    """Sans ce contrôle, le hash existerait sans rien protéger."""
    pl, batch, staff, master, client = await _ctx(db)
    await w.generate_draft(db, pl=pl, batch=batch, leg=None, user=staff)
    await w.validate_by_client(db, batch=batch, client=client)
    await w.sign_by_master(db, batch=batch, user=master)

    batch.description_of_goods = "Autre marchandise"
    with pytest.raises(w.BlFrozen):
        await w.issue_final(db, batch=batch, user=staff)
    assert batch.bl_state == "master_signed", "l'état ne doit pas avoir bougé"


def test_unsigned_is_not_the_same_as_intact():
    """⚠️ `None` et non `True` : on n'affirme pas une intégrité invérifiable."""
    from types import SimpleNamespace

    assert w.signature_is_intact(SimpleNamespace(bl_signature_hash=None)) is None


def test_signature_payload_is_stable_and_discriminant():
    """Deux contenus différents ne doivent jamais produire la même empreinte."""
    from types import SimpleNamespace

    def fake(**kw):
        base = dict.fromkeys(w.SIGNED_FIELDS)
        base.update(kw)
        return SimpleNamespace(**base)

    a, b = fake(pallet_count=4), fake(pallet_count=400)
    assert w.compute_signature_hash(a) != w.compute_signature_hash(b)
    # Stable : deux appels sur un contenu identique donnent le même hash.
    assert w.compute_signature_hash(a) == w.compute_signature_hash(fake(pallet_count=4))
    # L'ordre des champs est fixe, pas dépendant d'un dict.
    assert w.signature_payload(a).startswith("bl_number=")
