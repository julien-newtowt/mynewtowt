"""Service « Vente à bord » — catalogue, stock, ventes & registre douanier.

Logique métier réutilisable (le routeur reste fin). Points clés :

- **Règlement idempotent** : ``settle_sale`` est le seul chemin qui encaisse.
  Il pose ``sale.cashbox_movement_id`` (verrou) → un rejeu (webhook Stripe
  redélivré) est un no-op. Il crée un unique ``CashboxMovement`` catégorie
  ``vente_a_bord`` (montant positif, devise de la vente) et écrit les sorties
  de stock (registre douanier).
- **Stock signé** : ``SUM(qty)`` live par (navire, produit). On n'empêche
  jamais un règlement pour cause de stock insuffisant (le paiement a eu lieu) ;
  un stock négatif est un signal d'écart d'inventaire, surfacé à l'écran.
- **Régime** : toutes les ventes sont en franchise (avitaillement).
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.onboard_sales import (
    REGIME_FRANCHISE,
    SUPPORTED_CURRENCIES,
    OnboardProduct,
    OnboardSale,
    OnboardSaleLine,
    OnboardStockMovement,
)
from app.services import cashbox as cashbox_svc
from app.utils.decimals import DecimalInputError, ensure_finite

_CENTS = Decimal("0.01")
_QTY_Q = Decimal("0.001")


class OnboardSalesError(Exception):
    """Erreur métier « Vente à bord » (message affichable à l'utilisateur)."""


def _guard(value: Decimal, label: str) -> Decimal:
    """Refuse ``NaN``/``Infinity`` avant toute écriture, quel que soit l'appelant.

    Les routeurs valident déjà la saisie (``utils.decimals``) ; ce garde-fou
    couvre les autres chemins (import, script, appel interne). Une valeur non
    finie écrite ici contaminerait définitivement un ``SUM()`` — solde de caisse
    ou stock — dans des tables append-only sans route de suppression.
    """
    try:
        return ensure_finite(value, label=label)
    except DecimalInputError as e:
        raise OnboardSalesError(str(e)) from None


def _money(value: Decimal) -> Decimal:
    return _guard(value, "montant").quantize(_CENTS, rounding=ROUND_HALF_UP)


def _qty(value: Decimal) -> Decimal:
    return _guard(value, "quantité").quantize(_QTY_Q, rounding=ROUND_HALF_UP)


# ── Références ────────────────────────────────────────────────────────────────


async def next_reference(db: AsyncSession, year: int) -> str:
    """Prochaine référence ``VB-YYYY-NNNN`` (séquence annuelle)."""
    prefix = f"VB-{year}-"
    last = await db.scalar(
        select(func.max(OnboardSale.reference)).where(OnboardSale.reference.like(f"{prefix}%"))
    )
    n = 0
    if last:
        try:
            n = int(last.rsplit("-", 1)[1])
        except (ValueError, IndexError):
            n = 0
    return f"{prefix}{n + 1:04d}"


# ── Stock ─────────────────────────────────────────────────────────────────────


async def stock_on_hand(db: AsyncSession, vessel_id: int, product_id: int) -> Decimal:
    """Solde de stock live d'un produit sur un navire (``SUM(qty)``)."""
    total = await db.scalar(
        select(func.coalesce(func.sum(OnboardStockMovement.qty), 0)).where(
            OnboardStockMovement.vessel_id == vessel_id,
            OnboardStockMovement.product_id == product_id,
        )
    )
    return Decimal(total or 0)


async def stock_map(db: AsyncSession, vessel_id: int) -> dict[int, Decimal]:
    """Solde de stock par product_id pour un navire (une requête)."""
    rows = (
        await db.execute(
            select(
                OnboardStockMovement.product_id,
                func.coalesce(func.sum(OnboardStockMovement.qty), 0),
            )
            .where(OnboardStockMovement.vessel_id == vessel_id)
            .group_by(OnboardStockMovement.product_id)
        )
    ).all()
    return {pid: Decimal(qty or 0) for pid, qty in rows}


async def add_stock_entry(
    db: AsyncSession,
    *,
    vessel_id: int,
    product: OnboardProduct,
    qty: Decimal,
    reason: str,
    note: str | None = None,
    occurred_at: datetime | None = None,
    recorded_by_id: int | None = None,
) -> OnboardStockMovement:
    """Enregistre un mouvement de stock (entrée avitaillement / ajustement…).

    ``qty`` est signée telle quelle (positif = entrée, négatif = sortie). Refuse
    zéro. Ne modifie jamais un mouvement existant (registre append-only).
    """
    q = _qty(Decimal(qty))
    if q == 0:
        raise OnboardSalesError("La quantité ne peut pas être nulle.")
    mov = OnboardStockMovement(
        vessel_id=vessel_id,
        product_id=product.id,
        qty=q,
        reason=reason,
        note=(note or None),
        occurred_at=occurred_at or datetime.now(UTC),
        recorded_by_id=recorded_by_id,
    )
    db.add(mov)
    await db.flush()
    return mov


# ── Ventes ──────────────────────────────────────────────────────────────────


async def create_sale(
    db: AsyncSession,
    *,
    vessel_id: int,
    currency: str = "EUR",
    leg_id: int | None = None,
    buyer_name: str | None = None,
    recorded_by_id: int | None = None,
) -> OnboardSale:
    """Crée une vente en brouillon (lignes ajoutées ensuite)."""
    cur = currency.upper()
    if cur not in SUPPORTED_CURRENCIES:
        raise OnboardSalesError(f"Devise non supportée : {currency}")
    now_year = datetime.now(UTC).year
    sale = OnboardSale(
        reference=await next_reference(db, now_year),
        vessel_id=vessel_id,
        leg_id=leg_id,
        buyer_name=(buyer_name or None),
        status="draft",
        currency=cur,
        total=Decimal("0"),
        regime=REGIME_FRANCHISE,
        recorded_by_id=recorded_by_id,
    )
    db.add(sale)
    await db.flush()
    return sale


async def add_line(
    db: AsyncSession,
    sale: OnboardSale,
    *,
    product: OnboardProduct,
    qty: Decimal,
) -> OnboardSaleLine:
    """Ajoute une ligne à une vente en brouillon (prix serveur, snapshot).

    Le prix unitaire et le libellé sont figés depuis le produit (jamais repris
    du client). La devise du produit doit correspondre à celle de la vente.
    """
    if sale.status != "draft":
        raise OnboardSalesError("La vente n'est plus modifiable.")
    q = _qty(Decimal(qty))
    if q <= 0:
        raise OnboardSalesError("La quantité doit être positive.")
    if product.currency.upper() != sale.currency.upper():
        raise OnboardSalesError(
            f"Le produit est en {product.currency}, la vente en {sale.currency}."
        )
    unit_price = _money(Decimal(product.unit_price))
    # Fusionne si le produit est déjà présent (contrainte unique sale×product) :
    # on cumule la quantité plutôt que de lever une IntegrityError.
    existing = (
        await db.execute(
            select(OnboardSaleLine).where(
                OnboardSaleLine.sale_id == sale.id,
                OnboardSaleLine.product_id == product.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.qty = _qty(Decimal(existing.qty) + q)
        existing.unit_price = unit_price
        existing.line_total = _money(unit_price * existing.qty)
        line = existing
    else:
        line = OnboardSaleLine(
            sale_id=sale.id,
            product_id=product.id,
            label=product.label,
            unit_price=unit_price,
            qty=q,
            line_total=_money(unit_price * q),
        )
        db.add(line)
    await db.flush()
    await recompute_total(db, sale)
    return line


async def recompute_total(db: AsyncSession, sale: OnboardSale) -> Decimal:
    """Recalcule ``sale.total`` = somme des lignes (source de vérité serveur)."""
    total = await db.scalar(
        select(func.coalesce(func.sum(OnboardSaleLine.line_total), 0)).where(
            OnboardSaleLine.sale_id == sale.id
        )
    )
    sale.total = _money(Decimal(total or 0))
    await db.flush()
    return sale.total


async def settle_sale(
    db: AsyncSession,
    sale: OnboardSale,
    *,
    payment_method: str,
    recorded_by_id: int | None = None,
    payment_intent_id: str | None = None,
) -> bool:
    """Encaisse une vente — **idempotent**. Renvoie True si réglée maintenant.

    Chemin unique de règlement (espèces confirmées **ou** webhook Stripe reçu) :
    1. garde d'idempotence sur ``cashbox_movement_id`` → rejeu = no-op ;
    2. crée UN ``CashboxMovement`` (``vente_a_bord``, +total, devise de la vente) ;
    3. passe la vente à ``paid`` + horodate ;
    4. écrit les sorties de stock (registre) pour les produits suivis.

    Ne bloque jamais sur un stock insuffisant (le paiement a eu lieu).
    """
    # 0. Sérialisation — le verrou d'idempotence doit être lu sous verrou.
    # La garde ci-dessous lisait un attribut d'un objet déjà chargé en session :
    # en READ COMMITTED, deux transactions concurrentes (webhook redélivré +
    # réconciliation à l'affichage) le voyaient toutes deux à NULL, créaient
    # chacune un mouvement de caisse et un jeu de sorties de stock, et la
    # seconde écrasait la référence de la première — mouvement orphelin,
    # indétraçable, dans un registre sans route de suppression.
    # Même patron que `packing_list.py` pour la séquence de numéros de BL.
    locked = await db.get(OnboardSale, sale.id, with_for_update=True)
    if locked is not None:
        sale = locked

    # 1. Idempotence — déjà réglée : ne rien refaire.
    if sale.cashbox_movement_id is not None:
        # Compléter la référence de paiement n'a de sens que si la vente a bien
        # été réglée **par carte** : la rattacher à une vente encaissée en
        # espèces la faisait passer pour une vente carte et masquait le seul
        # signal disponible d'un double débit du client (l'appelant compare
        # justement `stripe_payment_intent_id` pour détecter l'incident).
        if (
            payment_intent_id
            and sale.payment_method == "card"
            and not sale.stripe_payment_intent_id
        ):
            sale.stripe_payment_intent_id = payment_intent_id
            await db.flush()
        return False
    if sale.status in ("cancelled", "refunded"):
        raise OnboardSalesError("Vente annulée/remboursée : règlement impossible.")
    if sale.total <= 0:
        raise OnboardSalesError("Vente sans montant : ajoutez au moins une ligne.")

    # 2. Mouvement de caisse (encaissement). Peut lever PeriodClosed/CashboxError.
    cashbox = await cashbox_svc.get_or_create(db, sale.vessel_id)
    buyer = f" — {sale.buyer_name}" if sale.buyer_name else ""
    mov = await cashbox_svc.add_movement(
        db,
        cashbox,
        amount=_money(Decimal(sale.total)),
        currency=sale.currency,
        category="vente_a_bord",
        # Le support suit le moyen de paiement : une vente CB est encaissée
        # chez Stripe puis en banque, elle n'entre jamais dans le coffre. La
        # confondre avec l'espèce faussait la variance de clôture (ADR-011).
        medium="card" if payment_method == "card" else "cash",
        # L'argent a été encaissé : si la caisse est figée par une relève, on
        # reporte l'écriture au premier jour ouvert plutôt que de la refuser.
        # Perdre le règlement d'un paiement reçu serait pire que de le dater
        # d'un jour trop tard — et le report est visible dans le libellé.
        defer_if_frozen=True,
        description=f"Vente à bord {sale.reference}{buyer}",
        leg_id=sale.leg_id,
        recorded_by_id=recorded_by_id,
    )

    # 3. Marquage de la vente.
    sale.cashbox_movement_id = mov.id
    sale.status = "paid"
    sale.payment_method = payment_method
    sale.paid_at = datetime.now(UTC)
    if payment_intent_id:
        sale.stripe_payment_intent_id = payment_intent_id
    await db.flush()

    # 4. Sorties de stock (registre) pour les produits suivis en stock.
    lines = (
        (await db.execute(select(OnboardSaleLine).where(OnboardSaleLine.sale_id == sale.id)))
        .scalars()
        .all()
    )
    for line in lines:
        if line.product_id is None:
            continue
        product = await db.get(OnboardProduct, line.product_id)
        if product is None or not product.tracks_stock:
            continue
        db.add(
            OnboardStockMovement(
                vessel_id=sale.vessel_id,
                product_id=product.id,
                qty=-_qty(Decimal(line.qty)),
                reason="vente",
                sale_id=sale.id,
                note=f"Vente {sale.reference}",
                occurred_at=sale.paid_at,
                recorded_by_id=recorded_by_id,
            )
        )
    await db.flush()
    return True


async def cancel_sale(db: AsyncSession, sale: OnboardSale) -> None:
    """Annule une vente non réglée (brouillon ou lien Stripe en attente)."""
    if sale.is_settled or sale.status == "paid":
        raise OnboardSalesError("Vente déjà réglée : utilisez le remboursement.")
    sale.status = "cancelled"
    sale.cancelled_at = datetime.now(UTC)
    await db.flush()


async def revert_to_draft(db: AsyncSession, sale: OnboardSale) -> None:
    """Repasse en brouillon une vente **en attente de paiement**.

    Déclenché par l'événement ``checkout.session.expired``. La garde porte sur
    le statut exact : elle ne se contentait auparavant d'écarter que les ventes
    réglées, si bien qu'une vente **annulée** ressuscitait en brouillon à
    l'expiration de son lien — redevenant modifiable et encaissable, avec un
    ``cancelled_at`` incohérent. Une annulation est une décision, pas un état
    transitoire.
    """
    if sale.status != "pending_payment":
        return
    sale.status = "draft"
    sale.stripe_checkout_session_id = None
    await db.flush()


# ── Registre douanier & inventaire ──────────────────────────────────────────


async def current_inventory(db: AsyncSession, vessel_id: int) -> list[dict]:
    """Inventaire courant : produits suivis + solde de stock sur le navire."""
    products = (
        (
            await db.execute(
                select(OnboardProduct)
                .where(OnboardProduct.tracks_stock.is_(True))
                .order_by(OnboardProduct.label)
            )
        )
        .scalars()
        .all()
    )
    smap = await stock_map(db, vessel_id)
    return [{"product": p, "on_hand": smap.get(p.id, Decimal("0"))} for p in products]


async def register_rows(
    db: AsyncSession,
    vessel_id: int,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[dict]:
    """Lignes du registre douanier : mouvements de stock chronologiques.

    Chaque ligne = un mouvement (entrée avitaillement ou sortie vente). Le
    registre est append-only ; les corrections sont des mouvements
    supplémentaires (``ajustement`` / ``inventaire``).
    """
    stmt = (
        select(OnboardStockMovement, OnboardProduct, OnboardSale)
        .join(OnboardProduct, OnboardStockMovement.product_id == OnboardProduct.id)
        .join(OnboardSale, OnboardStockMovement.sale_id == OnboardSale.id, isouter=True)
        .where(OnboardStockMovement.vessel_id == vessel_id)
        .order_by(OnboardStockMovement.occurred_at, OnboardStockMovement.id)
    )
    if date_from is not None:
        stmt = stmt.where(OnboardStockMovement.occurred_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(OnboardStockMovement.occurred_at <= date_to)

    rows: list[dict] = []
    for mov, product, sale in (await db.execute(stmt)).all():
        qty = Decimal(mov.qty)
        rows.append(
            {
                "occurred_at": mov.occurred_at,
                "sku": product.sku,
                "label": product.label,
                "unit": product.unit,
                "reason": mov.reason,
                "qty_in": qty if qty > 0 else Decimal("0"),
                "qty_out": -qty if qty < 0 else Decimal("0"),
                "sale_reference": sale.reference if sale else "",
                "regime": REGIME_FRANCHISE,
                "note": mov.note or "",
            }
        )
    return rows


def export_csv(rows: list[dict], *, vessel_code: str) -> str:
    """Registre → CSV (séparateur ``;``, entêtes FR)."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "Date",
            "Navire",
            "SKU",
            "Désignation",
            "Unité",
            "Mouvement",
            "Entrée",
            "Sortie",
            "Vente réf.",
            "Régime",
            "Note",
        ]
    )
    for r in rows:
        occ = r["occurred_at"]
        writer.writerow(
            [
                occ.strftime("%Y-%m-%d %H:%M") if occ else "",
                vessel_code,
                r["sku"],
                r["label"],
                r["unit"],
                r["reason"],
                f"{r['qty_in']:.3f}" if r["qty_in"] else "",
                f"{r['qty_out']:.3f}" if r["qty_out"] else "",
                r["sale_reference"],
                r["regime"],
                r["note"],
            ]
        )
    return buf.getvalue()
