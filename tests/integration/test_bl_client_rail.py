"""Rail client du connaissement — accès, validation, et surtout **cloisonnement**.

Cf. `docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md` §4.1 et §5.4.

Ces routes sont le **préalable au retrait du rail booking** : le retirer avant
priverait le client de tout accès à son connaissement.

Ce que ces tests protègent, par ordre de gravité :

1. **🔴 le cloisonnement entre clients.** Un client authentifié ne doit jamais
   atteindre le connaissement d'un autre — ni par la référence de booking, ni en
   devinant un `batch_id`. C'est le second point qui compte : la référence dans
   l'URL ne suffit pas, **c'est le lot qui porte le document** ;
2. **404 et non 403** pour ce qui n'est pas à soi : un 403 confirmerait l'existence
   de la référence ;
3. **la validation engage le client titulaire**, jamais le portail expéditeur
   (anonyme par conception) ;
4. **la lecture n'écrit rien** — le défaut corrigé côté staff ne doit pas
   réapparaître côté client.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import bl_workflow as w


class _Req:
    headers: dict[str, str] = {}
    client = SimpleNamespace(host="198.51.100.9")
    url = SimpleNamespace(path="/me")
    state = SimpleNamespace(csrf_token="x")
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}


async def _leg(db):
    from app.models.leg import Leg
    from app.models.port import Port
    from app.models.vessel import Vessel

    v = Vessel(name="Anemos", code="1")
    pol = Port(locode="FRFEC", name="Fécamp", country="FR")
    pod = Port(locode="BRSSO", name="Santos", country="BR")
    db.add_all([v, pol, pod])
    await db.flush()
    base = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
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
    db.add(leg)
    await db.flush()
    return leg


async def _account(db, email):
    from app.models.client_account import ClientAccount

    c = ClientAccount(email=email, hashed_password="x", company_name=email.split("@")[0])
    db.add(c)
    await db.flush()
    return c


async def _booking_with_bls(db, leg, account, *, ref, count=2, emit=True):
    """Booking + packing list + `count` lots, BL émis si `emit`."""
    from app.models.booking import Booking
    from app.models.packing_list import PackingList, PackingListBatch
    from app.models.user import User

    booking = Booking(
        reference=ref, leg_id=leg.id, client_account_id=account.id, status="confirmed"
    )
    db.add(booking)
    await db.flush()
    pl = PackingList(booking_id=booking.id, leg_id=leg.id)
    db.add(pl)
    await db.flush()
    ops = User(
        username=f"ops-{ref}",
        email=f"ops-{ref}@newtowt.test",
        hashed_password="x",
        role="operation",
    )
    db.add(ops)
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
        await db.flush()
        if emit:
            await w.generate_draft(db, pl=pl, batch=b, leg=leg, user=ops)
        batches.append(b)
    return booking, pl, batches


# ───────────────────── 🔴 cloisonnement entre clients ─────────────────────


@pytest.mark.asyncio
async def test_a_client_cannot_list_another_clients_bls(db):
    from app.routers.cargo_router import client_bl_list

    leg = await _leg(db)
    mine = await _account(db, "mine@example.test")
    theirs = await _account(db, "theirs@example.test")
    booking, _pl, _b = await _booking_with_bls(db, leg, theirs, ref="BK-THEIRS")

    with pytest.raises(HTTPException) as e:
        await client_bl_list(booking.reference, _Req(), client=mine, db=db)
    assert e.value.status_code == 404, "403 confirmerait l'existence de la référence"


@pytest.mark.asyncio
async def test_a_client_cannot_download_another_clients_bl(db):
    from app.routers.cargo_router import client_batch_bl_pdf

    leg = await _leg(db)
    mine = await _account(db, "mine2@example.test")
    theirs = await _account(db, "theirs2@example.test")
    booking, _pl, batches = await _booking_with_bls(db, leg, theirs, ref="BK-THEIRS2")

    with pytest.raises(HTTPException) as e:
        await client_batch_bl_pdf(booking.reference, batches[0].id, _Req(), client=mine, db=db)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_a_batch_from_another_booking_is_refused(db):
    """🔴 Le point subtil : la référence dans l'URL ne suffit PAS.

    Le client passe **sa propre** référence de booking (donc le contrôle de
    propriété du booking passe) mais un `batch_id` appartenant à quelqu'un d'autre.
    Sans vérification que le lot appartient bien à CE booking, il lirait le
    connaissement d'un tiers.
    """
    from app.routers.cargo_router import client_batch_bl_pdf

    leg = await _leg(db)
    mine = await _account(db, "mine3@example.test")
    theirs = await _account(db, "theirs3@example.test")
    my_booking, _p1, _b1 = await _booking_with_bls(db, leg, mine, ref="BK-MINE3", count=1)
    _their_booking, _p2, their_batches = await _booking_with_bls(
        db, leg, theirs, ref="BK-THEIRS3", count=1
    )

    with pytest.raises(HTTPException) as e:
        await client_batch_bl_pdf(
            my_booking.reference, their_batches[0].id, _Req(), client=mine, db=db
        )
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_a_client_cannot_validate_another_clients_bl(db):
    from app.routers.cargo_router import client_validate_bl

    leg = await _leg(db)
    mine = await _account(db, "mine4@example.test")
    theirs = await _account(db, "theirs4@example.test")
    my_booking, _p1, _b1 = await _booking_with_bls(db, leg, mine, ref="BK-MINE4", count=1)
    _tb, _p2, their_batches = await _booking_with_bls(db, leg, theirs, ref="BK-THEIRS4", count=1)

    with pytest.raises(HTTPException) as e:
        await client_validate_bl(
            my_booking.reference, their_batches[0].id, _Req(), client=mine, db=db
        )
    assert e.value.status_code == 404
    assert their_batches[0].bl_state == w.DRAFT, "un BL tiers a été validé"


# ───────────────────────── accès légitime ─────────────────────────


@pytest.mark.asyncio
async def test_the_owner_sees_only_their_own_bls(db):
    from app.routers.cargo_router import client_bl_list

    leg = await _leg(db)
    mine = await _account(db, "owner@example.test")
    theirs = await _account(db, "other@example.test")
    my_booking, _p, my_batches = await _booking_with_bls(db, leg, mine, ref="BK-OWN", count=2)
    await _booking_with_bls(db, leg, theirs, ref="BK-OTHER", count=3)

    resp = await client_bl_list(my_booking.reference, _Req(), client=mine, db=db)
    listed = resp.context["batches"]
    assert {b.id for b in listed} == {b.id for b in my_batches}


@pytest.mark.asyncio
async def test_a_batch_without_a_bl_is_not_listed(db):
    """Rien à montrer tant que l'équipe n'a pas émis le draft."""
    from app.routers.cargo_router import client_bl_list

    leg = await _leg(db)
    mine = await _account(db, "nobl@example.test")
    booking, _pl, _b = await _booking_with_bls(db, leg, mine, ref="BK-NOBL", emit=False)

    resp = await client_bl_list(booking.reference, _Req(), client=mine, db=db)
    assert resp.context["batches"] == []


@pytest.mark.asyncio
async def test_the_owner_can_read_their_bl(db, monkeypatch):
    """⚠️ Garde anti-sur-correction : le cloisonnement ne doit pas bloquer l'ayant droit."""
    from app.routers.cargo_router import client_batch_bl_pdf

    leg = await _leg(db)
    mine = await _account(db, "read@example.test")
    booking, _pl, batches = await _booking_with_bls(db, leg, mine, ref="BK-READ", count=1)

    def _fake(**kw):
        return SimpleNamespace(pdf=b"%PDF-1.4 fake", mime="application/pdf", filename="bl.pdf")

    monkeypatch.setattr("app.services.pdf_generator.render_bill_of_lading_from_pl", _fake)
    resp = await client_batch_bl_pdf(booking.reference, batches[0].id, _Req(), client=mine, db=db)
    assert resp.status_code == 200
    assert resp.body == b"%PDF-1.4 fake"


@pytest.mark.asyncio
async def test_reading_does_not_write(db, monkeypatch):
    """Le défaut corrigé côté staff ne doit pas réapparaître côté client."""
    from app.models.packing_list import PackingListBatch
    from app.routers.cargo_router import client_batch_bl_pdf

    leg = await _leg(db)
    mine = await _account(db, "nowrite@example.test")
    booking, _pl, batches = await _booking_with_bls(db, leg, mine, ref="BK-NOWRITE", count=1)
    before = (batches[0].bl_number, batches[0].bl_state)

    def _fake(**kw):
        return SimpleNamespace(pdf=b"%PDF", mime="application/pdf", filename="bl.pdf")

    monkeypatch.setattr("app.services.pdf_generator.render_bill_of_lading_from_pl", _fake)
    await client_batch_bl_pdf(booking.reference, batches[0].id, _Req(), client=mine, db=db)

    fresh = await db.get(PackingListBatch, batches[0].id)
    assert (fresh.bl_number, fresh.bl_state) == before


# ───────────────────────── validation client ─────────────────────────


@pytest.mark.asyncio
async def test_the_owner_validates_the_draft_and_is_named_as_the_validator(db):
    from app.routers.cargo_router import client_validate_bl

    leg = await _leg(db)
    mine = await _account(db, "validate@example.test")
    booking, _pl, batches = await _booking_with_bls(db, leg, mine, ref="BK-VAL", count=1)
    b = batches[0]

    resp = await client_validate_bl(booking.reference, b.id, _Req(), client=mine, db=db)
    assert resp.status_code == 303
    assert b.bl_state == w.CLIENT_VALIDATED
    # C'est bien le CLIENT, pas un repli staff « pour son compte ».
    assert b.bl_client_validated_by_id == mine.id
    assert b.bl_validated_on_behalf_by_id is None
    assert b.bl_client_validated_at is not None


@pytest.mark.asyncio
async def test_validating_twice_is_refused(db):
    from app.routers.cargo_router import client_validate_bl

    leg = await _leg(db)
    mine = await _account(db, "twice@example.test")
    booking, _pl, batches = await _booking_with_bls(db, leg, mine, ref="BK-TWICE", count=1)

    await client_validate_bl(booking.reference, batches[0].id, _Req(), client=mine, db=db)
    with pytest.raises(HTTPException) as e:
        await client_validate_bl(booking.reference, batches[0].id, _Req(), client=mine, db=db)
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_validating_a_signed_bl_is_refused(db):
    from app.models.user import User
    from app.routers.cargo_router import client_validate_bl

    leg = await _leg(db)
    mine = await _account(db, "signed@example.test")
    booking, _pl, batches = await _booking_with_bls(db, leg, mine, ref="BK-SIGNED", count=1)
    master = User(
        username="cdt-cr", email="cdt-cr@newtowt.test", hashed_password="x", role="marins"
    )
    db.add(master)
    await db.flush()
    await w.validate_by_client(db, batch=batches[0], client=mine)
    await w.sign_by_master(db, batch=batches[0], user=master)

    with pytest.raises(HTTPException) as e:
        await client_validate_bl(booking.reference, batches[0].id, _Req(), client=mine, db=db)
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_editing_after_the_client_validated_returns_it_to_draft(db):
    """La règle de régression, vue depuis le rail client : une validation porte sur
    un contenu, et le client devra revalider."""
    from app.routers.cargo_router import client_validate_bl

    leg = await _leg(db)
    mine = await _account(db, "regress@example.test")
    booking, _pl, batches = await _booking_with_bls(db, leg, mine, ref="BK-REGR", count=1)
    b = batches[0]
    await client_validate_bl(booking.reference, b.id, _Req(), client=mine, db=db)

    await w.invalidate_validation_on_edit(db, batch=b, actor_name="ops")
    assert b.bl_state == w.DRAFT
    assert b.bl_client_validated_by_id is None


# ───────────────────────── déclarations et gabarit ─────────────────────────


def test_the_routes_are_declared_with_the_right_methods():
    from app.routers import cargo_router as r

    paths = {rt.path: rt.methods for rt in r.router.routes if "/bl" in getattr(rt, "path", "")}
    assert paths["/me/bookings/{ref}/bls"] == {"GET"}
    assert paths["/me/bookings/{ref}/bl/{batch_id}.pdf"] == {"GET"}
    assert paths["/me/bookings/{ref}/bl/{batch_id}/validate"] == {"POST"}


def test_the_client_template_states_that_a_draft_has_no_legal_value():
    """Le client doit savoir ce qu'il valide, et ce qu'un projet ne vaut pas."""
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "client/bl_list.html")[0]
    assert "aucune valeur de titre" in src
    assert "no legal value" in src  # les deux langues, pas seulement le français
    assert "/validate" in src


def test_the_booking_page_prefers_the_real_bl_registry_when_it_exists():
    """§5.4 — transition : le nouveau rail quand il a produit, l'ancien sinon.

    Retirer l'ancien lien avant que le nouveau ne couvre tous les cas priverait
    certains clients de tout document.
    """
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "client/booking_detail.html")[0]
    assert "{% if pl_bl_count %}" in src
    assert "/bls" in src
    assert "/bl.pdf" in src, "le repli du rail booking a été retiré trop tôt"
