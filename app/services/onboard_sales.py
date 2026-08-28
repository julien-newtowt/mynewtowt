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

from app.models.onboard_cashbox import CashboxMovement
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


def _apply_discount(unit_price: Decimal, qty: Decimal, discount_pct: Decimal) -> Decimal:
    """Total de ligne après remise. Toujours dérivé, jamais saisi."""
    pct = _guard(Decimal(discount_pct), "remise")
    if pct < 0 or pct > 100:
        raise OnboardSalesError("La remise doit être comprise entre 0 et 100 %.")
    return _money(unit_price * qty * (Decimal("100") - pct) / Decimal("100"))


async def add_free_line(
    db: AsyncSession,
    sale: OnboardSale,
    *,
    label: str,
    unit_price: Decimal,
    qty: Decimal,
) -> OnboardSaleLine:
    """Ligne hors catalogue — article absent du référentiel, geste commercial.

    Le modèle prévoyait ``product_id`` nullable depuis l'origine, mais aucune
    route ne créait de ligne libre : vendre un article non catalogué imposait de
    créer un faux produit, qui polluait ensuite le catalogue et l'inventaire
    (audit du 2026-08-27).

    Sans produit, il n'y a **pas de mouvement de stock** au règlement : c'est
    cohérent, l'article n'est pas suivi.
    """
    if sale.status != "draft":
        raise OnboardSalesError("La vente n'est plus modifiable.")
    if not label.strip():
        raise OnboardSalesError("Désignation requise pour une ligne hors catalogue.")
    q = _qty(Decimal(qty))
    if q <= 0:
        raise OnboardSalesError("La quantité doit être positive.")
    price = _money(Decimal(unit_price))
    if price < 0:
        raise OnboardSalesError("Le prix ne peut pas être négatif.")
    line = OnboardSaleLine(
        sale_id=sale.id,
        product_id=None,
        label=label.strip()[:200],
        unit_price=price,
        qty=q,
        discount_pct=Decimal("0"),
        line_total=_money(price * q),
    )
    db.add(line)
    await db.flush()
    await recompute_total(db, sale)
    return line


async def add_line(
    db: AsyncSession,
    sale: OnboardSale,
    *,
    product: OnboardProduct,
    qty: Decimal,
    discount_pct: Decimal = Decimal("0"),
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
        existing.discount_pct = Decimal(discount_pct)
        existing.line_total = _apply_discount(unit_price, existing.qty, discount_pct)
        line = existing
    else:
        line = OnboardSaleLine(
            sale_id=sale.id,
            product_id=product.id,
            label=product.label,
            unit_price=unit_price,
            qty=q,
            discount_pct=Decimal(discount_pct),
            line_total=_apply_discount(unit_price, q, discount_pct),
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
    cash_received: Decimal | None = None,
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
    if cash_received is not None and payment_method == "cash":
        # Purement informatif : la caisse est créditée du **total de la vente**,
        # jamais de ce montant. Sans cette trace, un écart de rendu de monnaie
        # restait inexplicable au comptage.
        received = _money(Decimal(cash_received))
        if received < Decimal(sale.total):
            raise OnboardSalesError("Espèces reçues inférieures au montant de la vente.")
        sale.cash_received = received
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


async def create_cash_sale(
    db: AsyncSession,
    *,
    vessel_id: int,
    items: list[tuple[int, Decimal]],
    client_uuid: str,
    currency: str = "EUR",
    buyer_name: str | None = None,
    leg_id: int | None = None,
    recorded_by_id: int | None = None,
) -> OnboardSale:
    """Crée **et encaisse** une vente espèces en une seule opération. Idempotent.

    C'est ce qui rend la vente rapide rejouable hors connexion. Le parcours
    écran par écran enchaîne trois requêtes dépendantes — créer la vente,
    ajouter chaque ligne, encaisser — dont la deuxième a besoin de la référence
    renvoyée par la première : impossible à mettre en file d'attente. Une
    opération atomique, elle, se rejoue telle quelle.

    ``client_uuid`` est généré par le navigateur et porte l'idempotence : un
    rejeu de la file renvoie la vente déjà enregistrée au lieu d'en créer une
    seconde. La contrainte d'unicité en base est le filet de dernier recours.

    ``items`` : couples ``(product_id, quantité)``. Les prix ne viennent jamais
    du client — ils sont lus sur le catalogue, comme dans ``add_line``.
    """
    uuid = (client_uuid or "").strip()
    if not uuid:
        raise OnboardSalesError("Identifiant de vente manquant.")
    existing = (
        await db.execute(select(OnboardSale).where(OnboardSale.client_uuid == uuid))
    ).scalar_one_or_none()
    if existing is not None:
        return existing  # rejeu de la file : rien à refaire
    if not items:
        raise OnboardSalesError("Vente sans article : ajoutez au moins une ligne.")

    sale = await create_sale(
        db,
        vessel_id=vessel_id,
        currency=currency,
        leg_id=leg_id,
        buyer_name=buyer_name,
        recorded_by_id=recorded_by_id,
    )
    sale.client_uuid = uuid
    await db.flush()

    for product_id, qty in items:
        product = await db.get(OnboardProduct, product_id)
        if product is None or not product.is_active:
            raise OnboardSalesError(f"Article indisponible (id={product_id}).")
        await add_line(db, sale, product=product, qty=qty)
    await recompute_total(db, sale)
    await settle_sale(db, sale, payment_method="cash", recorded_by_id=recorded_by_id)
    return sale


async def request_refund(db: AsyncSession, sale: OnboardSale, *, note: str | None = None) -> None:
    """Le bord signale une vente à rembourser. Il ne rembourse pas lui-même.

    Sans ce geste, la décision « seul le siège rembourse » (ADR-013) se
    contournerait par téléphone et la trace se perdrait.
    """
    if not sale.is_settled:
        raise OnboardSalesError("Vente non réglée : utilisez l'annulation.")
    if sale.is_refunded:
        raise OnboardSalesError("Vente déjà remboursée.")
    sale.refund_requested_at = datetime.now(UTC)
    sale.refund_request_note = note or None
    await db.flush()


async def refund_sale(
    db: AsyncSession,
    sale: OnboardSale,
    *,
    reason: str | None = None,
    refunded_by_id: int | None = None,
    stripe_refund_id: str | None = None,
) -> CashboxMovement:
    """Rembourse une vente réglée, **par contre-passation**. Idempotent.

    Miroir strict de ``settle_sale`` :

    1. verrou sur ``refund_cashbox_movement_id`` — un rejeu est un no-op ;
    2. un mouvement de caisse **négatif** dans la même catégorie et le même
       support que l'encaissement d'origine : c'est une contre-passation
       comptable, pas une suppression. Le total « ventes à bord » devient net
       des remboursements, ce qui est le comportement recherché ;
    3. des mouvements de stock ``retour`` pour les produits suivis ;
    4. la vente passe à ``refunded``.

    Le remboursement Stripe lui-même est déclenché par l'appelant (routeur) et
    son identifiant passé ici : on ne veut pas qu'une écriture comptable dépende
    d'un appel réseau à l'intérieur de la transaction.
    """
    locked = await db.get(OnboardSale, sale.id, with_for_update=True)
    if locked is not None:
        sale = locked
    if sale.refund_cashbox_movement_id is not None:
        return await db.get(CashboxMovement, sale.refund_cashbox_movement_id)
    if not sale.is_settled:
        raise OnboardSalesError("Vente non réglée : rien à rembourser.")
    if sale.total <= 0:
        raise OnboardSalesError("Vente sans montant.")

    buyer = f" — {sale.buyer_name}" if sale.buyer_name else ""
    mov = await cashbox_svc.add_movement(
        db,
        await cashbox_svc.get_or_create(db, sale.vessel_id),
        amount=-_money(Decimal(sale.total)),
        currency=sale.currency,
        category="vente_a_bord",
        # Le remboursement emprunte le même canal que l'encaissement : une vente
        # CB est recréditée sur la carte, pas prise dans le coffre.
        medium="card" if sale.payment_method == "card" else "cash",
        # L'argent est réellement sorti : on ne perd pas l'écriture si la caisse
        # vient d'être figée par une relève (cf. ADR-013).
        defer_if_frozen=True,
        description=f"Remboursement vente {sale.reference}{buyer}",
        leg_id=sale.leg_id,
        recorded_by_id=refunded_by_id,
    )

    sale.refund_cashbox_movement_id = mov.id
    sale.status = "refunded"
    sale.refunded_at = datetime.now(UTC)
    sale.refunded_by_id = refunded_by_id
    sale.refund_reason = reason or None
    if stripe_refund_id:
        sale.stripe_refund_id = stripe_refund_id
    await db.flush()

    # Retour en stock des produits suivis — symétrique des sorties de vente.
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
                qty=_qty(Decimal(line.qty)),
                reason="retour",
                sale_id=sale.id,
                note=f"Remboursement {sale.reference}",
                occurred_at=sale.refunded_at,
                recorded_by_id=refunded_by_id,
            )
        )
    await db.flush()
    return mov


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


# ── Reporting ────────────────────────────────────────────────────────────────


def _settled_filter():
    """Ventes effectivement encaissées — les brouillons ne sont pas du chiffre."""
    return OnboardSale.cashbox_movement_id.is_not(None)


async def sales_summary(
    db: AsyncSession,
    *,
    vessel_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    """Chiffre d'affaires de la vente à bord, ventilé.

    Le siège ne pouvait pas répondre à « combien la boutique a-t-elle vendu ce
    mois-ci ? » autrement qu'en lisant l'export CSV de caisse : aucune
    agrégation n'existait dans l'application (audit du 2026-08-27).

    Ne compte que les ventes **réglées**. Une vente remboursée est comptée à
    part et retirée du net : le chiffre d'affaires est net de remboursements,
    comme la contre-passation le fait déjà en caisse.

    Les montants restent **ventilés par devise** — il n'existe aucun taux de
    change dans l'application, et en inventer un ici produirait un total faux
    d'apparence juste.
    """
    from app.models.leg import Leg
    from app.models.vessel import Vessel

    def _scope(stmt):
        stmt = stmt.where(_settled_filter())
        if vessel_id is not None:
            stmt = stmt.where(OnboardSale.vessel_id == vessel_id)
        if date_from is not None:
            stmt = stmt.where(OnboardSale.paid_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(OnboardSale.paid_at <= date_to)
        return stmt

    # ── Totaux par devise ────────────────────────────────────────────────────
    rows = (
        await db.execute(
            _scope(
                select(
                    OnboardSale.currency,
                    OnboardSale.status,
                    func.coalesce(func.sum(OnboardSale.total), 0).label("amount"),
                    func.count(OnboardSale.id).label("cnt"),
                ).group_by(OnboardSale.currency, OnboardSale.status)
            )
        )
    ).all()
    totals: dict[str, dict] = {}
    for r in rows:
        entry = totals.setdefault(
            r.currency,
            {"gross": Decimal("0"), "refunded": Decimal("0"), "net": Decimal("0"), "count": 0},
        )
        amount = Decimal(r.amount or 0)
        if r.status == "refunded":
            entry["refunded"] += amount
        else:
            entry["gross"] += amount
            entry["count"] += int(r.cnt or 0)
    for entry in totals.values():
        entry["net"] = _money(entry["gross"])
        entry["gross"] = _money(entry["gross"])
        entry["refunded"] = _money(entry["refunded"])

    # ── Par navire ───────────────────────────────────────────────────────────
    by_vessel = [
        {
            "vessel_id": r.id,
            "code": r.code,
            "name": r.name,
            "currency": r.currency,
            "net": _money(Decimal(r.amount or 0)),
            "count": int(r.cnt or 0),
        }
        for r in (
            await db.execute(
                _scope(
                    select(
                        Vessel.id,
                        Vessel.code,
                        Vessel.name,
                        OnboardSale.currency,
                        func.coalesce(func.sum(OnboardSale.total), 0).label("amount"),
                        func.count(OnboardSale.id).label("cnt"),
                    )
                    .join(Vessel, Vessel.id == OnboardSale.vessel_id)
                    .where(OnboardSale.status == "paid")
                    .group_by(Vessel.id, Vessel.code, Vessel.name, OnboardSale.currency)
                    .order_by(Vessel.code)
                )
            )
        ).all()
    ]

    # ── Par article ──────────────────────────────────────────────────────────
    by_product = [
        {
            "label": r.label,
            "currency": r.currency,
            "qty": _qty(Decimal(r.qty or 0)),
            "net": _money(Decimal(r.amount or 0)),
        }
        for r in (
            await db.execute(
                _scope(
                    select(
                        OnboardSaleLine.label,
                        OnboardSale.currency,
                        func.coalesce(func.sum(OnboardSaleLine.qty), 0).label("qty"),
                        func.coalesce(func.sum(OnboardSaleLine.line_total), 0).label("amount"),
                    )
                    .join(OnboardSale, OnboardSale.id == OnboardSaleLine.sale_id)
                    .where(OnboardSale.status == "paid")
                    .group_by(OnboardSaleLine.label, OnboardSale.currency)
                )
            )
        ).all()
    ]
    by_product.sort(key=lambda row: row["net"], reverse=True)

    # ── Par voyage ───────────────────────────────────────────────────────────
    by_leg = [
        {
            "leg_id": r.id,
            "leg_code": r.leg_code,
            "currency": r.currency,
            "net": _money(Decimal(r.amount or 0)),
            "count": int(r.cnt or 0),
        }
        for r in (
            await db.execute(
                _scope(
                    select(
                        Leg.id,
                        Leg.leg_code,
                        OnboardSale.currency,
                        func.coalesce(func.sum(OnboardSale.total), 0).label("amount"),
                        func.count(OnboardSale.id).label("cnt"),
                    )
                    .join(Leg, Leg.id == OnboardSale.leg_id)
                    .where(OnboardSale.status == "paid")
                    .group_by(Leg.id, Leg.leg_code, OnboardSale.currency)
                    .order_by(Leg.leg_code)
                )
            )
        ).all()
    ]

    return {
        "totals": totals,
        "by_vessel": by_vessel,
        "by_product": by_product,
        "by_leg": by_leg,
    }


async def onboard_revenue_by_leg(db: AsyncSession, leg_id: int) -> dict[str, Decimal]:
    """CA de vente à bord d'un voyage, par devise.

    Exposé pour Finance/KPI. **Volontairement non injecté** dans
    ``LegFinance.revenue_eur``, qui est saisi par un opérateur : y écrire
    d'office écraserait sa saisie sans qu'il le sache. La consolidation
    automatique est une décision de gestion, pas un détail d'implémentation.
    """
    rows = (
        await db.execute(
            select(
                OnboardSale.currency,
                func.coalesce(func.sum(OnboardSale.total), 0).label("amount"),
            )
            .where(
                OnboardSale.leg_id == leg_id,
                OnboardSale.status == "paid",
                _settled_filter(),
            )
            .group_by(OnboardSale.currency)
        )
    ).all()
    return {r.currency: _money(Decimal(r.amount or 0)) for r in rows}


def export_summary_csv(summary: dict, *, period: str) -> str:
    """Export comptable du chiffre d'affaires, ventilé comme à l'écran."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([f"Vente à bord — chiffre d'affaires — période {period}"])
    w.writerow([])

    w.writerow(["Totaux par devise"])
    w.writerow(["Devise", "Encaissé", "Remboursé", "Net", "Nombre de ventes"])
    for currency, entry in sorted(summary["totals"].items()):
        w.writerow(
            [
                currency,
                f"{entry['gross']:.2f}",
                f"{entry['refunded']:.2f}",
                f"{entry['net']:.2f}",
                entry["count"],
            ]
        )

    w.writerow([])
    w.writerow(["Par navire"])
    w.writerow(["Navire", "Nom", "Devise", "Net", "Ventes"])
    for row in summary["by_vessel"]:
        w.writerow([row["code"], row["name"], row["currency"], f"{row['net']:.2f}", row["count"]])

    w.writerow([])
    w.writerow(["Par article"])
    w.writerow(["Article", "Devise", "Quantité", "Net"])
    for row in summary["by_product"]:
        w.writerow([row["label"], row["currency"], f"{row['qty']:.3f}", f"{row['net']:.2f}"])

    w.writerow([])
    w.writerow(["Par voyage"])
    w.writerow(["Leg", "Devise", "Net", "Ventes"])
    for row in summary["by_leg"]:
        w.writerow([row["leg_code"], row["currency"], f"{row['net']:.2f}", row["count"]])
    return buf.getvalue()


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
    rows = []
    for p in products:
        on_hand = smap.get(p.id, Decimal("0"))
        threshold = Decimal(p.min_stock_alert) if p.min_stock_alert is not None else None
        rows.append(
            {
                "product": p,
                "on_hand": on_hand,
                "threshold": threshold,
                # Une rupture ne se découvrait qu'au moment de vendre : le
                # commandant ne savait pas qu'il fallait réapprovisionner.
                "low": threshold is not None and on_hand <= threshold,
                "negative": on_hand < 0,
            }
        )
    return rows


async def low_stock(db: AsyncSession, vessel_id: int) -> list[dict]:
    """Articles sous leur seuil d'alerte — à réapprovisionner."""
    return [row for row in await current_inventory(db, vessel_id) if row["low"] or row["negative"]]


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
                # Lu sur la vente, pas écrit en dur : tant qu'il n'existe qu'un
                # régime l'écart est invisible, mais le jour où un second
                # apparaît le registre mentirait sans que rien n'échoue. Un
                # mouvement sans vente (avitaillement, inventaire) reste en
                # franchise, qui est le régime du navire.
                "regime": sale.regime if sale else REGIME_FRANCHISE,
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
