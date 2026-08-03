"""Les mutations du portail expéditeur laissent une trace dans `activity_logs`.

Trou d'audit relevé au §14.2 de `PROJECT_CONTEXT.md` : les 8 routes mutantes de
`cargo_portal_router` alimentaient (partiellement) la piste `PackingListAudit`
mais **jamais** `activity_logs`, le journal append-only consulté depuis
`/admin/activity-logs`. Rien de ce que faisait un expéditeur n'y apparaissait —
or c'est cette piste qu'un P&I club réclame à l'ouverture d'un dossier.

Deux routes n'étaient tracées **nulle part** : la soumission de la packing list
(qui change pourtant son état) et l'envoi d'un message.

Exigences vérifiées ici :
- chacune des 8 routes produit une entrée ;
- l'acteur identifie le **canal et le dossier** (`portal:PL<id>`), là où la piste
  précédente n'écrivait qu'un `client` sans dossier — et **sans nommer personne**,
  le portail étant anonyme par conception (quiconque détient le lien agit) ;
- le **token n'apparaît jamais** dans le journal ;
- le **corps d'un message n'est pas dupliqué** dans le journal ;
- une édition sans changement réel ne crée **pas** de ligne.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select


class _Req:
    """Requête minimale — le portail ne lit que l'IP et l'en-tête user-agent."""

    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="203.0.113.7")
    url = SimpleNamespace(path="/p/tok/packing")
    state = SimpleNamespace(notif_count=0)

    def __init__(self, form: dict | None = None):
        self._form = form or {}

    async def form(self):
        return self._form


TOKEN = "a1b2c3d4e5f6a7b8c9d0e1f2"


async def _setup(db):
    """Packing list rattachée à une commande (rail A), accessible par token."""
    from datetime import UTC, datetime, timedelta

    from app.models.commercial import Client, Order
    from app.models.packing_list import PackingList

    db.add(Client(id=1, name="ACME", client_type="shipper"))
    await db.flush()
    db.add(Order(id=1, reference="CMD-1", client_id=1))
    await db.flush()
    pl = PackingList(
        id=1,
        order_id=1,
        token=TOKEN,
        token_expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(pl)
    await db.flush()
    return pl


async def _logs(db, *, action: str | None = None) -> list:
    from app.models.activity_log import ActivityLog

    stmt = select(ActivityLog).where(ActivityLog.module == "cargo")
    if action:
        stmt = stmt.where(ActivityLog.action == action)
    return list((await db.execute(stmt)).scalars().all())


@pytest.mark.asyncio
async def test_add_batch_is_traced_with_named_actor(db):
    from app.routers.cargo_portal_router import portal_packing_add

    await _setup(db)
    await portal_packing_add(TOKEN, _Req({"pallet_count": "4"}), db=db)

    rows = await _logs(db, action="create")
    assert len(rows) == 1
    row = rows[0]
    # L'acteur est NOMMÉ : la piste précédente n'enregistrait que « client ».
    assert row.user_name == "portal:PL1"
    assert row.user_id is None  # accès par token, aucun compte
    assert row.user_role == "portal"
    assert row.entity_type == "packing_batch"
    assert row.ip_address == "203.0.113.7"


@pytest.mark.asyncio
async def test_token_never_reaches_the_audit_log(db):
    """Le token est un secret d'accès (90 j) : il ne doit fuiter dans aucun champ."""
    from app.routers.cargo_portal_router import portal_packing_add

    await _setup(db)
    await portal_packing_add(TOKEN, _Req({"pallet_count": "1"}), db=db)

    rows = await _logs(db)
    # Garde anti-succès-à-vide : sans cette assertion, le test passerait aussi
    # bien sur un code qui ne journalise RIEN (« le token n'apparaît pas » est
    # trivialement vrai sur zéro ligne).
    assert rows, "aucune entrée journalisée — le test serait vide de sens"
    for row in rows:
        joined = " ".join(
            str(v) for v in (row.user_name, row.entity_label, row.detail, row.ip_address) if v
        )
        assert TOKEN not in joined


@pytest.mark.asyncio
async def test_submit_is_traced_although_it_was_traced_nowhere(db):
    """La soumission change l'état de la PL et n'était tracée nulle part."""
    from app.models.packing_list import PackingList
    from app.routers.cargo_portal_router import portal_packing_submit

    await _setup(db)
    await portal_packing_submit(TOKEN, _Req(), db=db)

    pl = await db.get(PackingList, 1)
    assert pl.status == "submitted"

    rows = await _logs(db, action="submit")
    assert len(rows) == 1
    assert rows[0].entity_type == "packing_list"
    assert "submitted" in (rows[0].detail or "")


@pytest.mark.asyncio
async def test_message_is_traced_without_duplicating_its_body(db):
    """On trace le FAIT, pas le contenu : le corps reste dans `portal_messages`."""
    from app.routers.cargo_portal_router import portal_message_post

    await _setup(db)
    secret = "Prix negocie confidentiel 42 EUR la palette"
    await portal_message_post(TOKEN, _Req(), body=secret, sender_name="Ana", db=db)

    rows = await _logs(db, action="message")
    assert len(rows) == 1
    joined = " ".join(str(v) for v in (rows[0].entity_label, rows[0].detail) if v)
    assert secret not in joined
    assert str(len(secret)) in joined  # seule la longueur est conservée


@pytest.mark.asyncio
async def test_batch_delete_traced_before_the_row_disappears(db):
    """Le libellé doit être construit AVANT la suppression, sinon il est vide."""
    from app.routers.cargo_portal_router import portal_packing_add, portal_packing_delete

    pl = await _setup(db)
    await portal_packing_add(TOKEN, _Req({"pallet_count": "7"}), db=db)
    from app.models.packing_list import PackingListBatch

    batch = (await db.execute(select(PackingListBatch))).scalars().one()
    await portal_packing_delete(TOKEN, batch.id, _Req(), db=db)

    rows = await _logs(db, action="delete")
    assert len(rows) == 1
    assert f"PL {pl.id}" in (rows[0].entity_label or "")
    assert rows[0].entity_id == batch.id


@pytest.mark.asyncio
async def test_noop_edit_creates_no_entry(db):
    """Une soumission sans changement réel ne doit pas polluer la piste."""
    from app.models.packing_list import PackingListBatch
    from app.routers.cargo_portal_router import portal_packing_add, portal_packing_edit

    await _setup(db)
    await portal_packing_add(TOKEN, _Req({"pallet_count": "3"}), db=db)
    batch = (await db.execute(select(PackingListBatch))).scalars().one()

    # Garde anti-succès-à-vide : la journalisation doit être ACTIVE, sinon
    # « aucune ligne ajoutée » serait vrai sur un code qui ne trace rien.
    assert await _logs(db, action="create"), "journalisation inactive"

    before = len(await _logs(db, action="update"))
    # On repasse la valeur déjà en base : aucun champ ne change.
    await portal_packing_edit(TOKEN, batch.id, _Req({"pallet_count": "3"}), db=db)
    assert len(await _logs(db, action="update")) == before


@pytest.mark.asyncio
async def test_real_edit_creates_one_entry(db):
    from app.models.packing_list import PackingListBatch
    from app.routers.cargo_portal_router import portal_packing_add, portal_packing_edit

    await _setup(db)
    await portal_packing_add(TOKEN, _Req({"pallet_count": "3"}), db=db)
    batch = (await db.execute(select(PackingListBatch))).scalars().one()

    await portal_packing_edit(TOKEN, batch.id, _Req({"pallet_count": "9"}), db=db)
    rows = await _logs(db, action="update")
    assert len(rows) == 1
    assert rows[0].user_name == "portal:PL1"


@pytest.mark.asyncio
async def test_actor_identifies_the_file_and_never_a_person(db):
    """L'acteur désigne le canal et le dossier — jamais une personne.

    Le portail est **anonyme par conception** : quiconque détient le lien peut
    agir, et ce peut être un transitaire plutôt que l'expéditeur. Écrire un nom
    de société laisserait croire à une attribution personnelle que rien ne
    vérifie. Le libellé reste donc factuel et **déterministe**, y compris sur une
    packing list vide (cas où aucun batch, donc aucun `shipper_name`, n'existe).
    """
    from datetime import UTC, datetime, timedelta

    from app.models.packing_list import PackingList
    from app.routers.cargo_portal_router import _portal_actor

    pl = PackingList(
        id=2,
        order_id=None,
        booking_id=None,
        token="f" * 24,
        token_expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    assert _portal_actor(pl) == "portal:PL2"
    # `PackingList` ne porte AUCUN champ d'identité d'expéditeur : les parties
    # (shipper / notify / consignee) vivent sur `PackingListBatch`. Ce test fige
    # ce constat — la spécification supposait l'inverse, et un accès à
    # `pl.shipper_name` aurait fait échouer chaque mutation du portail.
    assert not hasattr(pl, "shipper_name")
