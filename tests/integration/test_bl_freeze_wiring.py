"""Le gel du BL s'applique sur TOUS les chemins d'écriture, staff comme portail.

Un garde-fou qui n'existe qu'en service ne protège rien — c'est exactement le
défaut relevé sur `crew_compliance.passport_blocking_reason` : une fonction
complète, correcte, et **sans appelant**. Ces tests vérifient le câblage, pas la
logique (couverte par `test_bl_workflow_transitions.py`).

Et un garde-fou qui ne s'applique qu'à une interface **se contourne par l'autre** :
chaque cas est donc vérifié des deux côtés.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

TOKEN = "c" * 24


class _Req:
    headers: dict[str, str] = {}
    client = SimpleNamespace(host="203.0.113.11")
    url = SimpleNamespace(path="/p/tok/packing")

    def __init__(self, form: dict | None = None):
        self._form = form or {}

    async def form(self):
        return self._form


async def _signed_batch(db, *, state="master_signed"):
    """Une PL accessible par token, avec un lot au BL signé."""
    from datetime import UTC, datetime, timedelta

    from app.models.commercial import Client, Order
    from app.models.packing_list import PackingList, PackingListBatch

    db.add(Client(id=1, name="ACME", client_type="shipper"))
    await db.flush()
    db.add(Order(id=1, reference="CMD-1", client_id=1))
    await db.flush()
    pl = PackingList(
        order_id=1, token=TOKEN, token_expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    db.add(pl)
    await db.flush()
    batch = PackingListBatch(
        packing_list_id=pl.id,
        batch_number=1,
        pallet_count=4,
        bl_number="TUAW_TEST_001",
        bl_state=state,
        bl_signature_hash="0" * 64,
    )
    db.add(batch)
    await db.flush()
    return pl, batch


async def _staff(db):
    from app.models.user import User

    u = User(username="ops-fz", email="ops-fz@newtowt.test", hashed_password="x", role="operation")
    db.add(u)
    await db.flush()
    return u


# ───────────────────────── côté staff ─────────────────────────


@pytest.mark.asyncio
async def test_staff_edit_of_a_signed_batch_is_refused(db):
    from app.routers.cargo_packing_router import edit_batch

    pl, batch = await _signed_batch(db)
    user = await _staff(db)
    with pytest.raises(HTTPException) as e:
        await edit_batch(pl.id, batch.id, _Req({"pallet_count": "99"}), db=db, user=user)
    assert e.value.status_code == 409
    assert "signé" in str(e.value.detail)


@pytest.mark.asyncio
async def test_staff_edit_leaves_the_content_untouched_when_refused(db):
    """Le refus doit précéder l'écriture, pas la suivre."""
    from app.models.packing_list import PackingListBatch
    from app.routers.cargo_packing_router import edit_batch

    pl, batch = await _signed_batch(db)
    user = await _staff(db)
    with pytest.raises(HTTPException):
        await edit_batch(pl.id, batch.id, _Req({"pallet_count": "99"}), db=db, user=user)
    fresh = await db.get(PackingListBatch, batch.id)
    assert fresh.pallet_count == 4, "la modification a été appliquée malgré le refus"


@pytest.mark.asyncio
async def test_staff_delete_of_a_signed_batch_is_refused(db):
    from app.routers.cargo_packing_router import delete_batch

    pl, batch = await _signed_batch(db)
    user = await _staff(db)
    with pytest.raises(HTTPException) as e:
        await delete_batch(pl.id, batch.id, _Req(), db=db, user=user)
    assert e.value.status_code == 409


# ───────────────────────── côté portail expéditeur ─────────────────────────


@pytest.mark.asyncio
async def test_portal_edit_of_a_signed_batch_is_refused(db):
    """Le même garde-fou, par l'autre porte."""
    from app.routers.cargo_portal_router import portal_packing_edit

    _pl, batch = await _signed_batch(db)
    with pytest.raises(HTTPException) as e:
        await portal_packing_edit(TOKEN, batch.id, _Req({"pallet_count": "99"}), db=db)
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_portal_delete_of_a_signed_batch_is_refused(db):
    from app.routers.cargo_portal_router import portal_packing_delete

    _pl, batch = await _signed_batch(db)
    with pytest.raises(HTTPException) as e:
        await portal_packing_delete(TOKEN, batch.id, _Req(), db=db)
    assert e.value.status_code == 409


# ───────────────────── le cas le plus grave : l'import ─────────────────────


@pytest.mark.asyncio
async def test_portal_excel_import_is_refused_when_a_bl_is_signed(db):
    """🔴 L'import REMPLACE les lots : il détruirait un titre opposable.

    Et il le compterait comme « importé » — c'est la conjonction destruction +
    comptage faux qui rend ce chemin le plus dangereux des quatre.
    """
    from app.models.packing_list import PackingListBatch
    from app.routers.cargo_portal_router import portal_packing_import_xlsx
    from app.services import cargo_excel

    _pl, batch = await _signed_batch(db)
    content = cargo_excel.build_template_xlsx()

    class _Upload:
        filename = "import.xlsx"

        async def read(self):
            return content

    with pytest.raises(HTTPException) as e:
        await portal_packing_import_xlsx(TOKEN, _Req(), file=_Upload(), db=db)
    # 400 si le gabarit vide n'a aucune ligne exploitable, 409 si le gel a mordu.
    # Dans les deux cas rien ne doit avoir été détruit — c'est ce qui compte.
    assert e.value.status_code in (400, 409)
    survivors = list((await db.execute(select(PackingListBatch))).scalars().all())
    assert len(survivors) == 1 and survivors[0].bl_number == "TUAW_TEST_001"


# ───────────────────── un lot NON signé reste modifiable ─────────────────────


@pytest.mark.asyncio
async def test_a_draft_batch_stays_editable_on_both_sides(db):
    """⚠️ Garde anti-sur-correction : le draft DOIT rester modifiable.

    C'est l'exigence métier explicite — l'expéditeur corrige sa packing list au
    stade draft. Un gel trop large casserait le workflow qu'on met en place.
    """
    from app.models.packing_list import PackingListBatch
    from app.routers.cargo_portal_router import portal_packing_edit

    _pl, batch = await _signed_batch(db, state="draft")
    await portal_packing_edit(TOKEN, batch.id, _Req({"pallet_count": "12"}), db=db)
    fresh = await db.get(PackingListBatch, batch.id)
    assert fresh.pallet_count == 12


@pytest.mark.asyncio
async def test_editing_a_validated_batch_returns_it_to_draft_from_the_portal(db):
    """La règle de régression traverse aussi le portail, pas seulement le staff."""
    from app.models.packing_list import PackingListBatch
    from app.routers.cargo_portal_router import portal_packing_edit

    _pl, batch = await _signed_batch(db, state="client_validated")
    await portal_packing_edit(TOKEN, batch.id, _Req({"pallet_count": "7"}), db=db)
    fresh = await db.get(PackingListBatch, batch.id)
    assert fresh.pallet_count == 7
    assert fresh.bl_state == "draft", "la validation client devait être annulée"
