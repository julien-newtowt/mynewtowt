"""Révisions numérotées d'un connaissement — §4.1.

> « À partir de ``master_signed``, la correction ne passe plus par l'édition mais par
> une **révision numérotée** (``TUAW_…_R2``) qui annule explicitement la précédente,
> les deux restant tracées. »

⚠️ **Écart assumé par rapport au §4.2 de la spec.** Celle-ci plaçait
`bl_superseded_by_id` en FK vers `packing_list_batches`, donc une révision aurait créé
un **nouveau lot**. Or le lot porte la marchandise : `pdf_generator` somme
`pallet_count` / `weight_kg` sur `pl.batches`, l'export Excel les liste tous, le
stowage les localise. Un lot cloné aurait **doublé** tous ces totaux. Le lot reste donc
unique et c'est le **document** qui est versionné (`BlRevision`).

Ce que ces tests protègent :

1. **le document annulé survit intégralement** — numéro, empreinte, signataire **et
   contenu signé**. Sans le contenu, on saurait qu'un document a existé sans savoir ce
   qu'il disait ;
2. **le nouveau document repart de zéro** — ni validé, ni signé. Laisser les marques de
   l'ancien suggérerait une signature qui ne s'applique plus au contenu courant ;
3. **on ne révise pas un document non signé** — ce serait brûler un numéro pour rien ;
4. **la justification est obligatoire** ;
5. **le numéro de révision ne perturbe pas la séquence** — `_R2` ne doit pas être lu
   comme un numéro de séquence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.packing_list import BlRevision
from app.services import bl_workflow as w
from app.services.derived_override import JustificationRequired

MOTIF = "Poids corrigé après pesée à quai, écart de 120 kg constaté"


class _Req:
    def __init__(self, form: dict | None = None):
        self._form = form or {}
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="203.0.113.90")
        self.url = SimpleNamespace(path="/cargo")
        self.state = SimpleNamespace(csrf_token="x")
        self.cookies: dict[str, str] = {}
        self.query_params: dict[str, str] = {}

    async def form(self):
        return self._form


async def _signed(db, *, count=1):
    """Un lot dont le connaissement est signé, prêt à être révisé."""
    from app.models.client_account import ClientAccount
    from app.models.commercial import Client, Order
    from app.models.leg import Leg
    from app.models.packing_list import PackingList, PackingListBatch
    from app.models.port import Port
    from app.models.user import User
    from app.models.vessel import Vessel

    v = Vessel(name="Anemos", code="1")
    pol = Port(locode="FRFEC", name="Fécamp", country="FR")
    pod = Port(locode="BRSSO", name="Santos", country="BR")
    db.add_all([v, pol, pod])
    await db.flush()
    base = datetime(2026, 8, 10, tzinfo=UTC)
    leg = Leg(
        leg_code="1CFRBR6",
        vessel_id=v.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd_ref=base,
        eta_ref=base + timedelta(days=20),
        etd=base,
        eta=base + timedelta(days=20),
    )
    cl = Client(name="ACME", client_type="shipper")
    db.add_all([leg, cl])
    await db.flush()
    order = Order(reference="CMD-REV", client_id=cl.id, leg_id=leg.id)
    db.add(order)
    await db.flush()
    pl = PackingList(order_id=order.id, leg_id=leg.id)
    db.add(pl)
    await db.flush()
    ops = User(
        username="ops-rv", email="ops-rv@newtowt.test", hashed_password="x", role="operation"
    )
    master = User(
        username="cdt-rv", email="cdt-rv@newtowt.test", hashed_password="x", role="marins"
    )
    account = ClientAccount(email="c-rv@example.test", hashed_password="x", company_name="Belco")
    db.add_all([ops, master, account])
    await db.flush()
    batches = []
    for i in range(1, count + 1):
        b = PackingListBatch(
            packing_list_id=pl.id,
            batch_number=i,
            pallet_count=4,
            weight_kg=1200,
            consignee_name="Belco France",
            description_of_goods="Café vert",
        )
        db.add(b)
        await db.flush()
        await w.generate_draft(db, pl=pl, batch=b, leg=leg, user=ops)
        await w.validate_by_client(db, batch=b, client=account)
        await w.sign_by_master(db, batch=b, user=master)
        batches.append(b)
    return leg, pl, batches, ops, master, account


# ───────────────────────── numérotation ─────────────────────────


def test_the_revision_number_format_matches_the_spec():
    assert w.revision_number("TUAW_1CFRBR6_001", 2) == "TUAW_1CFRBR6_001_R2"


def test_the_base_number_survives_successive_revisions():
    """La révision 3 part du numéro d'origine, pas de `…_R2_R3`."""
    assert w.base_bl_number("TUAW_1CFRBR6_001_R2") == "TUAW_1CFRBR6_001"
    assert w.base_bl_number("TUAW_1CFRBR6_001") == "TUAW_1CFRBR6_001"


@pytest.mark.asyncio
async def test_successive_revisions_do_not_stack_suffixes(db):
    leg, _pl, batches, ops, master, account = await _signed(db)
    b = batches[0]

    n2 = await w.create_revision(db, batch=b, user=ops, reason=MOTIF)
    assert n2 == "TUAW_1CFRBR6_001_R2"

    # Pour réviser à nouveau il faut d'abord resigner : une révision repart en projet.
    await w.validate_by_client(db, batch=b, client=account)
    await w.sign_by_master(db, batch=b, user=master)
    n3 = await w.create_revision(db, batch=b, user=ops, reason=MOTIF)
    assert n3 == "TUAW_1CFRBR6_001_R3", "les suffixes se sont empilés"


@pytest.mark.asyncio
async def test_a_revision_number_does_not_disturb_the_sequence(db):
    """🔴 Deux exigences en une, trouvées par ce test.

    1. `…_001_R2` ne doit **pas** être lu comme un numéro de séquence (l'amorçage lit
       `_(\\d+)$`), sinon le prochain numéro du voyage serait faussé ;
    2. le numéro d'origine `…_001`, qui après révision ne vit plus **que dans
       l'archive**, doit rester compté comme consommé — il a circulé. Ne lire que les
       lots ferait réémettre un numéro déjà porté par un document remis à un tiers.
    """
    from app.models.packing_list import PackingListBatch
    from app.services.packing_list import _max_issued_suffix, assign_bl_number

    leg, pl, batches, ops, _m, _c = await _signed(db)
    await w.create_revision(db, batch=batches[0], user=ops, reason=MOTIF)

    # 001 n'est plus sur aucun lot, mais il est archivé : il compte toujours.
    assert await _max_issued_suffix(db, prefix="TUAW_1CFRBR6_") == 1

    fresh = PackingListBatch(packing_list_id=pl.id, batch_number=9, pallet_count=1)
    db.add(fresh)
    await db.flush()
    assert await assign_bl_number(db, pl, fresh, leg) == "TUAW_1CFRBR6_002"


# ───────────── 🔴 le document annulé survit intégralement ─────────────


@pytest.mark.asyncio
async def test_the_superseded_document_is_archived_in_full(db):
    """🔴 Sans le CONTENU signé, la trace dirait qu'un document a existé sans dire
    ce qu'il disait — inexploitable en contrôle."""
    leg, _pl, batches, ops, _m, _c = await _signed(db)
    b = batches[0]
    old_number = b.bl_number
    old_hash = b.bl_signature_hash
    old_signer = b.bl_signed_by_name
    old_payload = w.signature_payload(b)

    await w.create_revision(db, batch=b, user=ops, reason=MOTIF)

    rows = await w.revisions_for_batch(db, batch_id=b.id)
    assert len(rows) == 1
    r = rows[0]
    assert r.revision == 1
    assert r.bl_number == old_number
    assert r.signature_hash == old_hash
    assert r.signed_by_name == old_signer
    assert r.signed_content == old_payload
    assert r.reason == MOTIF
    assert r.superseded_by_user_id == ops.id


@pytest.mark.asyncio
async def test_the_archive_keeps_the_content_even_after_the_batch_changes(db):
    """L'instantané est figé : modifier le lot ensuite ne le réécrit pas."""
    leg, _pl, batches, ops, _m, _c = await _signed(db)
    b = batches[0]
    await w.create_revision(db, batch=b, user=ops, reason=MOTIF)
    archived = (await w.revisions_for_batch(db, batch_id=b.id))[0].signed_content

    b.weight_kg = 9999
    await db.flush()
    assert (await w.revisions_for_batch(db, batch_id=b.id))[0].signed_content == archived


# ───────────── le nouveau document repart de zéro ─────────────


@pytest.mark.asyncio
async def test_the_new_document_is_neither_validated_nor_signed(db):
    """🔴 Laisser les marques de l'ancien suggérerait une signature qui ne
    s'applique plus au contenu courant."""
    leg, _pl, batches, ops, _m, _c = await _signed(db)
    b = batches[0]
    await w.create_revision(db, batch=b, user=ops, reason=MOTIF)

    assert b.bl_state == w.DRAFT
    assert b.bl_signature_hash is None
    assert b.bl_signed_at is None and b.bl_signed_by_id is None and b.bl_signed_by_name is None
    assert b.bl_client_validated_at is None
    assert b.bl_client_validated_by_id is None
    assert b.bl_client_validated_by is None
    assert w.is_frozen(b) is False, "le lot est resté gelé après révision"


@pytest.mark.asyncio
async def test_the_revised_document_must_be_revalidated_and_resigned(db):
    """Le cycle complet reprend : c'est un document neuf."""
    leg, _pl, batches, ops, master, account = await _signed(db)
    b = batches[0]
    await w.create_revision(db, batch=b, user=ops, reason=MOTIF)

    # On ne peut pas signer directement : il faut repasser par la validation client.
    with pytest.raises(w.InvalidTransition):
        await w.sign_by_master(db, batch=b, user=master)

    await w.validate_by_client(db, batch=b, client=account)
    await w.sign_by_master(db, batch=b, user=master)
    assert b.bl_state == w.MASTER_SIGNED
    assert w.signature_is_intact(b) is True


@pytest.mark.asyncio
async def test_the_revision_is_traced_with_both_numbers_and_the_reason(db):
    from app.models.activity_log import ActivityLog

    leg, _pl, batches, ops, _m, _c = await _signed(db)
    b = batches[0]
    old = b.bl_number
    new = await w.create_revision(db, batch=b, user=ops, reason=MOTIF)

    logs = list(
        (await db.execute(select(ActivityLog).where(ActivityLog.action == "bl_revised")))
        .scalars()
        .all()
    )
    assert len(logs) == 1
    detail = logs[0].detail or ""
    assert old in detail and new in detail
    assert MOTIF in detail
    assert "revalidé" in detail and "resigné" in detail


# ───────────── refus ─────────────


@pytest.mark.asyncio
async def test_revising_an_unsigned_bl_is_refused(db):
    """Avant signature, la correction passe par l'édition — réviser brûlerait un
    numéro pour rien."""
    from app.models.commercial import Client, Order
    from app.models.packing_list import PackingList, PackingListBatch
    from app.models.user import User

    cl = Client(name="ACME", client_type="shipper")
    db.add(cl)
    await db.flush()
    order = Order(reference="CMD-NS", client_id=cl.id)
    db.add(order)
    await db.flush()
    pl = PackingList(order_id=order.id)
    db.add(pl)
    await db.flush()
    ops = User(
        username="ops-ns", email="ops-ns@newtowt.test", hashed_password="x", role="operation"
    )
    batch = PackingListBatch(packing_list_id=pl.id, batch_number=1, pallet_count=1)
    db.add_all([ops, batch])
    await db.flush()
    await w.generate_draft(db, pl=pl, batch=batch, leg=None, user=ops)

    with pytest.raises(w.InvalidTransition):
        await w.create_revision(db, batch=batch, user=ops, reason=MOTIF)
    assert await w.revisions_for_batch(db, batch_id=batch.id) == []
    assert batch.bl_revision == 1


@pytest.mark.asyncio
async def test_a_revision_without_a_reason_is_refused_before_any_write(db):
    leg, _pl, batches, ops, _m, _c = await _signed(db)
    b = batches[0]
    old_number = b.bl_number

    with pytest.raises(JustificationRequired):
        await w.create_revision(db, batch=b, user=ops, reason="")
    assert b.bl_number == old_number, "le numéro a changé malgré le refus"
    assert b.bl_state == w.MASTER_SIGNED
    assert await w.revisions_for_batch(db, batch_id=b.id) == []


@pytest.mark.asyncio
async def test_a_hollow_reason_is_refused(db):
    leg, _pl, batches, ops, _m, _c = await _signed(db)
    with pytest.raises(JustificationRequired):
        await w.create_revision(db, batch=batches[0], user=ops, reason="correction")


@pytest.mark.asyncio
async def test_the_same_revision_cannot_be_archived_twice(db):
    """La contrainte d'unicité empêche deux instantanés du même document annulé."""
    from sqlalchemy.exc import IntegrityError

    leg, _pl, batches, ops, _m, _c = await _signed(db)
    b = batches[0]
    await w.create_revision(db, batch=b, user=ops, reason=MOTIF)

    db.add(
        BlRevision(
            batch_id=b.id,
            revision=1,
            bl_number="TUAW_1CFRBR6_001",
            superseded_at=datetime.now(UTC),
            reason="doublon",
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


# ───────────── la route et l'écran ─────────────


@pytest.mark.asyncio
async def test_the_route_revises_and_redirects(db):
    from app.routers.cargo_packing_router import revise_bl

    leg, pl, batches, ops, _m, _c = await _signed(db)
    resp = await revise_bl(pl.id, batches[0].id, _Req({"reason": MOTIF}), db=db, user=ops)
    assert resp.status_code == 303
    assert batches[0].bl_number.endswith("_R2")


@pytest.mark.asyncio
async def test_the_route_returns_400_without_a_reason_and_409_when_unsigned(db):
    from app.routers.cargo_packing_router import revise_bl

    leg, pl, batches, ops, _m, _c = await _signed(db)
    with pytest.raises(HTTPException) as e:
        await revise_bl(pl.id, batches[0].id, _Req({"reason": ""}), db=db, user=ops)
    assert e.value.status_code == 400

    # Après une révision réussie, le document repart en projet : réviser à nouveau
    # doit renvoyer 409.
    await revise_bl(pl.id, batches[0].id, _Req({"reason": MOTIF}), db=db, user=ops)
    with pytest.raises(HTTPException) as e2:
        await revise_bl(pl.id, batches[0].id, _Req({"reason": MOTIF}), db=db, user=ops)
    assert e2.value.status_code == 409


@pytest.mark.asyncio
async def test_the_screen_shows_the_superseded_documents(db):
    """Un registre doit montrer ce qui a circulé, pas seulement l'état courant."""
    from app.routers.cargo_packing_router import packing_list_detail

    leg, pl, batches, ops, master, account = await _signed(db)
    b = batches[0]
    await w.create_revision(db, batch=b, user=ops, reason=MOTIF)
    await w.validate_by_client(db, batch=b, client=account)
    await w.sign_by_master(db, batch=b, user=master)

    resp = await packing_list_detail(pl.id, _Req(), db=db, user=ops)
    revs = resp.context["revisions_by_batch"][b.id]
    assert len(revs) == 1 and revs[0].reason == MOTIF


def test_the_screen_offers_the_revision_form_and_requires_a_reason():
    import re as _re

    from app.templating import templates

    raw = templates.env.loader.get_source(templates.env, "staff/cargo/packing_list_detail.html")[0]
    src = _re.sub(r"\{#.*?#\}", "", raw, flags=_re.DOTALL)
    assert "/bl/revise" in src
    assert 'name="reason" required' in src
    # L'écran doit annoncer la conséquence : revalidation et nouvelle signature.
    assert "resigné par le commandant" in src


def test_the_dead_column_is_gone():
    """`bl_superseded_by_id` n'a plus d'objet dans ce modèle.

    La laisser en place serait exactement le piège d'une colonne qui a l'air de
    vouloir dire quelque chose et que personne ne lit.
    """
    from app.models.packing_list import PackingListBatch

    assert not hasattr(PackingListBatch, "bl_superseded_by_id")
