"""Phase 3 de la reprise UX — espace client HTMX + transition wizard→app.

Cf. docs/design/03-reprise-ux-legacy.md §10.3, constats K-4 et K-6.

Couvre :
1. K-4 — motif HTMX « maison » (204 + ``HX-Trigger`` toast + ``meRefresh``)
   appliqué aux actions répétitives de l'espace client : marquer une
   notification lue, poster un message, publier/dépublier la page voyage,
   valider un connaissement, confirmer la réception des originaux. Chacune
   garde son 303 classique en repli sans JS.
2. Invariant préservé : la validation client d'un connaissement continue de
   poser ``bl_client_validated_by_id`` — la forme de la réponse change,
   jamais l'invariant métier (cf. ``test_bl_client_rail.py``).
3. K-6 — bloc de bienvenue générique sur ``booking_done.html`` (le contexte
   de ``booking_router.step_4_done`` ne distingue pas compte auto-créé vs
   existant, et ce routeur est hors périmètre de cette phase).
4. Hygiène : aucun ``href="#"`` introduit dans les gabarits touchés.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.services import bl_workflow as w
from app.templating import templates
from tests.integration.conftest import FakeRequest
from tests.integration.test_bl_client_rail import _account, _booking_with_bls, _leg


def _hx_request(form: dict | None = None) -> FakeRequest:
    req = FakeRequest(form)
    req.headers["hx-request"] = "true"
    return req


# ───────────────────── 1a. K-4 — notification marquée lue ─────────────────


@pytest.mark.asyncio
async def test_notification_mark_read_hx_returns_204_with_meRefresh_trigger(db):
    from app.models.client_account import ClientAccount
    from app.models.notification import Notification
    from app.routers.client_dashboard_router import notification_mark_read

    account = ClientAccount(
        email="notif-hx@example.test", hashed_password="x", company_name="Acme", language="en"
    )
    db.add(account)
    await db.flush()
    notif = Notification(target_client_id=account.id, type="info", title="Test", is_read=False)
    db.add(notif)
    await db.flush()

    resp = await notification_mark_read(_hx_request(), notif.id, client=account, db=db)
    assert resp.status_code == 204
    trigger = json.loads(resp.headers["HX-Trigger"])
    assert trigger["meRefresh"] is True
    assert trigger["toast"]["type"] == "success"
    assert trigger["toast"]["message"]  # non vide
    # client.language == "en" → le toast parle anglais.
    assert trigger["toast"]["message"] == "Notification marked as read"
    assert notif.is_read is True


@pytest.mark.asyncio
async def test_notification_mark_read_without_hx_returns_303(db):
    from app.models.client_account import ClientAccount
    from app.models.notification import Notification
    from app.routers.client_dashboard_router import notification_mark_read

    account = ClientAccount(
        email="notif-nohx@example.test", hashed_password="x", company_name="Acme"
    )
    db.add(account)
    await db.flush()
    notif = Notification(target_client_id=account.id, type="info", title="Test", is_read=False)
    db.add(notif)
    await db.flush()

    resp = await notification_mark_read(FakeRequest(), notif.id, client=account, db=db)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/me/notifications"
    assert notif.is_read is True


# ───────────────────── 1b. K-4 — messagerie du booking ─────────────────────


async def _booking_for_client(db, email="msg@example.test", ref="BK-MSG"):
    from app.models.booking import Booking
    from app.models.client_account import ClientAccount

    leg = await _leg(db)
    account = ClientAccount(email=email, hashed_password="x", company_name=email.split("@")[0])
    db.add(account)
    await db.flush()
    booking = Booking(
        reference=ref, leg_id=leg.id, client_account_id=account.id, status="confirmed"
    )
    db.add(booking)
    await db.flush()
    return account, booking


@pytest.mark.asyncio
async def test_post_message_hx_returns_204_with_meRefresh_trigger(db):
    from app.routers.client_dashboard_router import post_message

    account, booking = await _booking_for_client(db, email="msg-hx@example.test", ref="BK-MSGHX")
    resp = await post_message(
        _hx_request(), booking.reference, body="Bonjour", client=account, db=db
    )
    assert resp.status_code == 204
    trigger = json.loads(resp.headers["HX-Trigger"])
    assert trigger["meRefresh"] is True
    assert trigger["toast"]["message"]


@pytest.mark.asyncio
async def test_post_message_without_hx_returns_303_with_anchor(db):
    from app.routers.client_dashboard_router import post_message

    account, booking = await _booking_for_client(
        db, email="msg-nohx@example.test", ref="BK-MSGNOHX"
    )
    resp = await post_message(
        FakeRequest(), booking.reference, body="Bonjour", client=account, db=db
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/me/bookings/{booking.reference}#messages"


# ───────────────────── 1c. K-4 — toggle page voyage publique ───────────────


@pytest.mark.asyncio
async def test_voyage_public_toggle_hx_returns_204(db):
    from app.routers.client_dashboard_router import booking_voyage_public_toggle

    account, booking = await _booking_for_client(db, email="voy-hx@example.test", ref="BK-VOYHX")
    resp = await booking_voyage_public_toggle(
        _hx_request(), booking.reference, enabled="on", client=account, db=db
    )
    assert resp.status_code == 204
    trigger = json.loads(resp.headers["HX-Trigger"])
    assert trigger["meRefresh"] is True
    assert booking.voyage_public is True


@pytest.mark.asyncio
async def test_voyage_public_toggle_without_hx_returns_303(db):
    from app.routers.client_dashboard_router import booking_voyage_public_toggle

    account, booking = await _booking_for_client(
        db, email="voy-nohx@example.test", ref="BK-VOYNOHX"
    )
    resp = await booking_voyage_public_toggle(
        FakeRequest(), booking.reference, enabled="on", client=account, db=db
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/me/bookings/{booking.reference}?voyage_saved=1"


# ───────────────────── 2. K-4 — validation BL client (invariant préservé) ──


@pytest.mark.asyncio
async def test_client_validate_bl_hx_returns_204_and_invariant_holds(db):
    from app.routers.cargo_router import client_validate_bl

    leg = await _leg(db)
    mine = await _account(db, "validate-hx@example.test")
    booking, _pl, batches = await _booking_with_bls(db, leg, mine, ref="BK-VALHX", count=1)
    b = batches[0]

    resp = await client_validate_bl(booking.reference, b.id, _hx_request(), client=mine, db=db)
    assert resp.status_code == 204
    trigger = json.loads(resp.headers["HX-Trigger"])
    assert trigger["meRefresh"] is True

    # L'invariant du rail BL client n'a pas bougé : c'est bien LE CLIENT qui
    # a validé, ce que la forme de la réponse (204 vs 303) ne doit jamais
    # affecter (cf. test_bl_client_rail.py).
    assert b.bl_state == w.CLIENT_VALIDATED
    assert b.bl_client_validated_by_id == mine.id
    assert b.bl_validated_on_behalf_by_id is None
    assert b.bl_client_validated_at is not None


@pytest.mark.asyncio
async def test_client_validate_bl_without_hx_still_returns_303(db):
    """Garde anti-régression : le repli sans JS n'a pas changé (cf. test_bl_client_rail.py)."""
    from app.routers.cargo_router import client_validate_bl

    leg = await _leg(db)
    mine = await _account(db, "validate-nohx@example.test")
    booking, _pl, batches = await _booking_with_bls(db, leg, mine, ref="BK-VALNOHX", count=1)

    resp = await client_validate_bl(
        booking.reference, batches[0].id, FakeRequest(), client=mine, db=db
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/me/bookings/{booking.reference}/bls"


@pytest.mark.asyncio
async def test_client_validate_bl_error_path_unaffected_by_hx(db):
    """Le 409 (double validation) n'est pas ré-encodé en 204 : ce n'est QUE la
    forme de la réponse de succès qui change, jamais celle des erreurs."""
    from app.routers.cargo_router import client_validate_bl

    leg = await _leg(db)
    mine = await _account(db, "validate-twice-hx@example.test")
    booking, _pl, batches = await _booking_with_bls(db, leg, mine, ref="BK-VALTWICEHX", count=1)

    await client_validate_bl(booking.reference, batches[0].id, _hx_request(), client=mine, db=db)
    with pytest.raises(HTTPException) as exc:
        await client_validate_bl(
            booking.reference, batches[0].id, _hx_request(), client=mine, db=db
        )
    assert exc.value.status_code == 409


# ───────────────────── 2b. K-4 — confirmation de réception des originaux ───


@pytest.mark.asyncio
async def test_client_confirm_bl_receipt_hx_returns_204(db):
    from app.models.user import User
    from app.routers.cargo_router import client_confirm_bl_receipt

    leg = await _leg(db)
    mine = await _account(db, "receipt-hx@example.test")
    booking, _pl, batches = await _booking_with_bls(db, leg, mine, ref="BK-RECEIPTHX", count=1)
    b = batches[0]
    master = User(
        username="cdt-recthx", email="cdt-recthx@newtowt.test", hashed_password="x", role="marins"
    )
    db.add(master)
    await db.flush()
    await w.validate_by_client(db, batch=b, client=mine)
    await w.sign_by_master(db, batch=b, user=master)

    resp = await client_confirm_bl_receipt(
        booking.reference, b.id, _hx_request(), client=mine, db=db
    )
    assert resp.status_code == 204
    trigger = json.loads(resp.headers["HX-Trigger"])
    assert trigger["meRefresh"] is True


# ───────────────────── 3. K-6 — bloc de bienvenue générique ────────────────


def test_booking_done_template_has_welcome_block():
    src = templates.env.loader.get_source(templates.env, "client/booking_done.html")[0]
    assert 'id="welcome-block"' in src
    assert "booking_welcome_title" in src
    assert "/me/bookings" in src
    assert "/me/documents" in src
    assert "/me/track/" in src


@pytest.mark.parametrize("lang", ["fr", "en", "es", "pt-br", "vi"])
def test_booking_welcome_catalog_key_resolves_in_every_language(lang):
    """La clé n'existe qu'en fr/en (repli propre vérifié) : les autres langues
    doivent quand même recevoir un texte non vide (repli FR), jamais la clé brute."""
    from app.i18n import t

    resolved = t("booking_welcome_title", lang)
    assert resolved != "booking_welcome_title"
    assert resolved


# ───────────────────── 4. Sources — motif HTMX & hygiène des gabarits ──────


def test_notifications_template_has_meRefresh_wrapper_and_empty_state():
    src = templates.env.loader.get_source(templates.env, "client/notifications.html")[0]
    assert "meRefresh" in src
    assert "empty-state" in src
    assert 'hx-post="/me/notifications/' in src


def test_messages_template_has_empty_state():
    src = templates.env.loader.get_source(templates.env, "client/messages.html")[0]
    assert "empty-state" in src


def test_booking_detail_template_has_meRefresh_wrapper():
    src = templates.env.loader.get_source(templates.env, "client/booking_detail.html")[0]
    assert "meRefresh" in src
    assert 'hx-post="/me/bookings/{{ booking.reference }}/voyage-public"' in src


def test_bl_list_template_has_meRefresh_wrapper_and_hx_confirm():
    src = templates.env.loader.get_source(templates.env, "client/bl_list.html")[0]
    assert "meRefresh" in src
    assert "hx-confirm=" in src
    # data-confirm est remplacé (pas doublé) pour éviter un double window.confirm() —
    # seule la mention en commentaire explique pourquoi ; aucun attribut ne survit.
    assert 'data-confirm="' not in src


@pytest.mark.parametrize(
    "tpl",
    [
        "client/notifications.html",
        "client/messages.html",
        "client/booking_detail.html",
        "client/booking_done.html",
        "client/bl_list.html",
    ],
)
def test_no_dead_links_introduced(tpl):
    src = templates.env.loader.get_source(templates.env, tpl)[0]
    assert 'href="#"' not in src
