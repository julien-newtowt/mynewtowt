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
from decimal import Decimal, InvalidOperation

import segno
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
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
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services import onboard_sales as svc
from app.services import stripe_checkout as stripe_svc
from app.services.activity import record as activity_record
from app.services.cashbox import CashboxError, PeriodClosed
from app.templating import templates

logger = logging.getLogger("onboard_sales")

router = APIRouter(prefix="/captain/ventes", tags=["onboard-sales"])
# Webhook Stripe : monté sous /webhooks/ → exempté de CSRF (cf. app/csrf.py),
# sans auth staff, validé par signature Stripe.
webhook_router = APIRouter(prefix="/webhooks", tags=["stripe"])

# Motifs de mouvement de stock saisissables manuellement (la « vente » est
# générée automatiquement au règlement).
_MANUAL_STOCK_REASONS = ("avitaillement", "retour", "ajustement", "inventaire")


def _parse_decimal(raw: str) -> Decimal:
    try:
        return Decimal((raw or "").strip().replace(",", ".").replace(" ", ""))
    except (InvalidOperation, AttributeError):
        raise HTTPException(status_code=400, detail="Valeur numérique invalide") from None


async def _default_leg_id(db: AsyncSession, vessel_id: int) -> int | None:
    """Leg « courant » du navire (dernier leg par id) pour rattacher la vente."""
    return await db.scalar(
        select(Leg.id).where(Leg.vessel_id == vessel_id).order_by(Leg.id.desc()).limit(1)
    )


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
    sku: str = Form(...),
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
    sku = sku.strip()
    if not sku or not label.strip():
        raise HTTPException(status_code=400, detail="SKU et libellé requis")
    if currency.upper() not in SUPPORTED_CURRENCIES:
        raise HTTPException(status_code=400, detail="Devise non supportée")
    exists = await db.scalar(select(OnboardProduct.id).where(OnboardProduct.sku == sku))
    if exists:
        raise HTTPException(status_code=400, detail=f"SKU déjà utilisé : {sku}")
    product = OnboardProduct(
        sku=sku,
        label=label.strip(),
        kind=kind if kind in ("bien", "service") else "bien",
        unit_price=_parse_decimal(unit_price),
        currency=currency.upper(),
        unit=unit.strip() or "pièce",
        tracks_stock=(kind != "service") and (tracks_stock in ("on", "true", "1", "yes")),
        notes=notes.strip() or None,
    )
    db.add(product)
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
        detail=f"{sku} {product.label}",
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
    product.unit_price = _parse_decimal(unit_price)
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
            qty=_parse_decimal(qty),
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
            "stripe_enabled": stripe_svc.is_configured(),
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
        await svc.add_line(db, sale, product=product, qty=_parse_decimal(qty))
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
    if not stripe_svc.is_configured():
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
    except (stripe_svc.StripeNotConfigured, stripe_svc.StripeCheckoutError) as e:
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
    if not raw:
        return None
    try:
        d = datetime.fromisoformat(raw)
    except ValueError:
        return None
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
        return
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
    obj = event.get("data", {}).get("object", {})
    if etype in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        await _settle_from_session(db, obj)
    elif etype == "checkout.session.expired":
        await _revert_from_session(db, obj)
    # Tout autre event : accusé de réception (200) sans traitement.
    return JSONResponse({"received": True})


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
    payment_intent = obj.get("payment_intent")
    if isinstance(payment_intent, dict):
        payment_intent = payment_intent.get("id")
    try:
        settled = await svc.settle_sale(
            db, sale, payment_method="card", payment_intent_id=payment_intent
        )
    except (svc.OnboardSalesError, CashboxError) as e:
        logger.error("Webhook Stripe : règlement échoué %s : %s", sale.reference, e)
        return
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
