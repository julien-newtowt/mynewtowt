"""Registre de remise des originaux du connaissement — §5.1.

Sans ce registre, le transporteur ne peut établir **ni à qui, ni quand, ni comment**
il a remis les originaux : c'est le dispositif dont l'absence exclut la
*misdelivery* de la couverture P&I.

Ce que ces tests protègent, par ordre de gravité :

1. **🔴 un téléchargement n'est PAS une réception.** C'est l'erreur qui coûterait le
   plus cher : un préchargement de lien ou un antivirus de messagerie produit un
   téléchargement sans que personne n'ait rien lu. Le compter comme une réception
   produirait une affirmation fausse au moment où elle compte le plus ;
2. **le repli Opérations exige un moyen de remise** — une attestation qui ne dit pas
   *comment* n'établit rien. Exigé en service **et** en base ;
3. **le confirmateur est le client OU le staff, jamais les deux** — une attestation
   du staff ne doit pas pouvoir être relue comme une déclaration du client ;
4. **le registre est append-only** — on n'écrase pas un événement de remise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.packing_list import BlDeliveryReceipt
from app.services import bl_delivery as d


async def _ctx(db, *, state="master_signed"):
    """Par défaut un BL **signé** : c'est la signature qui crée les originaux, donc
    le seul état où parler de remise a un sens."""
    from app.models.client_account import ClientAccount
    from app.models.commercial import Client, Order
    from app.models.packing_list import PackingList, PackingListBatch
    from app.models.user import User

    cl = Client(name="ACME", client_type="shipper")
    db.add(cl)
    await db.flush()
    order = Order(reference="CMD-DEL", client_id=cl.id)
    db.add(order)
    await db.flush()
    pl = PackingList(order_id=order.id)
    db.add(pl)
    await db.flush()
    batch = PackingListBatch(
        packing_list_id=pl.id,
        batch_number=1,
        pallet_count=4,
        bl_number="TUAW_DEL_001",
        bl_state=state,
    )
    account = ClientAccount(email="cli-del@example.test", hashed_password="x", company_name="Belco")
    ops = User(
        username="ops-del", email="ops-del@newtowt.test", hashed_password="x", role="operation"
    )
    db.add_all([batch, account, ops])
    await db.flush()
    return batch, account, ops


# ───────────── 🔴 un téléchargement n'est pas une réception ─────────────


@pytest.mark.asyncio
async def test_a_download_is_not_an_acknowledgement(db):
    """🔴 Le cœur du module.

    Un préchargement de lien produit ce même événement. Le compter comme une
    réception ferait affirmer, face à un assureur, une remise que rien n'établit.
    """
    batch, account, _ops = await _ctx(db)
    await d.record_download(db, batch=batch, client=account, ip="203.0.113.5")
    await d.record_download(db, batch=batch, client=account)

    assert await d.has_client_acknowledgement(db, batch_id=batch.id) is False

    status = await d.delivery_status(db, batch_id=batch.id)
    assert status["download_count"] == 2
    assert status["acknowledged"] is False
    assert status["acknowledged_at"] is None


@pytest.mark.asyncio
async def test_a_client_confirmation_is_an_acknowledgement(db):
    batch, account, _ops = await _ctx(db)
    await d.record_download(db, batch=batch, client=account)
    await d.confirm_by_client(db, batch=batch, client=account, notes="Reçus ce matin")

    assert await d.has_client_acknowledgement(db, batch_id=batch.id) is True
    status = await d.delivery_status(db, batch_id=batch.id)
    assert status["acknowledged"] is True
    assert status["acknowledged_channel"] == BlDeliveryReceipt.CHANNEL_CLIENT
    # Le téléchargement reste consigné, il ne disparaît pas.
    assert status["download_count"] == 1


@pytest.mark.asyncio
async def test_the_acknowledged_date_is_the_attested_one_not_the_latest_event(db):
    """⚠️ Un téléchargement postérieur ne doit pas déplacer la date d'attestation."""
    batch, account, _ops = await _ctx(db)
    await d.confirm_by_client(db, batch=batch, client=account)
    ack_at = (await d.delivery_status(db, batch_id=batch.id))["acknowledged_at"]

    await d.record_download(db, batch=batch, client=account)
    status = await d.delivery_status(db, batch_id=batch.id)
    assert status["acknowledged_at"] == ack_at


# ───────────────────── repli Opérations ─────────────────────


@pytest.mark.asyncio
async def test_the_ops_fallback_requires_a_means(db):
    """🔴 Une attestation qui ne dit pas COMMENT n'établit rien."""
    batch, _account, ops = await _ctx(db)
    with pytest.raises(d.MeansRequired):
        await d.confirm_by_ops(
            db, batch=batch, user=ops, confirmed_at=datetime.now(UTC), means="  "
        )
    assert await d.receipts_for_batch(db, batch_id=batch.id) == []


@pytest.mark.asyncio
async def test_the_ops_fallback_is_recorded_as_a_fallback(db):
    """Jamais relisible comme une déclaration du client."""
    from app.models.activity_log import ActivityLog

    batch, _account, ops = await _ctx(db)
    when = datetime(2026, 8, 14, 16, 30, tzinfo=UTC)
    r = await d.confirm_by_ops(
        db,
        batch=batch,
        user=ops,
        confirmed_at=when,
        means="coursier",
        notes="Remis au transitaire, bordereau signé",
    )
    assert r.channel == BlDeliveryReceipt.CHANNEL_OPS
    assert r.confirmed_by_user_id == ops.id
    assert r.confirmed_by_client_id is None, "un repli staff signé comme le client"
    assert r.confirmed_at == when
    assert await d.has_client_acknowledgement(db, batch_id=batch.id) is True

    logs = list(
        (
            await db.execute(
                select(ActivityLog).where(ActivityLog.action == "bl_delivery_confirmed_by_ops")
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    detail = logs[0].detail or ""
    assert "PAR NEWTOWT" in detail, "le repli doit être explicite dans la trace"
    assert "coursier" in detail


@pytest.mark.asyncio
async def test_the_ops_date_may_precede_the_entry(db):
    """La remise papier est saisie après coup : `confirmed_at` est la date DÉCLARÉE."""
    batch, _account, ops = await _ctx(db)
    when = datetime.now(UTC) - timedelta(days=3)
    r = await d.confirm_by_ops(db, batch=batch, user=ops, confirmed_at=when, means="mail")
    assert r.confirmed_at == when
    assert r.created_at is not None


@pytest.mark.asyncio
async def test_an_attachment_is_recorded_and_traced(db):
    from app.models.activity_log import ActivityLog

    batch, _account, ops = await _ctx(db)
    r = await d.confirm_by_ops(
        db,
        batch=batch,
        user=ops,
        confirmed_at=datetime.now(UTC),
        means="courrier",
        attachment_path="cargo-delivery/abc123.pdf",
    )
    assert r.attachment_path == "cargo-delivery/abc123.pdf"
    logs = list(
        (
            await db.execute(
                select(ActivityLog).where(ActivityLog.action == "bl_delivery_confirmed_by_ops")
            )
        )
        .scalars()
        .all()
    )
    assert "abc123.pdf" in (logs[0].detail or "")


# ───────────────────── contraintes en base ─────────────────────


@pytest.mark.asyncio
async def test_the_database_refuses_an_ops_receipt_without_a_means(db):
    """⚠️ Le garde-fou ultime : un futur chemin d'écriture ne peut pas contourner."""
    batch, _account, ops = await _ctx(db)
    db.add(
        BlDeliveryReceipt(
            batch_id=batch.id,
            channel=BlDeliveryReceipt.CHANNEL_OPS,
            confirmed_at=datetime.now(UTC),
            means=None,
            confirmed_by_user_id=ops.id,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_the_database_refuses_two_confirmers_at_once(db):
    batch, account, ops = await _ctx(db)
    db.add(
        BlDeliveryReceipt(
            batch_id=batch.id,
            channel=BlDeliveryReceipt.CHANNEL_CLIENT,
            confirmed_at=datetime.now(UTC),
            confirmed_by_client_id=account.id,
            confirmed_by_user_id=ops.id,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_the_database_refuses_an_unknown_channel(db):
    """La liste des canaux est fermée : trois valeurs probantes, pas une quatrième
    improvisée dont personne ne saurait ce qu'elle prouve."""
    batch, account, _ops = await _ctx(db)
    db.add(
        BlDeliveryReceipt(
            batch_id=batch.id,
            channel="peut_etre_recu",
            confirmed_at=datetime.now(UTC),
            confirmed_by_client_id=account.id,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


# ───────────────────── append-only ─────────────────────


@pytest.mark.asyncio
async def test_the_register_accumulates_rather_than_overwrites(db):
    """Un registre qui se réécrit ne prouve rien."""
    batch, account, ops = await _ctx(db)
    await d.confirm_by_client(db, batch=batch, client=account)
    await d.confirm_by_ops(
        db, batch=batch, user=ops, confirmed_at=datetime.now(UTC), means="téléphone"
    )
    rows = await d.receipts_for_batch(db, batch_id=batch.id)
    assert len(rows) == 2
    assert {r.channel for r in rows} == {
        BlDeliveryReceipt.CHANNEL_CLIENT,
        BlDeliveryReceipt.CHANNEL_OPS,
    }


@pytest.mark.asyncio
async def test_receipts_are_scoped_to_their_batch(db):
    """Le registre d'un lot ne doit pas montrer les remises d'un autre."""
    from app.models.packing_list import PackingListBatch

    batch, account, _ops = await _ctx(db)
    other = PackingListBatch(
        packing_list_id=batch.packing_list_id,
        batch_number=2,
        pallet_count=1,
        bl_number="TUAW_DEL_002",
    )
    db.add(other)
    await db.flush()
    await d.confirm_by_client(db, batch=batch, client=account)

    assert len(await d.receipts_for_batch(db, batch_id=batch.id)) == 1
    assert await d.receipts_for_batch(db, batch_id=other.id) == []
    assert await d.has_client_acknowledgement(db, batch_id=other.id) is False


def test_the_number_of_originals_is_a_constant():
    """§5.1 — « Toujours 3 », aucun paramétrage à prévoir."""
    assert d.NUMBER_OF_ORIGINALS == 3


# ───────── un projet n'est pas un original : rien à remettre ─────────


@pytest.mark.asyncio
async def test_downloading_a_draft_is_not_recorded_at_all(db):
    """🔴 Télécharger un PROJET n'est pas recevoir un original.

    Avant signature aucun original n'existe. Consigner ces accès dans un registre de
    **remise** le remplirait d'événements qui ne concernent pas la remise, et
    gonflerait un compteur que quelqu'un finirait par lire comme une preuve.
    """
    batch, account, _ops = await _ctx(db, state="draft")
    assert await d.record_download(db, batch=batch, client=account) is None
    assert await d.receipts_for_batch(db, batch_id=batch.id) == []

    status = await d.delivery_status(db, batch_id=batch.id)
    assert status["download_count"] == 0


@pytest.mark.asyncio
async def test_confirming_a_draft_is_refused_on_both_channels(db):
    """Confirmer la réception d'un projet n'a aucun sens — des deux côtés."""
    batch, account, ops = await _ctx(db, state="client_validated")

    with pytest.raises(d.DeliveryReceiptError):
        await d.confirm_by_client(db, batch=batch, client=account)
    with pytest.raises(d.DeliveryReceiptError):
        await d.confirm_by_ops(
            db, batch=batch, user=ops, confirmed_at=datetime.now(UTC), means="mail"
        )
    assert await d.receipts_for_batch(db, batch_id=batch.id) == []


@pytest.mark.asyncio
async def test_a_final_bl_is_deliverable_too(db):
    """⚠️ Garde anti-sur-correction : `final` doit rester remisible, pas seulement
    `master_signed`."""
    batch, account, _ops = await _ctx(db, state="final")
    assert d.is_deliverable(batch) is True
    assert await d.record_download(db, batch=batch, client=account) is not None


# ───────────────────────── les routes ─────────────────────────


class _Req:
    """Requête minimale portant un formulaire."""

    def __init__(self, form: dict | None = None):
        self._form = form or {}
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="203.0.113.77")
        self.url = SimpleNamespace(path="/me")
        self.state = SimpleNamespace(csrf_token="x")
        self.cookies: dict[str, str] = {}
        self.query_params: dict[str, str] = {}

    async def form(self):
        return self._form


async def _client_booking_ctx(db, *, state="master_signed"):
    """Booking client + packing list + un lot dont le BL est dans `state`."""
    from app.models.booking import Booking
    from app.models.client_account import ClientAccount
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
    account = ClientAccount(email="cli-rt@example.test", hashed_password="x", company_name="Belco")
    db.add_all([leg, account])
    await db.flush()
    booking = Booking(
        reference="BK-DELIV", leg_id=leg.id, client_account_id=account.id, status="confirmed"
    )
    db.add(booking)
    await db.flush()
    pl = PackingList(booking_id=booking.id, leg_id=leg.id)
    db.add(pl)
    await db.flush()
    batch = PackingListBatch(
        packing_list_id=pl.id,
        batch_number=1,
        pallet_count=4,
        bl_number="TUAW_RT_001",
        bl_state=state,
    )
    ops = User(
        username="ops-rt", email="ops-rt@newtowt.test", hashed_password="x", role="operation"
    )
    db.add_all([batch, ops])
    await db.flush()
    return booking, pl, batch, account, ops


def _fake_render(**kw):
    return SimpleNamespace(pdf=b"%PDF", mime="application/pdf", filename="bl.pdf")


@pytest.mark.asyncio
async def test_downloading_an_original_records_an_access(db, monkeypatch):
    """Le téléchargement alimente le registre — comme un ACCÈS, pas une réception."""
    from app.routers.cargo_router import client_batch_bl_pdf

    booking, _pl, batch, account, _ops = await _client_booking_ctx(db)
    monkeypatch.setattr("app.services.pdf_generator.render_bill_of_lading_from_pl", _fake_render)
    await client_batch_bl_pdf(booking.reference, batch.id, _Req(), client=account, db=db)

    state = await d.delivery_status(db, batch_id=batch.id)
    assert state["download_count"] == 1
    assert state["acknowledged"] is False, "un téléchargement compté comme réception"


@pytest.mark.asyncio
async def test_downloading_a_draft_records_nothing(db, monkeypatch):
    from app.routers.cargo_router import client_batch_bl_pdf

    booking, _pl, batch, account, _ops = await _client_booking_ctx(db, state="draft")
    monkeypatch.setattr("app.services.pdf_generator.render_bill_of_lading_from_pl", _fake_render)
    await client_batch_bl_pdf(booking.reference, batch.id, _Req(), client=account, db=db)
    assert await d.receipts_for_batch(db, batch_id=batch.id) == []


@pytest.mark.asyncio
async def test_the_client_can_confirm_receipt(db):
    from app.routers.cargo_router import client_confirm_bl_receipt

    booking, _pl, batch, account, _ops = await _client_booking_ctx(db)
    resp = await client_confirm_bl_receipt(
        booking.reference, batch.id, _Req({"notes": "3 originaux reçus"}), client=account, db=db
    )
    assert resp.status_code == 303
    assert await d.has_client_acknowledgement(db, batch_id=batch.id) is True


@pytest.mark.asyncio
async def test_a_client_cannot_confirm_another_clients_bl(db):
    """🔴 Le cloisonnement s'applique aussi à la confirmation de réception."""
    from app.models.client_account import ClientAccount
    from app.routers.cargo_router import client_confirm_bl_receipt

    booking, _pl, batch, _account, _ops = await _client_booking_ctx(db)
    intruder = ClientAccount(email="intrus@example.test", hashed_password="x", company_name="X")
    db.add(intruder)
    await db.flush()

    with pytest.raises(HTTPException) as e:
        await client_confirm_bl_receipt(booking.reference, batch.id, _Req(), client=intruder, db=db)
    assert e.value.status_code == 404
    assert await d.has_client_acknowledgement(db, batch_id=batch.id) is False


@pytest.mark.asyncio
async def test_confirming_an_unsigned_bl_returns_409(db):
    from app.routers.cargo_router import client_confirm_bl_receipt

    booking, _pl, batch, account, _ops = await _client_booking_ctx(db, state="draft")
    with pytest.raises(HTTPException) as e:
        await client_confirm_bl_receipt(booking.reference, batch.id, _Req(), client=account, db=db)
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_the_ops_route_refuses_a_missing_means(db):
    """400 : donnée d'entrée invalide, l'utilisateur doit compléter."""
    from app.routers.cargo_packing_router import ops_confirm_bl_delivery

    _bk, pl, batch, _account, ops = await _client_booking_ctx(db)
    req = _Req({"confirmed_at": "2026-08-14T16:30", "means": ""})
    with pytest.raises(HTTPException) as e:
        await ops_confirm_bl_delivery(pl.id, batch.id, req, file=None, db=db, user=ops)
    assert e.value.status_code == 400
    assert await d.receipts_for_batch(db, batch_id=batch.id) == []


@pytest.mark.asyncio
async def test_the_ops_route_refuses_a_malformed_datetime(db):
    from app.routers.cargo_packing_router import ops_confirm_bl_delivery

    _bk, pl, batch, _account, ops = await _client_booking_ctx(db)
    req = _Req({"confirmed_at": "14/08/2026 16:30", "means": "coursier"})
    with pytest.raises(HTTPException) as e:
        await ops_confirm_bl_delivery(pl.id, batch.id, req, file=None, db=db, user=ops)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_the_ops_route_records_the_attestation(db):
    from app.routers.cargo_packing_router import ops_confirm_bl_delivery

    _bk, pl, batch, _account, ops = await _client_booking_ctx(db)
    req = _Req(
        {
            "confirmed_at": "2026-08-14T16:30",
            "means": "coursier",
            "notes": "Bordereau signé par le transitaire",
        }
    )
    resp = await ops_confirm_bl_delivery(pl.id, batch.id, req, file=None, db=db, user=ops)
    assert resp.status_code == 303

    rows = await d.receipts_for_batch(db, batch_id=batch.id)
    assert len(rows) == 1
    assert rows[0].channel == BlDeliveryReceipt.CHANNEL_OPS
    assert rows[0].means == "coursier"
    # `datetime-local` n'a pas de fuseau : la route ancre la valeur en UTC. On
    # compare l'INSTANT et non le `tzinfo` : `DateTime(timezone=True)` rend du naïf
    # sous SQLite (tests) et de l'aware sous Postgres (production) — convention
    # `planning.ensure_utc` du projet. Asserter sur `tzinfo` testerait le driver.
    from app.services.planning import ensure_utc

    assert ensure_utc(rows[0].confirmed_at) == datetime(2026, 8, 14, 16, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_the_ops_route_stores_an_attachment(db):
    from app.routers.cargo_packing_router import ops_confirm_bl_delivery

    _bk, pl, batch, _account, ops = await _client_booking_ctx(db)

    class _Upload:
        filename = "bordereau.pdf"

        async def read(self):
            return b"%PDF-1.4 preuve de remise"

    req = _Req({"confirmed_at": "2026-08-14T16:30", "means": "courrier"})
    await ops_confirm_bl_delivery(pl.id, batch.id, req, file=_Upload(), db=db, user=ops)

    rows = await d.receipts_for_batch(db, batch_id=batch.id)
    assert rows[0].attachment_path and rows[0].attachment_path.startswith("bl-delivery/")


def test_the_routes_are_declared_as_posts():
    from app.routers import cargo_packing_router as pr
    from app.routers import cargo_router as cr

    client_route = next(
        rt for rt in cr.router.routes if getattr(rt, "path", "").endswith("/confirm-receipt")
    )
    ops_route = next(
        rt for rt in pr.router.routes if getattr(rt, "path", "").endswith("/bl/confirm-delivery")
    )
    assert client_route.methods == {"POST"}
    assert ops_route.methods == {"POST"}


# ───────────────────── les écrans ─────────────────────


def _stripped(name):
    import re

    from app.templating import templates

    raw = templates.env.loader.get_source(templates.env, name)[0]
    return re.sub(r"\{#.*?#\}", "", raw, flags=re.DOTALL)


def test_the_client_screen_never_calls_a_download_a_receipt():
    """🔴 Le vocabulaire compte autant que le calcul.

    Un client qui lit « reçu » alors qu'il a seulement ouvert le PDF se croira
    couvert. L'écran dit « consultation(s) — accès seulement ».
    """
    src = _stripped("client/bl_list.html")
    assert "accès seulement, pas une réception" in src
    assert "access only, not a receipt" in src  # les deux langues
    assert "/confirm-receipt" in src


def test_the_client_screen_only_offers_confirmation_on_a_signed_bl():
    """On ne confirme pas la réception d'un projet : aucun original n'existe."""
    src = _stripped("client/bl_list.html")
    assert "b.bl_state in ('master_signed', 'final') and not (dl and dl.acknowledged)" in src


def test_the_ops_screen_marks_the_fallback_as_such():
    """Un repli présenté comme une déclaration du client serait trompeur."""
    src = _stripped("staff/cargo/packing_list_detail.html")
    assert "/bl/confirm-delivery" in src
    assert "attestée par NEWTOWT" in src
    assert "déclarée par le client" in src
    # Le moyen est exigé côté formulaire aussi (le service reste l'autorité).
    assert 'name="means" required' in src
    # L'envoi de fichier exige le bon encodage, sinon la PJ n'arrive jamais.
    assert 'enctype="multipart/form-data"' in src


def test_the_ops_screen_shows_downloads_as_access_only():
    src = _stripped("staff/cargo/packing_list_detail.html")
    assert "ne vaut pas réception" in src


@pytest.mark.asyncio
async def test_the_client_screen_context_carries_the_delivery_state(db):
    from app.routers.cargo_router import client_bl_list

    booking, _pl, batch, account, _ops = await _client_booking_ctx(db)
    await d.confirm_by_client(db, batch=batch, client=account)

    resp = await client_bl_list(booking.reference, _Req(), client=account, db=db)
    assert resp.context["delivery"][batch.id]["acknowledged"] is True
    assert resp.context["number_of_originals"] == 3


@pytest.mark.asyncio
async def test_the_ops_screen_context_carries_the_delivery_state(db):
    from app.routers.cargo_packing_router import packing_list_detail

    _bk, pl, batch, _account, ops = await _client_booking_ctx(db)
    await d.confirm_by_ops(
        db, batch=batch, user=ops, confirmed_at=datetime.now(UTC), means="coursier"
    )

    resp = await packing_list_detail(pl.id, _Req(), db=db, user=ops)
    state = resp.context["delivery_by_batch"][batch.id]
    assert state["acknowledged"] is True
    assert state["acknowledged_channel"] == "ops_confirmed"
    assert "coursier" in resp.context["suggested_means"]
