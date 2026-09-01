"""Écran commandant — signature d'un connaissement, unitaire et groupée.

Cf. `docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md` §4.1 et §5.2 (« Donner le
choix au commandant de tout signer ou signer un BL en particulier »).

Ce que ces tests protègent :

1. **le rattachement au leg** — la requête doit suivre *exactement* la règle
   COM-11 (leg épinglé sur la packing list, repli sur order/booking). Une autre
   convention ferait apparaître ou disparaître des connaissements de l'écran du
   commandant selon la requête, ce qui est inacceptable pour un registre ;
2. **la signature groupée ne ment pas** — un lot non signable est écarté *avec sa
   raison*, sans faire échouer les autres, et le compte rendu le dit ;
3. **on ne signe que ce qui est validé par le client** — un draft n'est pas
   signable, et l'écran l'explique au lieu de le masquer ;
4. **la permission** — signer est `captain:M`, consulter `captain:C`.
"""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import bl_workflow as w


class _Req:
    """Requête minimale : `form()` (multi-valeurs), headers, client, query params."""

    def __init__(self, form: dict | None = None, query: dict | None = None):
        self._form = form or {}
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="198.51.100.7")
        self.url = SimpleNamespace(path="/captain/bl")
        self.query_params = query or {}
        self.state = SimpleNamespace(csrf_token="x")
        self.cookies: dict[str, str] = {}  # requis par TemplateResponse(request, …)

    async def form(self):
        outer = self._form

        class _Form(dict):
            def getlist(self, key):
                v = outer.get(key, [])
                return v if isinstance(v, list) else [v]

        f = _Form(outer)
        return f


async def _leg(db, *, code="1CFRBR6"):
    """Un leg complet. `departure_port_id` / `arrival_port_id` sont NOT NULL :
    les ports ne sont pas décoratifs dans cette fixture."""
    from app.models.leg import Leg
    from app.models.port import Port
    from app.models.vessel import Vessel

    v = Vessel(name=f"Navire {code}", code=code[:1])
    pol = Port(locode=f"FR{code[:3]}", name="Fécamp", country="FR")
    pod = Port(locode=f"BR{code[:3]}", name="Santos", country="BR")
    db.add_all([v, pol, pod])
    await db.flush()
    base = datetime.now(UTC)
    leg = Leg(
        leg_code=code,
        vessel_id=v.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd_ref=base - timedelta(days=2),
        eta_ref=base + timedelta(days=10),
        etd=base - timedelta(days=2),
        eta=base + timedelta(days=10),
    )
    db.add(leg)
    await db.flush()
    return leg


async def _users(db):
    from app.models.user import User

    ops = User(
        username="ops-cap", email="ops-cap@newtowt.test", hashed_password="x", role="operation"
    )
    master = User(
        username="cdt-cap", email="cdt-cap@newtowt.test", hashed_password="x", role="marins"
    )
    db.add_all([ops, master])
    await db.flush()
    return ops, master


async def _client(db):
    from app.models.client_account import ClientAccount

    c = ClientAccount(email="cli-cap@example.test", hashed_password="x", company_name="Belco")
    db.add(c)
    await db.flush()
    return c


async def _pl_with_batches(db, leg, *, count=2, pin_leg=True):
    """Une packing list rattachée au leg + `count` lots."""
    from app.models.commercial import Client, Order
    from app.models.packing_list import PackingList, PackingListBatch

    cl = Client(name="ACME", client_type="shipper")
    db.add(cl)
    await db.flush()
    order = Order(reference=f"CMD-{leg.id}", client_id=cl.id, leg_id=None if pin_leg else leg.id)
    db.add(order)
    await db.flush()
    pl = PackingList(order_id=order.id, leg_id=leg.id if pin_leg else None)
    db.add(pl)
    await db.flush()
    batches = []
    for i in range(1, count + 1):
        b = PackingListBatch(
            packing_list_id=pl.id,
            batch_number=i,
            pallet_count=4,
            shipper_name="Belco",
            consignee_name="Belco France",
            description_of_goods="Café vert",
        )
        db.add(b)
        batches.append(b)
    await db.flush()
    return pl, batches


async def _to_validated(db, pl, batch, leg, ops, client):
    await w.generate_draft(db, pl=pl, batch=batch, leg=leg, user=ops)
    await w.validate_by_client(db, batch=batch, client=client)


# ───────────────────────── rattachement au leg ─────────────────────────


@pytest.mark.asyncio
async def test_batches_are_found_via_the_pinned_leg(db):
    leg = await _leg(db)
    ops, _m = await _users(db)
    client = await _client(db)
    pl, batches = await _pl_with_batches(db, leg, count=2, pin_leg=True)
    for b in batches:
        await _to_validated(db, pl, b, leg, ops, client)

    found = await w.batches_for_leg(db, leg_id=leg.id, states=(w.CLIENT_VALIDATED,))
    assert {b.id for b in found} == {b.id for b in batches}


@pytest.mark.asyncio
async def test_batches_are_found_via_the_order_fallback(db):
    """🔴 Les PL héritées n'ont pas de `leg_id` : le repli COM-11 doit jouer.

    Sans ce repli, les connaissements des packing lists anciennes seraient
    invisibles à bord — le commandant ne signerait jamais.
    """
    leg = await _leg(db)
    ops, _m = await _users(db)
    client = await _client(db)
    pl, batches = await _pl_with_batches(db, leg, count=1, pin_leg=False)
    assert pl.leg_id is None, "ce test doit porter sur le repli, pas sur le leg épinglé"
    await _to_validated(db, pl, batches[0], leg, ops, client)

    found = await w.batches_for_leg(db, leg_id=leg.id, states=(w.CLIENT_VALIDATED,))
    assert [b.id for b in found] == [batches[0].id]


@pytest.mark.asyncio
async def test_a_batch_without_a_bl_is_not_listed(db):
    """Sans BL émis, il n'y a rien à signer."""
    leg = await _leg(db)
    await _users(db)
    await _pl_with_batches(db, leg, count=1)
    assert await w.batches_for_leg(db, leg_id=leg.id) == []


@pytest.mark.asyncio
async def test_another_legs_bls_are_not_listed(db):
    """⚠️ Le commandant ne doit pas voir — ni pouvoir signer — un autre voyage."""
    leg_a = await _leg(db, code="1CFRBR6")
    leg_b = await _leg(db, code="2AFRBR6")
    ops, _m = await _users(db)
    client = await _client(db)
    pl, batches = await _pl_with_batches(db, leg_a, count=1)
    await _to_validated(db, pl, batches[0], leg_a, ops, client)

    assert await w.batches_for_leg(db, leg_id=leg_b.id) == []


# ───────────────────────── signature unitaire ─────────────────────────


@pytest.mark.asyncio
async def test_signing_one_bl_freezes_it(db):
    from app.routers.captain_router import captain_sign_bl

    leg = await _leg(db)
    ops, master = await _users(db)
    client = await _client(db)
    pl, batches = await _pl_with_batches(db, leg, count=1)
    b = batches[0]
    await _to_validated(db, pl, b, leg, ops, client)

    resp = await captain_sign_bl(b.id, _Req(query={"leg_id": str(leg.id)}), db=db, user=master)
    assert resp.status_code == 303
    assert b.bl_state == w.MASTER_SIGNED
    assert b.bl_signed_by_name and len(b.bl_signature_hash) == 64
    assert w.is_frozen(b) is True


@pytest.mark.asyncio
async def test_signing_an_unvalidated_draft_is_refused(db):
    """🔴 On ne signe pas ce que le client n'a pas validé."""
    from app.routers.captain_router import captain_sign_bl

    leg = await _leg(db)
    ops, master = await _users(db)
    pl, batches = await _pl_with_batches(db, leg, count=1)
    b = batches[0]
    await w.generate_draft(db, pl=pl, batch=b, leg=leg, user=ops)  # draft, pas validé

    with pytest.raises(HTTPException) as e:
        await captain_sign_bl(b.id, _Req(), db=db, user=master)
    assert e.value.status_code == 409
    assert b.bl_state == w.DRAFT, "l'état ne doit pas avoir bougé"


@pytest.mark.asyncio
async def test_signing_twice_is_refused(db):
    from app.routers.captain_router import captain_sign_bl

    leg = await _leg(db)
    ops, master = await _users(db)
    client = await _client(db)
    pl, batches = await _pl_with_batches(db, leg, count=1)
    b = batches[0]
    await _to_validated(db, pl, b, leg, ops, client)

    await captain_sign_bl(b.id, _Req(), db=db, user=master)
    first_hash = b.bl_signature_hash
    with pytest.raises(HTTPException) as e:
        await captain_sign_bl(b.id, _Req(), db=db, user=master)
    assert e.value.status_code == 409
    assert b.bl_signature_hash == first_hash, "la seconde tentative a réécrit l'empreinte"


# ───────────────────────── signature groupée ─────────────────────────


@pytest.mark.asyncio
async def test_bulk_signing_signs_every_selected_bl(db):
    from app.routers.captain_router import captain_sign_selected_bls

    leg = await _leg(db)
    ops, master = await _users(db)
    client = await _client(db)
    pl, batches = await _pl_with_batches(db, leg, count=3)
    for b in batches:
        await _to_validated(db, pl, b, leg, ops, client)

    form = {"batch_ids": [str(b.id) for b in batches], "leg_id": str(leg.id)}
    resp = await captain_sign_selected_bls(_Req(form=form), db=db, user=master)
    assert resp.status_code == 303
    assert all(b.bl_state == w.MASTER_SIGNED for b in batches)
    assert "signed=3" in resp.headers["location"]
    assert "skipped=0" in resp.headers["location"]


@pytest.mark.asyncio
async def test_bulk_signing_reports_what_it_could_not_sign(db):
    """🔴 Le cœur du mode groupé : ne jamais annoncer un succès complet à tort.

    Un lot revenu à `draft` entre l'affichage de l'écran et l'envoi du formulaire
    est écarté — mais les autres sont signés, et le compte rendu distingue les deux.
    """
    from app.routers.captain_router import captain_sign_selected_bls

    leg = await _leg(db)
    ops, master = await _users(db)
    client = await _client(db)
    pl, batches = await _pl_with_batches(db, leg, count=3)
    for b in batches:
        await _to_validated(db, pl, b, leg, ops, client)
    # Le deuxième régresse (modification de la packing list entre-temps).
    await w.invalidate_validation_on_edit(db, batch=batches[1], actor_name="ops")
    assert batches[1].bl_state == w.DRAFT

    form = {"batch_ids": [str(b.id) for b in batches], "leg_id": str(leg.id)}
    resp = await captain_sign_selected_bls(_Req(form=form), db=db, user=master)

    assert batches[0].bl_state == w.MASTER_SIGNED
    assert batches[2].bl_state == w.MASTER_SIGNED, "un écart a fait échouer les suivants"
    assert batches[1].bl_state == w.DRAFT, "un lot non validé a été signé"
    loc = resp.headers["location"]
    assert "signed=2" in loc and "skipped=1" in loc


@pytest.mark.asyncio
async def test_the_bulk_result_carries_the_reason_for_each_skip(db):
    """Le compte rendu de service porte la RAISON, pas seulement un nombre."""
    leg = await _leg(db)
    ops, master = await _users(db)
    client = await _client(db)
    pl, batches = await _pl_with_batches(db, leg, count=2)
    await _to_validated(db, pl, batches[0], leg, ops, client)
    await w.generate_draft(db, pl=pl, batch=batches[1], leg=leg, user=ops)  # reste draft

    result = await w.sign_many(db, batches=batches, user=master)
    assert len(result.signed) == 1
    assert len(result.skipped) == 1
    label, reason = result.skipped[0]
    assert label == batches[1].bl_number
    assert reason, "un écart sans raison est inexploitable"
    assert result.is_complete is False


@pytest.mark.asyncio
async def test_bulk_signing_nothing_selected_signs_nothing(db):
    """Rien de coché : on ne signe rien et on ne prétend pas le contraire."""
    from app.routers.captain_router import captain_sign_selected_bls

    leg = await _leg(db)
    ops, master = await _users(db)
    client = await _client(db)
    pl, batches = await _pl_with_batches(db, leg, count=1)
    await _to_validated(db, pl, batches[0], leg, ops, client)

    resp = await captain_sign_selected_bls(_Req(form={"leg_id": str(leg.id)}), db=db, user=master)
    assert resp.status_code == 303
    assert batches[0].bl_state == w.CLIENT_VALIDATED, "un BL a été signé sans sélection"
    assert "signed=" not in resp.headers["location"]


@pytest.mark.asyncio
async def test_bulk_signing_ignores_unparseable_ids(db):
    """Une saisie forgée ne doit pas faire tomber la route en 500."""
    from app.routers.captain_router import captain_sign_selected_bls

    leg = await _leg(db)
    _ops, master = await _users(db)
    form = {"batch_ids": ["abc", "", "999999"], "leg_id": str(leg.id)}
    resp = await captain_sign_selected_bls(_Req(form=form), db=db, user=master)
    assert resp.status_code == 303


# ───────────────────────── écran et permissions ─────────────────────────


@pytest.mark.asyncio
async def test_the_screen_separates_awaiting_signed_and_not_ready(db):
    """Un draft non validé est affiché À PART, pas masqué.

    Le masquer ferait croire à un oubli d'émission alors que la balle est chez le
    client.
    """
    from app.routers.captain_router import captain_bl_index

    leg = await _leg(db)
    ops, master = await _users(db)
    client = await _client(db)
    pl, batches = await _pl_with_batches(db, leg, count=3)
    await _to_validated(db, pl, batches[0], leg, ops, client)  # à signer
    await _to_validated(db, pl, batches[1], leg, ops, client)
    await w.sign_by_master(db, batch=batches[1], user=master)  # signé
    await w.generate_draft(db, pl=pl, batch=batches[2], leg=leg, user=ops)  # pas prêt

    resp = await captain_bl_index(_Req(), leg_id=leg.id, db=db, user=master)
    ctx = resp.context
    assert [b.id for b in ctx["awaiting"]] == [batches[0].id]
    assert [b.id for b in ctx["signed_batches"]] == [batches[1].id]
    assert [b.id for b in ctx["not_ready"]] == [batches[2].id]


def test_signing_requires_modify_and_reading_requires_consult():
    """La DÉCLARATION protège : rétrograder la permission doit casser la suite."""
    from app.routers import captain_router as r

    assert 'require_permission("captain", "M")' in inspect.getsource(r.captain_sign_bl)
    assert 'require_permission("captain", "M")' in inspect.getsource(r.captain_sign_selected_bls)
    assert 'require_permission("captain", "C")' in inspect.getsource(r.captain_bl_index)

    routes = {
        rt.path: rt.methods
        for rt in r.router.routes
        if getattr(rt, "path", "").startswith("/captain/bl")
    }
    assert routes["/captain/bl"] == {"GET"}
    assert routes["/captain/bl/{batch_id}/sign"] == {"POST"}
    assert routes["/captain/bl/sign-selected"] == {"POST"}


def test_the_template_avoids_nested_forms_and_inline_handlers():
    """Deux pièges HTML/CSP que ce gabarit ne doit pas reproduire.

    - un `<form>` imbriqué dans un `<form>` est interdit et casserait les DEUX
      envois (groupé et unitaire) ; l'action unitaire passe donc par `formaction` ;
    - la CSP du projet est `script-src 'self'` sans `unsafe-inline` : un
      `onchange="this.form.submit()"` serait bloqué, le filtre resterait inerte.
    """
    from app.templating import templates

    raw = templates.env.loader.get_source(templates.env, "staff/captain/bl_list.html")[0]
    # Les commentaires Jinja `{# … #}` ne sortent JAMAIS au rendu : les inclure dans
    # l'analyse ferait échouer le test sur les commentaires qui documentent
    # précisément les pièges à éviter (ils citent `<form>` et `onchange=`).
    src = re.sub(r"\{#.*?#\}", "", raw, flags=re.DOTALL)

    assert "formaction=" in src
    assert src.count("<form") == 2, "un formulaire de trop : risque d'imbrication"
    assert "onchange=" not in src, "gestionnaire inline bloqué par la CSP du projet"


def test_the_screen_is_reachable_from_the_onboard_index():
    """Un écran sans lien n'existe pas pour le commandant, quelle que soit la route."""
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/captain/index.html")[0]
    assert 'href="/captain/bl' in src
