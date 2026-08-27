"""Vente à bord — règlement (espèces & webhook carte), idempotence, stock,
registre. Base SQLite in-memory (fixtures ``db`` / ``staff_user``)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.onboard_cashbox import CashboxMovement
from app.models.onboard_sales import OnboardProduct
from app.models.vessel import Vessel
from app.services import onboard_sales as svc


async def _setup_sale(db, staff_user, *, stock=Decimal("10"), qty=Decimal("2")):
    vessel = Vessel(code="ANE", name="Anemos")
    db.add(vessel)
    await db.flush()
    product = OnboardProduct(
        sku="CAF-250",
        label="Café moulu 250 g",
        kind="bien",
        unit_price=Decimal("6.50"),
        currency="EUR",
        unit="pièce",
        tracks_stock=True,
    )
    db.add(product)
    await db.flush()
    await svc.add_stock_entry(
        db,
        vessel_id=vessel.id,
        product=product,
        qty=stock,
        reason="avitaillement",
        recorded_by_id=staff_user.id,
    )
    sale = await svc.create_sale(
        db,
        vessel_id=vessel.id,
        currency="EUR",
        buyer_name="Marin X",
        recorded_by_id=staff_user.id,
    )
    await svc.add_line(db, sale, product=product, qty=qty)
    return vessel, product, sale


async def _count_vente_movements(db) -> int:
    return await db.scalar(
        select(func.count())
        .select_from(CashboxMovement)
        .where(CashboxMovement.category == "vente_a_bord")
    )


@pytest.mark.asyncio
async def test_reference_format(db, staff_user):
    vessel, _product, sale = await _setup_sale(db, staff_user)
    assert sale.reference.startswith("VB-")
    assert sale.reference.endswith("-0001")


@pytest.mark.asyncio
async def test_settle_cash_posts_cashbox_and_decrements_stock(db, staff_user):
    vessel, product, sale = await _setup_sale(db, staff_user)
    assert sale.total == Decimal("13.00")  # 6.50 × 2

    settled = await svc.settle_sale(db, sale, payment_method="cash", recorded_by_id=staff_user.id)
    assert settled is True
    assert sale.status == "paid"
    assert sale.payment_method == "cash"
    assert sale.cashbox_movement_id is not None

    mov = await db.get(CashboxMovement, sale.cashbox_movement_id)
    assert mov.category == "vente_a_bord"
    assert mov.currency == "EUR"
    assert mov.amount == Decimal("13.00")  # encaissement positif

    # Stock : 10 − 2 = 8
    assert await svc.stock_on_hand(db, vessel.id, product.id) == Decimal("8")


@pytest.mark.asyncio
async def test_settle_is_idempotent(db, staff_user):
    _vessel, _product, sale = await _setup_sale(db, staff_user)
    assert await svc.settle_sale(db, sale, payment_method="cash") is True
    # Rejeu : no-op, aucun second mouvement de caisse.
    assert await svc.settle_sale(db, sale, payment_method="cash") is False
    assert await _count_vente_movements(db) == 1


@pytest.mark.asyncio
async def test_register_lists_entry_and_exit(db, staff_user):
    vessel, _product, sale = await _setup_sale(db, staff_user)
    await svc.settle_sale(db, sale, payment_method="cash")
    rows = await svc.register_rows(db, vessel.id)
    # 1 entrée (avitaillement) + 1 sortie (vente)
    assert len(rows) == 2
    reasons = {r["reason"] for r in rows}
    assert reasons == {"avitaillement", "vente"}
    exit_row = next(r for r in rows if r["reason"] == "vente")
    assert exit_row["qty_out"] == Decimal("2")
    assert exit_row["sale_reference"] == sale.reference
    assert exit_row["regime"] == "franchise"


@pytest.mark.asyncio
async def test_cancel_only_when_unsettled(db, staff_user):
    _vessel, _product, sale = await _setup_sale(db, staff_user)
    await svc.settle_sale(db, sale, payment_method="cash")
    with pytest.raises(svc.OnboardSalesError):
        await svc.cancel_sale(db, sale)


@pytest.mark.asyncio
async def test_webhook_settle_idempotent(db, staff_user):
    # Import tardif : le routeur importe segno + stripe (présents en CI).
    from app.routers.onboard_sales_router import _settle_from_session

    _vessel, _product, sale = await _setup_sale(db, staff_user)
    # La vente porte l'identifiant de la session qui la paie : le webhook
    # vérifie désormais cette correspondance avant d'écrire en caisse.
    sale.stripe_checkout_session_id = "cs_test_123"
    await db.flush()
    session_obj = {
        "id": "cs_test_123",
        "payment_status": "paid",
        "payment_intent": "pi_test_123",
        "metadata": {"sale_id": str(sale.id), "reference": sale.reference},
    }
    await _settle_from_session(db, session_obj)
    assert sale.status == "paid"
    assert sale.payment_method == "card"
    assert sale.stripe_payment_intent_id == "pi_test_123"

    # Rejeu du même event (Stripe redélivre) : aucun doublon.
    await _settle_from_session(db, session_obj)
    assert await _count_vente_movements(db) == 1


@pytest.mark.asyncio
async def test_reconcile_settles_paid_pending_sale(db, staff_user, monkeypatch):
    """Réconciliation à l'affichage : une vente en attente dont Stripe confirme
    le paiement est soldée, même sans webhook. Idempotent avec le webhook."""
    from types import SimpleNamespace

    from app.routers import onboard_sales_router as r

    _vessel, _product, sale = await _setup_sale(db, staff_user)
    sale.status = "pending_payment"
    sale.stripe_checkout_session_id = "cs_test_ret"
    await db.flush()

    monkeypatch.setattr(r.stripe_svc, "is_configured", lambda: True)

    async def fake_retrieve(session_id):
        assert session_id == "cs_test_ret"
        return SimpleNamespace(payment_status="paid", payment_intent="pi_ret_1")

    monkeypatch.setattr(r.stripe_svc, "retrieve_session", fake_retrieve)

    await r._reconcile_pending_card_payment(db, sale, recorded_by_id=staff_user.id)
    assert sale.status == "paid"
    assert sale.payment_method == "card"
    assert sale.stripe_payment_intent_id == "pi_ret_1"
    assert await _count_vente_movements(db) == 1

    # Webhook tardif / ré-affichage : pas de second encaissement.
    await r._reconcile_pending_card_payment(db, sale, recorded_by_id=staff_user.id)
    assert await _count_vente_movements(db) == 1


@pytest.mark.asyncio
async def test_reconcile_noop_when_unpaid(db, staff_user, monkeypatch):
    """Session non payée → la vente reste en attente, aucun encaissement."""
    from types import SimpleNamespace

    from app.routers import onboard_sales_router as r

    _vessel, _product, sale = await _setup_sale(db, staff_user)
    sale.status = "pending_payment"
    sale.stripe_checkout_session_id = "cs_test_unpaid"
    await db.flush()

    monkeypatch.setattr(r.stripe_svc, "is_configured", lambda: True)

    async def fake_retrieve(session_id):
        return SimpleNamespace(payment_status="unpaid", payment_intent=None)

    monkeypatch.setattr(r.stripe_svc, "retrieve_session", fake_retrieve)

    await r._reconcile_pending_card_payment(db, sale, recorded_by_id=staff_user.id)
    assert sale.status == "pending_payment"
    assert await _count_vente_movements(db) == 0


@pytest.mark.asyncio
async def test_create_session_prefixes_sku(monkeypatch):
    """La référence produit (SKU) préfixe le libellé envoyé à Stripe ; une ligne
    sans produit du catalogue garde son libellé nu."""
    from types import SimpleNamespace

    from app.services import stripe_checkout as sc

    monkeypatch.setattr(sc.settings, "stripe_secret_key", "sk_test_x")
    # La voie carte exige désormais aussi le secret de webhook : sans canal de
    # confirmation, un lien de paiement encaisserait sans jamais remonter.
    monkeypatch.setattr(sc.settings, "stripe_webhook_secret", "whsec_x")
    captured: dict = {}

    def fake_sync(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="cs_test", url="https://checkout.stripe.com/x")

    monkeypatch.setattr(sc, "_create_session_sync", fake_sync)

    sale = SimpleNamespace(id=1, reference="VB-2026-0001", currency="EUR")
    line_known = SimpleNamespace(
        product_id=7, label="Café moulu 250 g", qty=Decimal("2"), line_total=Decimal("13.00")
    )
    line_free = SimpleNamespace(
        product_id=None, label="Service divers", qty=Decimal("1"), line_total=Decimal("5.00")
    )
    await sc.create_session(
        sale,
        [line_known, line_free],
        success_url="s",
        cancel_url="c",
        sku_by_product_id={7: "CAF-250"},
    )
    names = [li["price_data"]["product_data"]["name"] for li in captured["line_items"]]
    assert names[0] == "[CAF-250] Café moulu 250 g ×2"
    assert names[1] == "Service divers ×1"


def test_qr_svg_is_responsive():
    """Le QR n'a plus de dimension fixe (omitsize) → il épouse son conteneur."""
    from app.routers.onboard_sales_router import _qr_svg

    svg = _qr_svg("https://checkout.stripe.com/pay/cs_test_abc")
    assert "viewBox" in svg
    assert "width=" not in svg


@pytest.mark.asyncio
async def test_create_product_autogenerates_sku(db, staff_user):
    """Le SKU est attribué automatiquement (ART-XXXX), jamais saisi ; unique."""
    from app.routers.onboard_sales_router import create_product

    resp = await create_product(
        label="Café moulu 250 g",
        kind="bien",
        unit_price="6.50",
        currency="EUR",
        unit="pièce",
        tracks_stock="on",
        notes="",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    prods = (await db.execute(select(OnboardProduct))).scalars().all()
    assert len(prods) == 1
    assert prods[0].sku == f"ART-{prods[0].id:04d}"
    assert not prods[0].sku.startswith("__pending")

    # 2e produit → SKU distinct (pas de collision, pas de saisie).
    await create_product(
        label="Thé vert",
        kind="bien",
        unit_price="4.00",
        currency="EUR",
        unit="pièce",
        tracks_stock="on",
        notes="",
        db=db,
        user=staff_user,
    )
    skus = [p.sku for p in (await db.execute(select(OnboardProduct))).scalars().all()]
    assert len(set(skus)) == 2


# ── Garde-fous de saisie (audit 2026-08-27, lot 1) ──────────────────────────
#
# Les services revalident ce que les routeurs ont déjà validé : une valeur non
# finie peut aussi venir d'un import, d'un script ou d'un appelant interne. Les
# tables visées étant append-only et sans route de suppression, une écriture
# aberrante y serait définitive.


@pytest.mark.asyncio
async def test_stock_entry_rejects_non_finite_qty(db, staff_user):
    vessel = Vessel(code="ANE", name="Anemos")
    db.add(vessel)
    await db.flush()
    product = OnboardProduct(
        sku="CAF-250", label="Café", kind="bien", unit_price=Decimal("6.50"), currency="EUR"
    )
    db.add(product)
    await db.flush()
    for bad in (Decimal("nan"), Decimal("Infinity")):
        with pytest.raises(svc.OnboardSalesError):
            await svc.add_stock_entry(
                db, vessel_id=vessel.id, product=product, qty=bad, reason="avitaillement"
            )
    # Aucun mouvement n'a été écrit au registre.
    assert await svc.stock_on_hand(db, vessel_id=vessel.id, product_id=product.id) == Decimal("0")


@pytest.mark.asyncio
async def test_add_line_rejects_non_finite_and_non_positive_qty(db, staff_user):
    _vessel, product, sale = await _setup_sale(db, staff_user)
    for bad in (Decimal("nan"), Decimal("0"), Decimal("-1")):
        with pytest.raises(svc.OnboardSalesError):
            await svc.add_line(db, sale, product=product, qty=bad)
    # Le total de la vente n'a pas bougé (2 × 6,50 posés par _setup_sale).
    await svc.recompute_total(db, sale)
    assert sale.total == Decimal("13.00")


@pytest.mark.asyncio
async def test_cashbox_rejects_non_finite_amount(db, staff_user):
    """Le cas qui rendait un solde de caisse définitivement NaN."""
    from app.services import cashbox as cashbox_svc

    vessel = Vessel(code="ANE", name="Anemos")
    db.add(vessel)
    await db.flush()
    cb = await cashbox_svc.get_or_create(db, vessel.id)
    for bad in (Decimal("nan"), Decimal("Infinity"), Decimal("-Infinity")):
        with pytest.raises(cashbox_svc.CashboxError):
            await cashbox_svc.add_movement(
                db,
                cb,
                amount=bad,
                currency="EUR",
                category="depot_recharge",
                description="tentative",
            )
    # Aucun mouvement écrit : le solde reste calculable.
    # (`cashbox.balances()` n'est pas appelé ici — il utilise `greatest`/`least`,
    # non supportés par SQLite ; c'est la raison de sa couverture nulle, relevée
    # à l'audit et laissée en l'état, hors périmètre de ce lot.)
    total = await db.scalar(
        select(func.count()).select_from(CashboxMovement).where(CashboxMovement.cashbox_id == cb.id)
    )
    assert total == 0


@pytest.mark.asyncio
async def test_settle_refuses_cancelled_sale(db, staff_user):
    """Une vente annulée ne doit jamais produire d'écriture de caisse."""
    _vessel, _product, sale = await _setup_sale(db, staff_user)
    await svc.cancel_sale(db, sale)
    assert sale.status == "cancelled"
    before = await _count_vente_movements(db)
    with pytest.raises(svc.OnboardSalesError):
        await svc.settle_sale(db, sale, payment_method="cash", recorded_by_id=staff_user.id)
    assert await _count_vente_movements(db) == before


@pytest.mark.asyncio
async def test_cancel_sale_success_path(db, staff_user):
    """Le chemin nominal d'annulation n'était couvert par aucun test."""
    _vessel, _product, sale = await _setup_sale(db, staff_user)
    await svc.cancel_sale(db, sale)
    assert sale.status == "cancelled"
    assert sale.cancelled_at is not None
    assert sale.cashbox_movement_id is None


# ── Fermeture du lien Stripe (audit 2026-08-27, lot 2) ──────────────────────
#
# Sans fermeture, la session restait payable ~24 h après un encaissement en
# espèces ou une annulation : le client qui avait déjà scanné le QR pouvait
# payer une seconde fois, sans trace ni voie de remboursement.


def _stripe_on(monkeypatch):
    from app.services import stripe_checkout as sc

    monkeypatch.setattr(sc.settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(sc.settings, "stripe_webhook_secret", "whsec_x")
    return sc


def test_card_path_requires_both_secrets(monkeypatch):
    """Secure-by-default : une clé sans webhook n'ouvre pas la voie carte."""
    from app.services import stripe_checkout as sc

    monkeypatch.setattr(sc.settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(sc.settings, "stripe_webhook_secret", None)
    # L'API reste joignable (on doit pouvoir fermer/réconcilier l'existant)…
    assert sc.is_configured() is True
    # …mais aucun nouveau lien ne peut être proposé au client.
    assert sc.card_payments_enabled() is False

    monkeypatch.setattr(sc.settings, "stripe_secret_key", None)
    assert sc.is_configured() is False
    assert sc.card_payments_enabled() is False


@pytest.mark.asyncio
async def test_expire_session_closes_an_open_session(monkeypatch):
    from types import SimpleNamespace

    sc = _stripe_on(monkeypatch)
    expired: dict = {}

    async def fake_retrieve(session_id):
        return SimpleNamespace(id=session_id, status="open", payment_status="unpaid")

    monkeypatch.setattr(sc, "retrieve_session", fake_retrieve)
    monkeypatch.setattr(
        sc.stripe.checkout.Session,
        "expire",
        lambda sid, **kw: expired.setdefault("id", sid) or SimpleNamespace(status="expired"),
    )
    assert await sc.expire_session("cs_open") == "expired"
    assert expired["id"] == "cs_open"


@pytest.mark.asyncio
async def test_expire_session_refuses_when_already_paid(monkeypatch):
    """Le cas dangereux : le client a payé pendant qu'on cliquait."""
    from types import SimpleNamespace

    sc = _stripe_on(monkeypatch)

    async def fake_retrieve(session_id):
        return SimpleNamespace(id=session_id, status="complete", payment_status="paid")

    monkeypatch.setattr(sc, "retrieve_session", fake_retrieve)
    with pytest.raises(sc.StripeSessionAlreadyPaid):
        await sc.expire_session("cs_paid")


@pytest.mark.asyncio
async def test_confirm_cash_closes_the_payment_link_first(db, staff_user, monkeypatch):
    """L'encaissement espèces doit fermer le lien avant d'écrire en caisse."""
    from app.routers import onboard_sales_router as r

    sc = _stripe_on(monkeypatch)
    _vessel, _product, sale = await _setup_sale(db, staff_user)
    sale.stripe_checkout_session_id = "cs_open"
    sale.status = "pending_payment"
    await db.flush()

    calls: list[str] = []

    async def fake_expire(session_id):
        calls.append(session_id)
        return "expired"

    monkeypatch.setattr(sc, "expire_session", fake_expire)
    await r.confirm_cash(sale.reference, db=db, user=staff_user)

    assert calls == ["cs_open"], "le lien Stripe n'a pas été fermé"
    assert sale.status == "paid"
    assert sale.payment_method == "cash"


@pytest.mark.asyncio
async def test_confirm_cash_refuses_when_link_cannot_be_closed(db, staff_user, monkeypatch):
    """Stripe injoignable : on refuse plutôt que d'exposer à un double débit."""
    from fastapi import HTTPException

    from app.routers import onboard_sales_router as r

    sc = _stripe_on(monkeypatch)
    _vessel, _product, sale = await _setup_sale(db, staff_user)
    sale.stripe_checkout_session_id = "cs_open"
    sale.status = "pending_payment"
    await db.flush()

    async def boom(session_id):
        raise sc.StripeCheckoutError("réseau indisponible")

    monkeypatch.setattr(sc, "expire_session", boom)
    with pytest.raises(HTTPException) as exc:
        await r.confirm_cash(sale.reference, db=db, user=staff_user)
    assert exc.value.status_code == 502
    # Rien n'a été encaissé.
    assert sale.status == "pending_payment"
    assert sale.cashbox_movement_id is None
    assert await _count_vente_movements(db) == 0


@pytest.mark.asyncio
async def test_cancel_refuses_when_client_already_paid(db, staff_user, monkeypatch):
    """Annuler une vente que le client vient de régler par carte est refusé."""
    from fastapi import HTTPException

    from app.routers import onboard_sales_router as r

    sc = _stripe_on(monkeypatch)
    _vessel, _product, sale = await _setup_sale(db, staff_user)
    sale.stripe_checkout_session_id = "cs_paid"
    sale.status = "pending_payment"
    await db.flush()

    async def already_paid(session_id):
        raise sc.StripeSessionAlreadyPaid("déjà réglée")

    monkeypatch.setattr(sc, "expire_session", already_paid)
    with pytest.raises(HTTPException) as exc:
        await r.cancel_sale_route(sale.reference, db=db, user=staff_user)
    assert exc.value.status_code == 409
    assert sale.status == "pending_payment"


@pytest.mark.asyncio
async def test_expired_event_never_resurrects_a_cancelled_sale(db, staff_user):
    """Une annulation est une décision, pas un état transitoire."""
    _vessel, _product, sale = await _setup_sale(db, staff_user)
    sale.status = "cancelled"
    await db.flush()
    await svc.revert_to_draft(db, sale)
    assert sale.status == "cancelled"


@pytest.mark.asyncio
async def test_expired_event_reverts_a_pending_sale(db, staff_user):
    _vessel, _product, sale = await _setup_sale(db, staff_user)
    sale.status = "pending_payment"
    sale.stripe_checkout_session_id = "cs_x"
    await db.flush()
    await svc.revert_to_draft(db, sale)
    assert sale.status == "draft"
    assert sale.stripe_checkout_session_id is None


@pytest.mark.asyncio
async def test_card_payment_on_a_cash_settled_sale_raises_an_incident(db, staff_user):
    """Double débit : la garde d'idempotence tenait, mais en silence."""
    from app.models.notification import Notification
    from app.routers import onboard_sales_router as r

    _vessel, _product, sale = await _setup_sale(db, staff_user)
    sale.stripe_checkout_session_id = "cs_x"  # un lien CB avait été généré
    await db.flush()
    await svc.settle_sale(db, sale, payment_method="cash", recorded_by_id=staff_user.id)
    assert sale.status == "paid"

    # Le client paie malgré tout par carte : le webhook arrive.
    await r._settle_from_session(
        db,
        {
            "id": "cs_x",
            "payment_status": "paid",
            "payment_intent": "pi_double",
            "metadata": {"sale_id": str(sale.id)},
        },
    )
    # Aucun second mouvement de caisse (idempotence préservée)…
    assert await _count_vente_movements(db) == 1
    # …mais l'incident est désormais visible du siège.
    notifs = (
        (
            await db.execute(
                select(Notification).where(Notification.type == "onboard_payment_incident")
            )
        )
        .scalars()
        .all()
    )
    assert len(notifs) == 1
    assert sale.reference in notifs[0].title
    assert "pi_double" in (notifs[0].detail or "")


@pytest.mark.asyncio
async def test_webhook_replay_of_the_same_payment_is_not_an_incident(db, staff_user):
    """Un rejeu du même paiement reste un no-op normal, pas une alerte."""
    from app.models.notification import Notification
    from app.routers import onboard_sales_router as r

    _vessel, _product, sale = await _setup_sale(db, staff_user)
    sale.stripe_checkout_session_id = "cs_x"
    await db.flush()
    obj = {
        "id": "cs_x",
        "payment_status": "paid",
        "payment_intent": "pi_1",
        "metadata": {"sale_id": str(sale.id)},
    }
    await r._settle_from_session(db, obj)
    await r._settle_from_session(db, obj)  # redélivrance Stripe
    assert await _count_vente_movements(db) == 1
    assert (
        await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.type == "onboard_payment_incident")
        )
        == 0
    )


# ── Durcissement du webhook (audit 2026-08-27, lot 3) ───────────────────────
#
# Le webhook ne contrôlait que `payment_status` : il faisait confiance à
# `metadata.sale_id` pour désigner la vente, puis écrivait en caisse le total
# applicatif sans jamais regarder ce que Stripe avait réellement encaissé.


def _fake_request(body: bytes):
    """Requête Starlette minimale — permet d'exercer la vraie route webhook.

    Aucun test du module ne passait jusqu'ici par une route ; c'est ce qui avait
    laissé le défaut de permission `marins` invisible jusqu'au test à bord.
    """
    from starlette.requests import Request

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/webhooks/stripe",
        "headers": [(b"stripe-signature", b"t=1,v1=deadbeef")],
        "query_string": b"",
    }
    return Request(scope, receive)


async def _pending_card_sale(db, staff_user, session_id="cs_x"):
    _vessel, _product, sale = await _setup_sale(db, staff_user)
    sale.status = "pending_payment"
    sale.payment_method = "card"
    sale.stripe_checkout_session_id = session_id
    await db.flush()
    return sale


@pytest.mark.asyncio
async def test_webhook_refuses_a_divergent_amount(db, staff_user):
    """Le montant encaissé doit correspondre au montant attendu."""
    from app.models.notification import Notification
    from app.routers import onboard_sales_router as r

    sale = await _pending_card_sale(db, staff_user)  # total 13,00 EUR
    await r._settle_from_session(
        db,
        {
            "id": "cs_x",
            "payment_status": "paid",
            "payment_intent": "pi_1",
            "currency": "eur",
            "amount_total": 100,  # 1,00 € au lieu de 13,00 €
            "metadata": {"sale_id": str(sale.id)},
        },
    )
    assert sale.status == "pending_payment"
    assert await _count_vente_movements(db) == 0
    incident = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.type == "onboard_payment_incident")
    )
    assert incident == 1


@pytest.mark.asyncio
async def test_webhook_refuses_an_event_from_another_environment(db, staff_user):
    """Un compte Stripe partagé diffuse ses événements à tous ses endpoints."""
    from app.routers import onboard_sales_router as r

    sale = await _pending_card_sale(db, staff_user)
    await r._settle_from_session(
        db,
        {
            "id": "cs_x",
            "payment_status": "paid",
            "payment_intent": "pi_1",
            "metadata": {"sale_id": str(sale.id), "env": "staging-ailleurs"},
        },
    )
    assert sale.status == "pending_payment"
    assert await _count_vente_movements(db) == 0


@pytest.mark.asyncio
async def test_webhook_refuses_a_session_that_is_not_the_expected_one(db, staff_user):
    from app.routers import onboard_sales_router as r

    sale = await _pending_card_sale(db, staff_user, session_id="cs_attendue")
    await r._settle_from_session(
        db,
        {
            "id": "cs_autre",
            "payment_status": "paid",
            "payment_intent": "pi_1",
            "metadata": {"sale_id": str(sale.id)},
        },
    )
    assert sale.status == "pending_payment"
    assert await _count_vente_movements(db) == 0


@pytest.mark.asyncio
async def test_webhook_route_is_idempotent_per_event_id(db, staff_user, monkeypatch):
    """Deux livraisons du même event.id : la seconde ne touche à rien."""
    from app.models.stripe_event import StripeWebhookEvent
    from app.routers import onboard_sales_router as r

    sale = await _pending_card_sale(db, staff_user)
    event = {
        "id": "evt_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_x",
                "payment_status": "paid",
                "payment_intent": "pi_1",
                "currency": "eur",
                "amount_total": 1300,
                "metadata": {"sale_id": str(sale.id)},
            }
        },
    }
    monkeypatch.setattr(r.stripe_svc.settings, "stripe_webhook_secret", "whsec_x")
    monkeypatch.setattr(r.stripe_svc, "construct_event", lambda payload, sig: event)

    first = await r.stripe_webhook(_fake_request(b"{}"), db=db)
    assert first.status_code == 200
    assert await _count_vente_movements(db) == 1

    second = await r.stripe_webhook(_fake_request(b"{}"), db=db)
    assert second.status_code == 200
    assert b"duplicate" in second.body
    assert await _count_vente_movements(db) == 1
    # Une seule ligne au journal des événements.
    assert await db.scalar(select(func.count()).select_from(StripeWebhookEvent)) == 1


@pytest.mark.asyncio
async def test_webhook_route_rejects_an_invalid_signature(db, monkeypatch):
    from app.routers import onboard_sales_router as r

    monkeypatch.setattr(r.stripe_svc.settings, "stripe_webhook_secret", "whsec_x")

    def bad_signature(payload, sig):
        raise r.stripe_svc.StripeCheckoutError("signature invalide")

    monkeypatch.setattr(r.stripe_svc, "construct_event", bad_signature)
    resp = await r.stripe_webhook(_fake_request(b"{}"), db=db)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_webhook_route_returns_503_without_webhook_secret(db, monkeypatch):
    """Secure-by-default : pas de secret, pas de traitement."""
    from app.routers import onboard_sales_router as r

    monkeypatch.setattr(r.stripe_svc.settings, "stripe_webhook_secret", None)
    resp = await r.stripe_webhook(_fake_request(b"{}"), db=db)
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_webhook_asks_for_a_retry_when_the_period_is_closed(db, staff_user, monkeypatch):
    """Échec transitoire : 500 pour que Stripe rejoue, plutôt que de perdre
    l'écriture d'un paiement déjà encaissé."""
    from app.routers import onboard_sales_router as r
    from app.services.cashbox import PeriodClosed

    sale = await _pending_card_sale(db, staff_user)
    event = {
        "id": "evt_closed",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_x",
                "payment_status": "paid",
                "payment_intent": "pi_1",
                "metadata": {"sale_id": str(sale.id)},
            }
        },
    }
    monkeypatch.setattr(r.stripe_svc.settings, "stripe_webhook_secret", "whsec_x")
    monkeypatch.setattr(r.stripe_svc, "construct_event", lambda payload, sig: event)

    async def closed(*a, **kw):
        raise PeriodClosed("Période clôturée")

    monkeypatch.setattr(r.svc, "settle_sale", closed)
    resp = await r.stripe_webhook(_fake_request(b"{}"), db=db)
    assert resp.status_code == 500
    assert b"retry" in resp.body


@pytest.mark.asyncio
async def test_a_retry_after_a_transient_failure_actually_settles(db, staff_user, monkeypatch):
    """Le rejeu qui suit un 500 ne doit pas être rejeté comme doublon.

    La marque d'idempotence est posée avant traitement pour sérialiser les
    livraisons concurrentes ; sur échec transitoire elle doit être retirée,
    sinon le paiement serait perdu malgré le 500 qui demandait le rejeu.
    """
    from app.routers import onboard_sales_router as r
    from app.services.cashbox import PeriodClosed

    sale = await _pending_card_sale(db, staff_user)
    event = {
        "id": "evt_retry",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_x",
                "payment_status": "paid",
                "payment_intent": "pi_1",
                "currency": "eur",
                "amount_total": 1300,
                "metadata": {"sale_id": str(sale.id)},
            }
        },
    }
    monkeypatch.setattr(r.stripe_svc.settings, "stripe_webhook_secret", "whsec_x")
    monkeypatch.setattr(r.stripe_svc, "construct_event", lambda payload, sig: event)

    real_settle = r.svc.settle_sale

    async def closed(*a, **kw):
        raise PeriodClosed("Période clôturée")

    monkeypatch.setattr(r.svc, "settle_sale", closed)
    first = await r.stripe_webhook(_fake_request(b"{}"), db=db)
    assert first.status_code == 500
    assert await _count_vente_movements(db) == 0

    # Le siège rouvre la période ; Stripe rejoue le même événement.
    monkeypatch.setattr(r.svc, "settle_sale", real_settle)
    second = await r.stripe_webhook(_fake_request(b"{}"), db=db)
    assert second.status_code == 200
    assert b"duplicate" not in second.body
    assert await _count_vente_movements(db) == 1
    assert sale.status == "paid"
