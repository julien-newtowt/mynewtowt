"""Contrôle de caisse — déclaration d'un état complet et historisation des écarts.

Le commandant sortant déclare sa caisse coupure par coupure (cf.
``models/cash_count``). Ce service produit l'état, calcule le solde théorique à
la date du comptage et fige l'écart.

Deux principes qui structurent le code :

* **le total compté n'est jamais repris du formulaire** — il est recalculé
  depuis les quantités saisies. Un état de caisse dont le total serait
  déclaratif ne contrôlerait rien ;
* **l'écart est figé à la déclaration**, avec le solde théorique du moment. Un
  mouvement saisi ou corrigé après coup ne réécrit pas un contrôle déjà rendu :
  il apparaîtra dans le contrôle suivant.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cash_count import (
    CashCount,
    CashCountCurrency,
    CashCountLine,
)
from app.models.onboard_cashbox import SUPPORTED_CURRENCIES, CashboxMovement, OnboardCashbox
from app.utils.decimals import CENTS, DecimalInputError, ensure_finite

# ── Référentiel des coupures ────────────────────────────────────────────────
#
# Reprend le document type « Master's Cash Box » utilisé à bord : billets du
# plus fort au plus faible, puis pièces. Le 500 € n'y figure pas (retiré de la
# circulation depuis 2019) ; l'ajouter reviendrait à proposer une ligne qu'un
# commandant ne peut pas remplir.
_EUR = (
    (Decimal("200"), "billet"),
    (Decimal("100"), "billet"),
    (Decimal("50"), "billet"),
    (Decimal("20"), "billet"),
    (Decimal("10"), "billet"),
    (Decimal("5"), "billet"),
    (Decimal("2"), "piece"),
    (Decimal("1"), "piece"),
    (Decimal("0.50"), "piece"),
    (Decimal("0.20"), "piece"),
    (Decimal("0.10"), "piece"),
    (Decimal("0.05"), "piece"),
    (Decimal("0.02"), "piece"),
    (Decimal("0.01"), "piece"),
)

_USD = (
    (Decimal("100"), "billet"),
    (Decimal("50"), "billet"),
    (Decimal("20"), "billet"),
    (Decimal("10"), "billet"),
    (Decimal("5"), "billet"),
    (Decimal("1"), "billet"),
    (Decimal("0.25"), "piece"),
    (Decimal("0.10"), "piece"),
    (Decimal("0.05"), "piece"),
    (Decimal("0.01"), "piece"),
)

# Le đồng n'a plus de pièces en circulation courante : uniquement des billets.
_VND = (
    (Decimal("500000"), "billet"),
    (Decimal("200000"), "billet"),
    (Decimal("100000"), "billet"),
    (Decimal("50000"), "billet"),
    (Decimal("20000"), "billet"),
    (Decimal("10000"), "billet"),
    (Decimal("5000"), "billet"),
    (Decimal("2000"), "billet"),
    (Decimal("1000"), "billet"),
)

DENOMINATIONS: dict[str, tuple[tuple[Decimal, str], ...]] = {
    "EUR": _EUR,
    "USD": _USD,
    "VND": _VND,
}


class CashCountError(Exception):
    """Erreur métier de contrôle de caisse (message affichable)."""


def denominations_for(currency: str) -> tuple[tuple[Decimal, str], ...]:
    cur = currency.upper()
    if cur not in DENOMINATIONS:
        raise CashCountError(f"Devise non supportée : {currency}")
    return DENOMINATIONS[cur]


def _money(value: Decimal) -> Decimal:
    try:
        return ensure_finite(Decimal(value), label="montant").quantize(CENTS)
    except DecimalInputError as e:
        raise CashCountError(str(e)) from None


async def computed_balance(
    db: AsyncSession, cashbox: OnboardCashbox, currency: str, *, upto: date | None = None
) -> Decimal:
    """Solde théorique d'une devise, éventuellement arrêté à une date incluse.

    Somme simple des mouvements signés. Volontairement distinct de
    ``cashbox.balances`` : celui-ci ventile entrées et sorties via
    ``greatest``/``least``, absents de SQLite — ce qui le rend intestable et
    explique sa couverture nulle. Un contrôle de caisse doit, lui, être testable.
    """
    stmt = select(func.coalesce(func.sum(CashboxMovement.amount), 0)).where(
        CashboxMovement.cashbox_id == cashbox.id,
        CashboxMovement.currency == currency.upper(),
    )
    if upto is not None:
        end = datetime(upto.year, upto.month, upto.day, 23, 59, 59, tzinfo=UTC)
        stmt = stmt.where(CashboxMovement.occurred_at <= end)
    return _money(Decimal(await db.scalar(stmt) or 0))


def _block_total(
    quantities: dict[Decimal, int], denominations: tuple[tuple[Decimal, str], ...], bulk: Decimal
) -> Decimal:
    total = Decimal("0")
    for value, _kind in denominations:
        total += value * Decimal(quantities.get(value, 0))
    return _money(total + bulk)


async def declare_count(
    db: AsyncSession,
    cashbox: OnboardCashbox,
    *,
    trigger: str,
    counted_on: date,
    declared_by_name: str,
    counts: dict[str, dict[Decimal, int]],
    bulk_coins: dict[str, Decimal] | None = None,
    variance_reasons: dict[str, str] | None = None,
    declared_by_id: int | None = None,
    handover_to_name: str | None = None,
    leg_id: int | None = None,
    notes: str | None = None,
) -> CashCount:
    """Enregistre un état de caisse déclaré et fige les écarts par devise.

    ``counts`` mappe une devise vers ``{valeur_faciale: nombre}``. Seules les
    devises présentes dans ``counts`` produisent un bloc : un commandant qui ne
    détient pas de dollars ne déclare pas un bloc USD à zéro, ce qui ferait
    apparaître un faux écart si la caisse en contient réellement.
    """
    if trigger not in ("fin_embarquement", "fin_de_mois", "controle"):
        raise CashCountError(f"Motif de contrôle inconnu : {trigger}")
    if not declared_by_name.strip():
        raise CashCountError("Le nom du déclarant est requis.")
    if not counts:
        raise CashCountError("Déclarez au moins une devise.")

    bulk_coins = bulk_coins or {}
    variance_reasons = variance_reasons or {}

    # Toutes les devises sont validées **avant** la moindre écriture : une
    # déclaration refusée ne doit laisser aucun état partiel derrière elle.
    for currency, quantities in counts.items():
        cur = currency.upper()
        if cur not in SUPPORTED_CURRENCIES:
            raise CashCountError(f"Devise non supportée : {currency}")
        known = {value for value, _ in denominations_for(cur)}
        for value, qty in quantities.items():
            if value not in known:
                raise CashCountError(f"Coupure inconnue pour {cur} : {value}")
            if qty < 0:
                raise CashCountError(f"Nombre de coupures négatif ({cur} {value}).")
        if _money(Decimal(bulk_coins.get(cur, 0))) < 0:
            raise CashCountError(f"Montant de pièces en vrac négatif ({cur}).")

    # Le graphe complet est construit **en mémoire** avant la première écriture.
    # Rattacher un bloc à un `CashCount` déjà persisté déclencherait le
    # chargement de la collection — donc une requête en contexte async, hors
    # greenlet SQLAlchemy. Les soldes théoriques sont donc lus d'abord, tant
    # qu'aucun objet de l'état n'est dans la session.
    blocks: list[CashCountCurrency] = []
    for currency, quantities in counts.items():
        cur = currency.upper()
        denominations = denominations_for(cur)
        bulk = _money(Decimal(bulk_coins.get(cur, 0)))
        counted_total = _block_total(quantities, denominations, bulk)
        theoretical = await computed_balance(db, cashbox, cur, upto=counted_on)

        block = CashCountCurrency(
            currency=cur,
            bulk_coins_amount=bulk,
            counted_total=counted_total,
            computed_balance=theoretical,
            variance=_money(counted_total - theoretical),
            variance_reason=(variance_reasons.get(cur) or None),
            lines=[
                CashCountLine(
                    denomination=value,
                    kind=kind,
                    quantity=int(quantities.get(value, 0)),
                    line_total=_money(value * int(quantities.get(value, 0))),
                )
                for value, kind in denominations
                # Les coupures absentes ne sont pas stockées : l'état reste
                # lisible, et un zéro est sans ambiguïté un zéro.
                if int(quantities.get(value, 0)) > 0
            ],
        )
        blocks.append(block)

    count = CashCount(
        cashbox_id=cashbox.id,
        trigger=trigger,
        counted_on=counted_on,
        declared_by_id=declared_by_id,
        declared_by_name=declared_by_name.strip()[:200],
        handover_to_name=(handover_to_name or "").strip()[:200] or None,
        leg_id=leg_id,
        status="declare",
        notes=(notes or None),
        currencies=blocks,
    )
    db.add(count)
    await db.flush()
    return count


async def review_count(
    db: AsyncSession,
    count: CashCount,
    *,
    status: str,
    reviewed_by_id: int | None = None,
    comment: str | None = None,
) -> CashCount:
    """Suite donnée par le siège : validation ou contestation de l'écart."""
    if status not in ("valide", "conteste"):
        raise CashCountError("Suite invalide : « valide » ou « conteste ».")
    if count.status != "declare":
        raise CashCountError("Cet état a déjà reçu une suite.")
    count.status = status
    count.reviewed_by_id = reviewed_by_id
    count.review_comment = comment or None
    count.reviewed_at = datetime.now(UTC)
    await db.flush()
    return count


async def history(db: AsyncSession, cashbox: OnboardCashbox, *, limit: int = 24) -> list[CashCount]:
    """Historique des contrôles, du plus récent au plus ancien."""
    stmt = (
        select(CashCount)
        .where(CashCount.cashbox_id == cashbox.id)
        .order_by(CashCount.counted_on.desc(), CashCount.id.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().unique().all())


async def last_count(db: AsyncSession, cashbox: OnboardCashbox) -> CashCount | None:
    rows = await history(db, cashbox, limit=1)
    return rows[0] if rows else None
