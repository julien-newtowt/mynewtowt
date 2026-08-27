"""Vente à bord — routes de l'espace commandant (``/captain/ventes``).

Le commandant gère un catalogue de biens/services, l'inventaire par navire,
crée des ventes et les encaisse (espèces → caisse de bord ; carte → Stripe
Checkout, cf. Lot 2). Toutes les ventes sont détaxées (avitaillement /
franchise) ; le registre des mouvements de stock est exportable.

Permissions : lecture ``captain/C`` ; mutations ``captain/M``. Le rôle
``marins`` (commandant) passe de C à CM via l'écran /admin/permissions.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import segno
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.leg import Leg
from app.models.onboard_sales import (
    PAYMENT_METHOD_LABELS,
    SALE_STATUS_LABELS,
    STOCK_REASON_LABELS,
    SUPPORTED_CURRENCIES,
    OnboardProduct,
    OnboardSale,
    OnboardSaleLine,
)
from app.models.stripe_event import StripeWebhookEvent
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services import notifications
from app.services import onboard_sales as svc
from app.services import stripe_checkout as stripe_svc
from app.services.activity import record as activity_record
from app.services.cashbox import CashboxError, PeriodClosed
from app.templating import templates
from app.utils.decimals import CENTS, QTY_STEP, DecimalInputError, parse_decimal

logger = logging.getLogger("onboard_sales")

router = APIRouter(prefix="/captain/ventes", tags=["onboard-sales"])
# Webhook Stripe : monté sous /webhooks/ → exempté de CSRF (cf. app/csrf.py),
# sans auth staff, validé par signature Stripe.
webhook_router = APIRouter(prefix="/webhooks", tags=["stripe"])

# Motifs de mouvement de stock saisissables manuellement (la « vente » est
# générée automatiquement au règlement).
_MANUAL_STOCK_REASONS = ("avitaillement", "retour", "ajustement", "inventaire")


def _parse_decimal(
    raw: str,
    *,
    label: str = "valeur",
    min_value: Decimal | None = None,
    quantize: Decimal | None = None,
) -> Decimal:
    """Parse une saisie numérique et refuse tout ce qui n'est pas fini et borné.

    ``NaN``/``Infinity`` sont des littéraux ``Decimal`` valides : sans le
    contrôle de finitude de ``utils.decimals``, ils traversaient jusqu'au
    registre de stock, dont aucune route ne permet de supprimer une ligne.
    """
    try:
        return parse_decimal(raw, label=label, min_value=min_value, quantize=quantize)
    except DecimalInputError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


async def _default_leg_id(db: AsyncSession, vessel_id: int) -> int | None:
    """Leg « courant » du navire (dernier leg par id) pour rattacher la vente."""
    return await db.scalar(
        select(Leg.id).where(Leg.vessel_id == vessel_id).order_by(Leg.id.desc()).limit(1)
    )


class _TransientSettlementError(Exception):
    """Échec de règlement rejouable : le webhook doit répondre 500, pas 200.

    Répondre 200 acquitte l'événement auprès de Stripe, qui cesse de le
    redélivrer. Sur une cause temporaire — période de caisse clôturée alors
    qu'un paiement était en vol, indisponibilité base — l'écriture était perdue
    définitivement, pour un paiement pourtant encaissé.
    """


async def _release_checkout_session(sale: OnboardSale) -> None:
    """Ferme le lien de paiement Stripe encore ouvert sur cette vente.

    À appeler **avant** tout geste qui rend la vente non payable par carte —
    encaissement en espèces, annulation, régénération d'un lien. Sans cela le
    lien restait vivant : le client qui avait déjà scanné le QR pouvait payer
    une vente déjà réglée en liquide ou annulée, et ce second débit n'était ni
    tracé ni remboursable depuis l'application.

    En cas d'échec on **lève** plutôt que de continuer : mieux vaut refuser le
    geste et le rejouer avec du réseau que d'exposer le client à un double
    paiement. Les deux cas non bloquants sont l'absence de session et une voie
    carte non configurée (aucune session n'a alors pu être créée).
    """
    if not sale.stripe_checkout_session_id or not stripe_svc.is_configured():
        return
    try:
        await stripe_svc.expire_session(sale.stripe_checkout_session_id)
    except stripe_svc.StripeNotConfigured:
        return
    except stripe_svc.StripeSessionAlreadyPaid as e:
        # Le client vient de payer : poursuivre encaisserait une seconde fois.
        raise HTTPException(status_code=409, detail=str(e)) from e
    except stripe_svc.StripeCheckoutError as e:
        logger.error("Fermeture du lien Stripe impossible (%s) : %s", sale.reference, e)
        raise HTTPException(
            status_code=502,
            detail=(
                "Le lien de paiement n'a pas pu être fermé (Stripe injoignable). "
                "N'encaissez pas maintenant : réessayez une fois le réseau revenu, "
                "sinon le client risque de payer deux fois."
            ),
        ) from e


async def _get_sale_or_404(db: AsyncSession, reference: str) -> OnboardSale:
    sale = (
        await db.execute(select(OnboardSale).where(OnboardSale.reference == reference))
    ).scalar_one_or_none()
    if sale is None:
        raise HTTPException(status_code=404, detail="Vente introuvable")
    return sale


# ───────────────────────────────────────────────────────────────── Hub / vessels


@router.get("", response_class=HTMLResponse)
async def hub(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    # Le commandant rattaché à un navire est redirigé vers son tableau de bord.
    assigned = getattr(user, "assigned_vessel_id", None)
    if assigned:
        return RedirectResponse(url=f"/captain/ventes/{assigned}", status_code=303)
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    return templates.TemplateResponse(
        "staff/onboard_sales/hub.html",
        {"request": request, "user": user, "vessels": vessels},
    )


# ───────────────────────────────────────────────────────────────────── Catalogue


@router.get("/catalogue", response_class=HTMLResponse)
async def catalogue(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    products = list(
        (await db.execute(select(OnboardProduct).order_by(OnboardProduct.label))).scalars().all()
    )
    return templates.TemplateResponse(
        "staff/onboard_sales/catalogue.html",
        {
            "request": request,
            "user": user,
            "products": products,
            "currencies": SUPPORTED_CURRENCIES,
        },
    )


@router.post("/catalogue/products")
async def create_product(
    label: str = Form(...),
    kind: str = Form("bien"),
    unit_price: str = Form(...),
    currency: str = Form("EUR"),
    unit: str = Form("pièce"),
    tracks_stock: str = Form(""),  # checkbox : absent si décochée
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    # La référence (SKU) est attribuée AUTOMATIQUEMENT (format ART-XXXX dérivé
    # de l'id), jamais saisie par l'utilisateur. On insère avec un placeholder
    # unique le temps d'obtenir l'id, puis on fige le SKU définitif.
    if not label.strip():
        raise HTTPException(status_code=400, detail="Désignation requise")
    if currency.upper() not in SUPPORTED_CURRENCIES:
        raise HTTPException(status_code=400, detail="Devise non supportée")
    product = OnboardProduct(
        sku=f"__pending_{uuid4().hex[:16]}",  # placeholder unique, remplacé après flush
        label=label.strip(),
        kind=kind if kind in ("bien", "service") else "bien",
        unit_price=_parse_decimal(
            unit_price, label="prix unitaire", min_value=Decimal("0"), quantize=CENTS
        ),
        currency=currency.upper(),
        unit=unit.strip() or "pièce",
        tracks_stock=(kind != "service") and (tracks_stock in ("on", "true", "1", "yes")),
        notes=notes.strip() or None,
    )
    db.add(product)
    await db.flush()  # id attribué
    product.sku = f"ART-{product.id:04d}"  # référence auto, stable et unique
    await db.flush()
    await activity_record(
        db,
        action="onboard_product_create",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="onboard_product",
        entity_id=product.id,
        detail=f"{product.sku} {product.label}",
    )
    return RedirectResponse(url="/captain/ventes/catalogue", status_code=303)


@router.post("/catalogue/products/{product_id}")
async def update_product(
    product_id: int,
    label: str = Form(...),
    unit_price: str = Form(...),
    unit: str = Form("pièce"),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    product = await db.get(OnboardProduct, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Produit introuvable")
    product.label = label.strip() or product.label
    product.unit_price = _parse_decimal(
        unit_price, label="prix unitaire", min_value=Decimal("0"), quantize=CENTS
    )
    product.unit = unit.strip() or product.unit
    product.notes = notes.strip() or None
    await db.flush()
    await activity_record(
        db,
        action="onboard_product_update",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="onboard_product",
        entity_id=product.id,
        detail=product.sku,
    )
    return RedirectResponse(url="/captain/ventes/catalogue", status_code=303)


@router.post("/catalogue/products/{product_id}/toggle")
async def toggle_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    product = await db.get(OnboardProduct, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Produit introuvable")
    product.is_active = not product.is_active
    await db.flush()
    return RedirectResponse(url="/captain/ventes/catalogue", status_code=303)


# ───────────────────────────────────────────────────────── Tableau de bord navire


@router.get("/{vessel_id}", response_class=HTMLResponse)
async def vessel_dashboard(
    request: Request,
    vessel_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404, detail="Navire introuvable")
    inventory = await svc.current_inventory(db, vessel_id)
    products = list(
        (
            await db.execute(
                select(OnboardProduct)
                .where(OnboardProduct.is_active.is_(True))
                .order_by(OnboardProduct.label)
            )
        )
        .scalars()
        .all()
    )
    sales = list(
        (
            await db.execute(
                select(OnboardSale)
                .where(OnboardSale.vessel_id == vessel_id)
                .order_by(OnboardSale.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        "staff/onboard_sales/vessel.html",
        {
            "request": request,
            "user": user,
            "vessel": vessel,
            "inventory": inventory,
            "products": products,
            "stock_products": [p for p in products if p.tracks_stock],
            "sales": sales,
            "currencies": SUPPORTED_CURRENCIES,
            "stock_reasons": _MANUAL_STOCK_REASONS,
            "stock_reason_labels": STOCK_REASON_LABELS,
            "status_labels": SALE_STATUS_LABELS,
            "payment_labels": PAYMENT_METHOD_LABELS,
        },
    )


@router.post("/{vessel_id}/stock")
async def add_stock(
    vessel_id: int,
    product_id: int = Form(...),
    qty: str = Form(...),
    reason: str = Form("avitaillement"),
    note: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404, detail="Navire introuvable")
    product = await db.get(OnboardProduct, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Produit introuvable")
    if reason not in _MANUAL_STOCK_REASONS:
        raise HTTPException(status_code=400, detail="Motif de mouvement invalide")
    try:
        mov = await svc.add_stock_entry(
            db,
            vessel_id=vessel_id,
            product=product,
            qty=_parse_decimal(qty, label="quantité", quantize=QTY_STEP),
            reason=reason,
            note=note,
            recorded_by_id=user.id,
        )
    except svc.OnboardSalesError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await activity_record(
        db,
        action="onboard_stock_movement",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="onboard_stock_movement",
        entity_id=mov.id,
        detail=f"vessel={vessel_id} {product.sku} {mov.qty} {reason}",
    )
    return RedirectResponse(url=f"/captain/ventes/{vessel_id}", status_code=303)


@router.post("/{vessel_id}/vente")
async def create_sale_route(
    vessel_id: int,
    buyer_name: str = Form(""),
    currency: str = Form("EUR"),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404, detail="Navire introuvable")
    try:
        sale = await svc.create_sale(
            db,
            vessel_id=vessel_id,
            currency=currency,
            leg_id=await _default_leg_id(db, vessel_id),
            buyer_name=buyer_name,
            recorded_by_id=user.id,
        )
    except svc.OnboardSalesError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await activity_record(
        db,
        action="onboard_sale_create",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="onboard_sale",
        entity_id=sale.id,
        detail=sale.reference,
    )
    return RedirectResponse(url=f"/captain/ventes/vente/{sale.reference}", status_code=303)


# ─────────────────────────────────────────────────────────────── Détail d'une vente


@router.get("/vente/{reference}", response_class=HTMLResponse)
async def sale_detail(
    request: Request,
    reference: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    sale = await _get_sale_or_404(db, reference)
    await _reconcile_pending_card_payment(db, sale, recorded_by_id=user.id)
    vessel = await db.get(Vessel, sale.vessel_id)
    lines = list(
        (
            await db.execute(
                select(OnboardSaleLine)
                .where(OnboardSaleLine.sale_id == sale.id)
                .order_by(OnboardSaleLine.id)
            )
        )
        .scalars()
        .all()
    )
    # Produits sélectionnables : actifs, même devise que la vente.
    products = list(
        (
            await db.execute(
                select(OnboardProduct)
                .where(
                    OnboardProduct.is_active.is_(True),
                    OnboardProduct.currency == sale.currency,
                )
                .order_by(OnboardProduct.label)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        "staff/onboard_sales/sale.html",
        {
            "request": request,
            "user": user,
            "sale": sale,
            "vessel": vessel,
            "lines": lines,
            "products": products,
            "status_labels": SALE_STATUS_LABELS,
            "payment_labels": PAYMENT_METHOD_LABELS,
            "stripe_enabled": stripe_svc.card_payments_enabled(),
        },
    )


@router.post("/vente/{reference}/line")
async def add_sale_line(
    reference: str,
    product_id: int = Form(...),
    qty: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    sale = await _get_sale_or_404(db, reference)
    product = await db.get(OnboardProduct, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Produit introuvable")
    try:
        await svc.add_line(
            db, sale, product=product, qty=_parse_decimal(qty, label="quantité", quantize=QTY_STEP)
        )
    except svc.OnboardSalesError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return RedirectResponse(url=f"/captain/ventes/vente/{sale.reference}", status_code=303)


@router.post("/vente/{reference}/line/{line_id}/delete")
async def delete_sale_line(
    reference: str,
    line_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    sale = await _get_sale_or_404(db, reference)
    if sale.status != "draft":
        raise HTTPException(status_code=400, detail="Vente non modifiable")
    line = await db.get(OnboardSaleLine, line_id)
    if line is None or line.sale_id != sale.id:
        raise HTTPException(status_code=404, detail="Ligne introuvable")
    await db.delete(line)
    await db.flush()
    await svc.recompute_total(db, sale)
    return RedirectResponse(url=f"/captain/ventes/vente/{sale.reference}", status_code=303)


@router.post("/vente/{reference}/confirm-cash")
async def confirm_cash(
    reference: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    sale = await _get_sale_or_404(db, reference)
    # Ferme d'abord le lien CB éventuel : un client qui a déjà scanné le QR
    # pourrait sinon payer par carte une vente encaissée en liquide.
    await _release_checkout_session(sale)
    try:
        await svc.settle_sale(db, sale, payment_method="cash", recorded_by_id=user.id)
    except PeriodClosed as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (svc.OnboardSalesError, CashboxError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await activity_record(
        db,
        action="onboard_sale_paid_cash",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="onboard_sale",
        entity_id=sale.id,
        detail=f"{sale.reference} {sale.total} {sale.currency}",
    )
    return RedirectResponse(url=f"/captain/ventes/vente/{sale.reference}", status_code=303)


@router.post("/vente/{reference}/checkout")
async def create_checkout(
    reference: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    """Génère un lien de paiement Stripe (Checkout Session) pour la vente."""
    if not stripe_svc.card_payments_enabled():
        raise HTTPException(
            status_code=503,
            detail="Encaissement carte indisponible (Stripe non configuré). Utilisez les espèces.",
        )
    sale = await _get_sale_or_404(db, reference)
    if sale.is_settled or sale.status == "paid":
        raise HTTPException(status_code=400, detail="Vente déjà réglée.")
    if sale.status not in ("draft", "pending_payment"):
        raise HTTPException(status_code=400, detail="Vente non payable dans cet état.")
    if sale.total <= 0:
        raise HTTPException(status_code=400, detail="Vente sans montant.")
    # Régénération : l'ancienne session était simplement écrasée en base et
    # restait payable — deux liens vivants pour une seule vente.
    await _release_checkout_session(sale)
    lines = (
        (await db.execute(select(OnboardSaleLine).where(OnboardSaleLine.sale_id == sale.id)))
        .scalars()
        .all()
    )
    sku_by_product_id = await _sku_map_for_lines(db, lines)
    base = settings.site_url.rstrip("/")
    try:
        session = await stripe_svc.create_session(
            sale,
            list(lines),
            success_url=f"{base}/captain/ventes/vente/{sale.reference}?paid=1",
            cancel_url=f"{base}/captain/ventes/vente/{sale.reference}",
            sku_by_product_id=sku_by_product_id,
        )
    except stripe_svc.StripeNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except stripe_svc.StripeCheckoutError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    sale.stripe_checkout_session_id = session.id
    sale.status = "pending_payment"
    sale.payment_method = "card"
    await db.flush()
    await activity_record(
        db,
        action="onboard_sale_checkout",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="onboard_sale",
        entity_id=sale.id,
        detail=f"{sale.reference} session={session.id}",
    )
    return RedirectResponse(url=f"/captain/ventes/vente/{sale.reference}/checkout", status_code=303)


@router.get("/vente/{reference}/checkout", response_class=HTMLResponse)
async def checkout_page(
    request: Request,
    reference: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
):
    """Affiche l'URL de paiement + QR code (SVG segno) de la session en cours."""
    sale = await _get_sale_or_404(db, reference)
    if sale.status != "pending_payment" or not sale.stripe_checkout_session_id:
        return RedirectResponse(url=f"/captain/ventes/vente/{sale.reference}", status_code=303)
    try:
        session = await stripe_svc.retrieve_session(sale.stripe_checkout_session_id)
    except stripe_svc.StripeNotConfigured as e:
        # Voie carte fermée : c'est une indisponibilité de service, pas une
        # panne d'amont — même sémantique que la route de création (503).
        raise HTTPException(status_code=503, detail=str(e)) from e
    except stripe_svc.StripeCheckoutError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    pay_url = getattr(session, "url", None)
    # Session expirée / déjà réglée : plus d'URL ouvrable → retour au détail.
    if not pay_url or getattr(session, "status", None) != "open":
        return RedirectResponse(url=f"/captain/ventes/vente/{sale.reference}", status_code=303)
    qr_svg = _qr_svg(pay_url)
    vessel = await db.get(Vessel, sale.vessel_id)
    lines = list(
        (
            await db.execute(
                select(OnboardSaleLine)
                .where(OnboardSaleLine.sale_id == sale.id)
                .order_by(OnboardSaleLine.id)
            )
        )
        .scalars()
        .all()
    )
    sku_by_product_id = await _sku_map_for_lines(db, lines)
    return templates.TemplateResponse(
        "staff/onboard_sales/checkout.html",
        {
            "request": request,
            "user": user,
            "sale": sale,
            "vessel": vessel,
            "pay_url": pay_url,
            "qr_svg": qr_svg,
            "lines": lines,
            "sku_by_product_id": sku_by_product_id,
        },
    )


@router.post("/vente/{reference}/cancel")
async def cancel_sale_route(
    reference: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    sale = await _get_sale_or_404(db, reference)
    # « Rien n'est encaissé » n'était vrai que si le lien cessait d'être payable.
    await _release_checkout_session(sale)
    try:
        await svc.cancel_sale(db, sale)
    except svc.OnboardSalesError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await activity_record(
        db,
        action="onboard_sale_cancel",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="onboard_sale",
        entity_id=sale.id,
        detail=sale.reference,
    )
    return RedirectResponse(url=f"/captain/ventes/vente/{sale.reference}", status_code=303)


# ───────────────────────────────────────────────────────────────── Registre douanier


@router.get("/{vessel_id}/registre", response_class=HTMLResponse)
async def registre(
    request: Request,
    vessel_id: int,
    date_from: str = "",
    date_to: str = "",
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404, detail="Navire introuvable")
    df = _parse_date(date_from)
    dt = _parse_date(date_to, end_of_day=True)
    rows = await svc.register_rows(db, vessel_id, date_from=df, date_to=dt)
    return templates.TemplateResponse(
        "staff/onboard_sales/registre.html",
        {
            "request": request,
            "user": user,
            "vessel": vessel,
            "rows": rows,
            "date_from": date_from,
            "date_to": date_to,
            "reason_labels": STOCK_REASON_LABELS,
        },
    )


@router.get("/{vessel_id}/registre/export.csv")
async def registre_csv(
    vessel_id: int,
    date_from: str = "",
    date_to: str = "",
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> Response:
    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404, detail="Navire introuvable")
    rows = await svc.register_rows(
        db,
        vessel_id,
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to, end_of_day=True),
    )
    csv_text = svc.export_csv(rows, vessel_code=vessel.code)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": (f'attachment; filename="registre-vente-bord-{vessel.code}.csv"')
        },
    )


def _parse_date(raw: str, *, end_of_day: bool = False) -> datetime | None:
    """Borne de période du registre douanier. Refuse une date illisible.

    Renvoyer ``None`` sur entrée invalide désactivait silencieusement le
    filtre : l'utilisateur croyait consulter (ou exporter) un mois, il obtenait
    l'historique complet du navire sans qu'aucun message ne le signale.
    """
    if not raw.strip():
        return None
    try:
        d = datetime.fromisoformat(raw)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Date de filtre invalide (format attendu AAAA-MM-JJ)."
        ) from None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    if end_of_day and d.hour == 0 and d.minute == 0:
        d = d.replace(hour=23, minute=59, second=59)
    return d


def _qr_svg(data: str) -> str:
    """QR code d'une URL → SVG inline (segno, pur-Python, sans JS externe).

    ``omitsize`` retire les attributs ``width``/``height`` fixes : le SVG épouse
    alors la largeur de son conteneur (``.qr-frame``, cf. kairos.css) au lieu de
    déborder — le ``viewBox`` préserve le ratio carré.
    """
    return segno.make(data, error="m").svg_inline(scale=5, border=2, omitsize=True)


async def _sku_map_for_lines(db: AsyncSession, lines) -> dict[int, str]:
    """Mappe ``product_id`` → SKU pour des lignes de vente (référence produit).

    Une ligne peut ne pas être rattachée au catalogue (``product_id`` NULL,
    vente libre) : elle est simplement absente du mapping.
    """
    product_ids = {ln.product_id for ln in lines if ln.product_id is not None}
    if not product_ids:
        return {}
    rows = (
        await db.execute(
            select(OnboardProduct.id, OnboardProduct.sku).where(OnboardProduct.id.in_(product_ids))
        )
    ).all()
    return dict(rows)


async def _flag_duplicate_payment(
    db: AsyncSession, sale: OnboardSale, payment_intent: str | None, *, source: str
) -> None:
    """Signale un paiement carte arrivé sur une vente déjà réglée ou annulée.

    ``settle_sale`` renvoie ``False`` dans ce cas — c'est sa garde d'idempotence
    qui joue, et elle est correcte : elle empêche un second mouvement de caisse.
    Mais elle le faisait **en silence**, alors que la situation signifie que le
    client a payé une fois de trop : l'argent est chez Stripe, le grand livre ne
    le voit pas, et aucune route de remboursement n'existe encore. Sans trace
    exploitable, personne ne pouvait rembourser à froid.

    On ne signale que le vrai doublon : un rejeu de webhook sur une vente déjà
    réglée **par le même paiement** est un no-op normal, pas un incident.
    """
    known = sale.stripe_payment_intent_id
    is_new_card_payment = bool(payment_intent) and payment_intent != known
    if not (is_new_card_payment or sale.status == "cancelled"):
        return
    detail = (
        f"{sale.reference} — paiement carte reçu ({source}) alors que la vente est "
        f"« {sale.status_label} »"
        + (f" (réglée en {sale.payment_method_label})" if sale.payment_method else "")
        + f". payment_intent={payment_intent or '?'}. "
        "Aucun mouvement de caisse n'a été créé : remboursement à traiter côté Stripe."
    )
    logger.error("Incident encaissement vente à bord : %s", detail)
    await activity_record(
        db,
        action="onboard_sale_duplicate_payment",
        user_name=f"stripe-{source}",
        module="captain",
        entity_type="onboard_sale",
        entity_id=sale.id,
        detail=detail,
    )
    await notifications.notify_onboard_payment_incident(
        db, sale_reference=sale.reference, sale_id=sale.id, detail=detail
    )


async def _reconcile_pending_card_payment(
    db: AsyncSession, sale: OnboardSale, *, recorded_by_id: int | None = None
) -> None:
    """Réconcilie une vente CB en attente avec Stripe, à l'affichage du détail.

    Le webhook ``checkout.session.completed`` est la voie primaire de règlement.
    S'il n'aboutit pas (endpoint mal déclaré, mauvais type d'événement souscrit,
    indisponibilité temporaire), une vente pourtant payée resterait « en
    attente ». À l'ouverture d'une vente en attente on interroge donc Stripe :
    si le paiement est confirmé, on solde immédiatement. **Idempotent**
    (``settle_sale`` ignore un règlement déjà posé) → jamais de double
    encaissement avec le webhook. Best-effort : toute erreur Stripe/caisse est
    journalisée sans casser l'affichage.
    """
    if not (
        stripe_svc.is_configured()
        and sale.status == "pending_payment"
        and sale.stripe_checkout_session_id
    ):
        return
    try:
        session = await stripe_svc.retrieve_session(sale.stripe_checkout_session_id)
    except (stripe_svc.StripeNotConfigured, stripe_svc.StripeCheckoutError) as e:
        logger.info("Réconciliation Stripe ignorée (%s) : %s", sale.reference, e)
        return
    if getattr(session, "payment_status", None) not in ("paid", "no_payment_required"):
        return
    # La session vient d'être relue par son identifiant : le contrôle porte donc
    # sur le montant et la devise, qui peuvent avoir divergé si la vente a été
    # modifiée entre la création du lien et le paiement.
    mismatch = _session_matches_sale(
        {
            "id": sale.stripe_checkout_session_id,
            "currency": getattr(session, "currency", None),
            "amount_total": getattr(session, "amount_total", None),
        },
        sale,
    )
    if mismatch:
        detail = f"{sale.reference} — réconciliation refusée : {mismatch}"
        logger.error("Réconciliation Stripe : %s", detail)
        await notifications.notify_onboard_payment_incident(
            db, sale_reference=sale.reference, sale_id=sale.id, detail=detail
        )
        return
    payment_intent = getattr(session, "payment_intent", None)
    if isinstance(payment_intent, dict):
        payment_intent = payment_intent.get("id")
    try:
        settled = await svc.settle_sale(
            db,
            sale,
            payment_method="card",
            payment_intent_id=payment_intent,
            recorded_by_id=recorded_by_id,
        )
    except (svc.OnboardSalesError, CashboxError) as e:
        logger.error("Réconciliation : règlement échoué %s : %s", sale.reference, e)
        await _flag_duplicate_payment(db, sale, payment_intent, source="reconcile")
        return
    if not settled:
        await _flag_duplicate_payment(db, sale, payment_intent, source="reconcile")
    if settled:
        await activity_record(
            db,
            action="onboard_sale_paid_card",
            user_id=recorded_by_id,
            user_name="stripe-reconcile",
            module="captain",
            entity_type="onboard_sale",
            entity_id=sale.id,
            detail=f"{sale.reference} (réconcilié à l'affichage)",
        )


# ───────────────────────────────────────────────────────────── Webhook Stripe


@webhook_router.post("/stripe")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Réception des events Stripe (paiement confirmé) — validé par signature.

    Sans auth staff (monté sous /webhooks/, exempté de CSRF) ; la confiance
    vient de la **signature** ``Stripe-Signature`` vérifiée contre
    ``STRIPE_WEBHOOK_SECRET``. Idempotent : ``settle_sale`` ignore les rejeux.
    """
    if not stripe_svc.webhook_configured():
        return JSONResponse({"error": "not_configured"}, status_code=503)
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe_svc.construct_event(payload, sig)
    except stripe_svc.StripeNotConfigured:
        return JSONResponse({"error": "not_configured"}, status_code=503)
    except stripe_svc.StripeCheckoutError as e:
        logger.warning("Webhook Stripe rejeté : %s", e)
        return JSONResponse({"error": "invalid_signature"}, status_code=400)

    etype = event.get("type", "")
    event_id = event.get("id") or ""
    obj = event.get("data", {}).get("object", {})

    # Idempotence au niveau **événement** : Stripe livre « au moins une fois ».
    # L'insertion sous contrainte d'unicité sérialise les livraisons
    # concurrentes du même événement — la seconde échoue ici et repart en 200
    # sans avoir rien touché.
    seen: StripeWebhookEvent | None = None
    if event_id:
        try:
            # Point de reprise : sur conflit, seule l'insertion est annulée. Un
            # `rollback()` complet annulerait aussi ce que la requête a déjà
            # écrit — et, en test où la transaction couvre plusieurs appels,
            # le traitement de la livraison précédente.
            async with db.begin_nested():
                seen = StripeWebhookEvent(event_id=event_id, event_type=etype[:80])
                db.add(seen)
        except IntegrityError:
            logger.info("Webhook Stripe : événement déjà traité (%s)", event_id)
            return JSONResponse({"received": True, "duplicate": True})

    try:
        if etype in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            await _settle_from_session(db, obj)
        elif etype == "checkout.session.expired":
            await _revert_from_session(db, obj)
        # Tout autre event : accusé de réception (200) sans traitement.
    except _TransientSettlementError as e:
        # Échec **transitoire** (période de caisse clôturée, indisponibilité
        # base) : répondre 200 retirait l'événement de la file de retry Stripe
        # et perdait définitivement l'écriture d'un paiement pourtant encaissé.
        # Un 500 fait rejouer Stripe (jusqu'à 3 jours).
        #
        # La marque d'idempotence doit être **retirée** : la laisser ferait
        # rejeter le rejeu comme doublon, et on perdrait le paiement malgré le
        # 500. On n'a donc consommé l'``event.id`` que pour sérialiser les
        # livraisons concurrentes, pas pour acquitter un traitement qui n'a pas
        # eu lieu.
        if seen is not None:
            await db.delete(seen)
            await db.flush()
        logger.error("Webhook Stripe : échec transitoire, retry demandé — %s", e)
        return JSONResponse({"error": "transient", "retry": True}, status_code=500)
    return JSONResponse({"received": True})


def _session_matches_sale(obj, sale: OnboardSale) -> str | None:
    """Vérifie que l'objet session Stripe correspond bien à cette vente.

    Renvoie ``None`` si tout concorde, sinon le motif du refus. Le webhook ne
    contrôlait jusqu'ici que ``payment_status`` : il faisait confiance à
    ``metadata.sale_id`` pour désigner la vente, puis écrivait en caisse le
    total **applicatif**, sans jamais regarder ce que Stripe avait réellement
    encaissé.

    Le cas concret que cela laissait passer n'est pas un exploit mais une
    mésconfiguration courante : un même compte Stripe servant staging et
    production diffuse chaque événement à **tous** ses endpoints du même mode.
    Un règlement de test à 1,00 € soldait alors, avec une signature parfaitement
    valide, la vente de même identifiant en production — plusieurs centaines
    d'euros portés en caisse sans qu'un centime ait été encaissé pour elle.
    """
    session_id = obj.get("id")
    if session_id and sale.stripe_checkout_session_id != session_id:
        return f"session {session_id} ≠ session attendue {sale.stripe_checkout_session_id}"

    env = (obj.get("metadata") or {}).get("env")
    if env is not None and env != settings.app_env:
        return f"événement émis par l'environnement « {env} »"

    livemode = obj.get("livemode")
    if livemode is not None and bool(livemode) != (settings.app_env == "production"):
        return f"livemode={livemode} incohérent avec app_env={settings.app_env}"

    currency = (obj.get("currency") or "").upper()
    if currency and currency != sale.currency.upper():
        return f"devise {currency} ≠ {sale.currency}"

    amount_total = obj.get("amount_total")
    if amount_total is not None:
        expected = stripe_svc.amount_to_minor(Decimal(sale.total), sale.currency)
        if int(amount_total) != expected:
            return f"montant encaissé {amount_total} ≠ montant attendu {expected}"
    return None


async def _find_sale_from_session(db: AsyncSession, obj) -> OnboardSale | None:
    """Retrouve la vente depuis l'objet session (metadata.sale_id, repli id)."""
    meta = obj.get("metadata") or {}
    sale_id = meta.get("sale_id")
    if sale_id:
        try:
            sale = await db.get(OnboardSale, int(sale_id))
            if sale is not None:
                return sale
        except (ValueError, TypeError):
            pass
    session_id = obj.get("id")
    if session_id:
        return (
            await db.execute(
                select(OnboardSale).where(OnboardSale.stripe_checkout_session_id == session_id)
            )
        ).scalar_one_or_none()
    return None


async def _settle_from_session(db: AsyncSession, obj) -> None:
    sale = await _find_sale_from_session(db, obj)
    if sale is None:
        logger.warning("Webhook Stripe : vente introuvable (session=%s)", obj.get("id"))
        return
    # Ne régler que si le paiement est effectif ('paid'). Les moyens asynchrones
    # peuvent émettre 'completed' encore 'unpaid' → l'event async_payment_succeeded
    # arrivera ensuite avec payment_status='paid'.
    if obj.get("payment_status") not in ("paid", "no_payment_required"):
        return
    mismatch = _session_matches_sale(obj, sale)
    if mismatch:
        # Un écart ici est un incident, pas un cas métier : on n'écrit rien et
        # on le fait remonter, plutôt que de créditer une caisse sur la foi
        # d'un événement qui ne concerne pas cette vente.
        detail = f"{sale.reference} — événement Stripe refusé : {mismatch}"
        logger.error("Webhook Stripe : %s", detail)
        await activity_record(
            db,
            action="onboard_sale_webhook_mismatch",
            user_name="stripe-webhook",
            module="captain",
            entity_type="onboard_sale",
            entity_id=sale.id,
            detail=detail,
        )
        await notifications.notify_onboard_payment_incident(
            db, sale_reference=sale.reference, sale_id=sale.id, detail=detail
        )
        return
    payment_intent = obj.get("payment_intent")
    if isinstance(payment_intent, dict):
        payment_intent = payment_intent.get("id")
    try:
        settled = await svc.settle_sale(
            db, sale, payment_method="card", payment_intent_id=payment_intent
        )
    except PeriodClosed as e:
        # Transitoire par nature : le siège rouvrira la période, ou l'écriture
        # sera datée du jour de réception. Ne pas acquitter, faire rejouer.
        raise _TransientSettlementError(f"{sale.reference} : {e}") from e
    except (svc.OnboardSalesError, CashboxError) as e:
        # Définitif (vente annulée, montant nul) : acquitter, mais laisser une
        # trace exploitable — le client a payé, l'application ne l'encaisse pas.
        logger.error("Webhook Stripe : règlement échoué %s : %s", sale.reference, e)
        await _flag_duplicate_payment(db, sale, payment_intent, source="webhook")
        return
    if not settled:
        await _flag_duplicate_payment(db, sale, payment_intent, source="webhook")
    if settled:
        await activity_record(
            db,
            action="onboard_sale_paid_card",
            user_name="stripe-webhook",
            module="captain",
            entity_type="onboard_sale",
            entity_id=sale.id,
            detail=f"{sale.reference} {sale.total} {sale.currency}",
        )


async def _revert_from_session(db: AsyncSession, obj) -> None:
    sale = await _find_sale_from_session(db, obj)
    if sale is not None:
        await svc.revert_to_draft(db, sale)
