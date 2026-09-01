"""L'émission d'un BL est une écriture : `POST` + `cargo:M`, jamais un `GET`.

Défaut corrigé (SPEC_WORKFLOW_BILL_OF_LADING §4.4). L'émission passait par un
`GET` en permission `cargo:C` qui **écrivait en base** :

- un `GET` qui écrit s'exécute sur un **préchargement de lien**, un scan de
  sécurité ou un passage de crawler ⇒ des connaissements émis en série, des
  numéros consommés, sans que personne ne l'ait demandé ;
- `cargo:C` est la permission de **consultation** : elle autorisait `technique`,
  `data_analyst` et **`marins`** à émettre un titre de propriété.

La consultation reste en `GET` — elle est légitime et nécessaire — mais elle
**n'écrit plus rien**.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class _Req:
    headers: dict[str, str] = {}
    client = SimpleNamespace(host="203.0.113.12")
    url = SimpleNamespace(path="/cargo")
    state = SimpleNamespace(csrf_token="x")


async def _ctx(db):
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
    batch = PackingListBatch(packing_list_id=pl.id, batch_number=1, pallet_count=4)
    db.add(batch)
    user = User(
        username="ops-em", email="ops-em@newtowt.test", hashed_password="x", role="operation"
    )
    db.add(user)
    await db.flush()
    return pl, batch, user


# ───────────────── la consultation n'écrit plus ─────────────────


@pytest.mark.asyncio
async def test_get_no_longer_creates_a_bl(db):
    """🔴 Le cœur du correctif : consulter ne doit pas émettre.

    Avant, cet appel attribuait un numéro. Un préchargement de lien suffisait donc
    à consommer un numéro de connaissement.
    """
    from app.models.packing_list import PackingListBatch
    from app.routers.cargo_packing_router import batch_bill_of_lading

    pl, batch, user = await _ctx(db)
    assert batch.bl_number is None

    with pytest.raises(HTTPException) as e:
        await batch_bill_of_lading(pl.id, batch.id, db=db, user=user)
    assert e.value.status_code == 404
    assert "aucun BL" in str(e.value.detail)

    fresh = await db.get(PackingListBatch, batch.id)
    assert fresh.bl_number is None, "la consultation a émis un BL"
    assert fresh.bl_state is None


# ───────────────── la génération est un POST protégé ─────────────────


def test_generation_is_declared_as_post_with_modify_permission():
    """Le contrat de la route, figé : POST et `cargo:M`.

    Un test de signature plutôt que d'appel : c'est la DÉCLARATION qui protège —
    repasser cette route en `GET` ou en `cargo:C` doit faire échouer la suite.
    """
    from app.routers import cargo_packing_router as r

    route = next(
        rt
        for rt in r.router.routes
        if getattr(rt, "path", "").endswith("/batches/{batch_id}/bl/draft")
    )
    assert route.methods == {"POST"}, f"méthodes déclarées : {route.methods}"

    # La dépendance de permission doit exiger le niveau M (modification).
    src = inspect.getsource(r.generate_bl_draft)
    assert 'require_permission("cargo", "M")' in src

    # Et la consultation doit rester en lecture, sans écriture.
    read = next(
        rt
        for rt in r.router.routes
        if getattr(rt, "path", "").endswith("/batches/{batch_id}/bl.pdf")
    )
    assert read.methods == {"GET"}
    read_src = inspect.getsource(r.batch_bill_of_lading)
    assert "assign_bl_number" not in read_src, "la consultation écrit encore"


@pytest.mark.asyncio
async def test_post_generates_the_draft_and_traces_it(db):
    from sqlalchemy import select

    from app.models.activity_log import ActivityLog
    from app.models.packing_list import PackingListBatch
    from app.routers.cargo_packing_router import generate_bl_draft

    pl, batch, user = await _ctx(db)
    resp = await generate_bl_draft(pl.id, batch.id, _Req(), db=db, user=user)
    assert resp.status_code == 303  # redirige vers la consultation

    fresh = await db.get(PackingListBatch, batch.id)
    assert fresh.bl_number, "aucun numéro attribué"
    assert fresh.bl_state == "draft"
    # Comble le trou d'audit : on sait désormais QUI a émis.
    assert fresh.bl_issued_by_id == user.id

    logs = list(
        (await db.execute(select(ActivityLog).where(ActivityLog.action == "bl_draft_generated")))
        .scalars()
        .all()
    )
    assert len(logs) == 1


@pytest.mark.asyncio
async def test_generating_twice_is_refused(db):
    """Un second draft sur le même lot passerait par une révision, pas par un rappel."""
    from app.routers.cargo_packing_router import generate_bl_draft

    pl, batch, user = await _ctx(db)
    await generate_bl_draft(pl.id, batch.id, _Req(), db=db, user=user)
    with pytest.raises(HTTPException) as e:
        await generate_bl_draft(pl.id, batch.id, _Req(), db=db, user=user)
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_generation_is_refused_on_a_locked_packing_list(db):
    from app.routers.cargo_packing_router import generate_bl_draft

    pl, batch, user = await _ctx(db)
    pl.status = "locked"
    await db.flush()
    with pytest.raises(HTTPException) as e:
        await generate_bl_draft(pl.id, batch.id, _Req(), db=db, user=user)
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_reading_works_once_generated(db, monkeypatch):
    """⚠️ Garde anti-sur-correction : la consultation doit rester possible.

    Un correctif qui casserait la lecture serait pire que le défaut corrigé.

    Le rendu WeasyPrint est monkeypatché (motif déjà utilisé par
    `test_carnet_conditions`) : ce test porte sur le **contrôle d'accès au
    document**, pas sur la mise en page. `importorskip` ne suffirait pas ici —
    il ne rattrape qu'`ImportError`, jamais l'`OSError` de GTK.
    """
    from types import SimpleNamespace

    from app.routers.cargo_packing_router import batch_bill_of_lading, generate_bl_draft

    pl, batch, user = await _ctx(db)
    await generate_bl_draft(pl.id, batch.id, _Req(), db=db, user=user)

    seen: dict[str, object] = {}

    def _fake_render(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(pdf=b"%PDF-1.4 fake", mime="application/pdf", filename="bl.pdf")

    monkeypatch.setattr(
        "app.routers.cargo_packing_router.render_bill_of_lading_from_pl", _fake_render
    )
    resp = await batch_bill_of_lading(pl.id, batch.id, db=db, user=user)
    assert resp.status_code == 200
    assert resp.body == b"%PDF-1.4 fake"
    # Le numéro rendu est celui déjà émis : la lecture n'en fabrique pas un nouveau.
    assert seen["bl_number"] == batch.bl_number


def test_the_template_uses_a_form_to_generate_and_a_link_to_read():
    """Le gabarit doit suivre : un lien ne peut plus déclencher une écriture."""
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/cargo/packing_list_detail.html")[0]
    assert 'action="/cargo/packing-lists/{{ pl.id }}/batches/{{ b.id }}/bl/draft"' in src
    assert 'method="post"' in src
    # La consultation reste un lien, mais seulement quand un BL existe.
    assert "{% if b.bl_number %}" in src
