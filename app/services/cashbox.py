"""Onboard cashbox service: balance computation per (vessel, currency).

Pure CRUD + aggregations. Movements are signed amounts:
positive = income/recharge, negative = expense.
"""

from __future__ import annotations

import calendar
import csv
import io
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cash_count import CashCount, CashCountCurrency
from app.models.onboard_cashbox import (
    CATEGORY_LABELS,
    SUPPORTED_CURRENCIES,
    CashboxClosure,
    CashboxMovement,
    OnboardCashbox,
)
from app.utils.decimals import CENTS, DecimalInputError, ensure_finite


@dataclass(frozen=True)
class CurrencyBalance:
    currency: str
    balance: Decimal
    income_total: Decimal
    expense_total: Decimal
    movement_count: int


class CashboxError(Exception):
    pass


class AccountingFrozen(CashboxError):
    """La comptabilité est figée par un état de caisse de fin d'embarquement.

    Distinct de ``PeriodClosed`` (clôture mensuelle) : une relève arrête la
    responsabilité d'une personne, pas un mois comptable. Le message doit le
    dire, sinon le commandant entrant ne comprend pas pourquoi il est refusé.
    """


class RegularisationRefused(CashboxError):
    """Régularisation d'écart refusée (ADR-014) — message affichable."""


class PeriodClosed(CashboxError):
    """La période visée est clôturée : mouvement refusé / lecture seule."""


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """(début, fin) UTC inclusifs d'un mois calendaire."""
    start = datetime(year, month, 1, tzinfo=UTC)
    last_day = calendar.monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=UTC)
    return start, end


async def get_or_create(db: AsyncSession, vessel_id: int) -> OnboardCashbox:
    cb = (
        await db.execute(select(OnboardCashbox).where(OnboardCashbox.vessel_id == vessel_id))
    ).scalar_one_or_none()
    if cb:
        return cb
    cb = OnboardCashbox(vessel_id=vessel_id, is_active=True)
    db.add(cb)
    await db.flush()
    return cb


async def frozen_until(db: AsyncSession, cashbox: OnboardCashbox) -> date | None:
    """Date jusqu'à laquelle la comptabilité de cette caisse est figée.

    Renvoie la date du dernier état de caisse déclaré en **fin d'embarquement**
    (ADR-013). Le gel couvre **toute la caisse**, pas seulement les devises
    déclarées : une relève arrête la comptabilité du débarquant, pas une partie
    de celle-ci. Corollaire opérationnel : le commandant doit déclarer toutes
    les devises qu'il détient — c'est dit dans la notice.
    """
    return await db.scalar(
        select(func.max(CashCount.counted_on)).where(
            CashCount.cashbox_id == cashbox.id,
            CashCount.trigger == "fin_embarquement",
        )
    )


def as_movement_date(when: datetime) -> datetime:
    """Ramène une date d'effet à minuit UTC.

    Un mouvement de caisse s'impute à une **journée**, pas à un instant : le
    commandant note ce qu'il a encaissé ou dépensé dans la journée, souvent en
    différé. Conserver une heure donnait une fausse précision — celle de la
    saisie, pas celle de l'opération — et rendait deux mouvements du même jour
    incomparables selon qu'ils avaient été saisis ou déduits automatiquement.

    Les bornes de période (`month_bounds`) sont inclusives depuis 00:00:00 :
    ramener à minuit ne fait donc sortir aucun mouvement de sa période.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def add_movement(
    db: AsyncSession,
    cashbox: OnboardCashbox,
    *,
    amount: Decimal,
    currency: str,
    category: str,
    description: str,
    medium: str = "cash",
    occurred_at: datetime | None = None,
    defer_if_frozen: bool = False,
    leg_id: int | None = None,
    port_id: int | None = None,
    recorded_by_id: int | None = None,
    receipt_url: str | None = None,
    receipt_mime: str | None = None,
) -> CashboxMovement:
    if currency.upper() not in SUPPORTED_CURRENCIES:
        raise CashboxError(f"Unsupported currency: {currency}")
    if medium not in ("cash", "card"):
        raise CashboxError(f"Support de règlement inconnu : {medium}")
    # Finitude d'abord : `Decimal("nan") == 0` vaut False, donc la garde
    # « montant non nul » ci-dessous était franchie par un NaN, qui rendait
    # ensuite `SUM(amount)` — et donc le solde de la caisse — définitivement NaN.
    try:
        amount = ensure_finite(Decimal(amount), label="montant")
    except DecimalInputError as e:
        raise CashboxError(str(e)) from None
    if amount == 0:
        raise CashboxError("Amount cannot be zero")
    if not description.strip():
        raise CashboxError("Description required")
    occ = as_movement_date(occurred_at or datetime.now(UTC))

    # Gel à la relève (ADR-013). Deux natures d'écriture, deux traitements :
    # une **saisie** est refusée — la période a été remise et déchargée ; un
    # **règlement de vente** est reporté au premier jour ouvert, parce que
    # l'argent a réellement été encaissé et qu'on ne perd jamais l'écriture
    # d'un paiement reçu. Le report est visible dans le libellé ; l'écart du
    # contrôle déjà rendu, lui, n'est jamais réécrit.
    frozen = await frozen_until(db, cashbox)
    if frozen is not None and occ.date() <= frozen:
        if not defer_if_frozen:
            raise AccountingFrozen(
                f"Comptabilité figée au {frozen} par un état de caisse de fin "
                "d'embarquement : aucune écriture n'est possible à cette date."
            )
        first_open = frozen + timedelta(days=1)
        occ = as_movement_date(
            datetime(first_open.year, first_open.month, first_open.day, tzinfo=UTC)
        )
        description = f"{description} [reporté — caisse figée au {frozen}]"

    if await is_period_closed(db, cashbox, currency.upper(), occ):
        raise PeriodClosed("Période clôturée : impossible d'ajouter un mouvement à cette date.")
    mov = CashboxMovement(
        cashbox_id=cashbox.id,
        amount=amount,
        currency=currency.upper(),
        medium=medium,
        category=category,
        description=description.strip()[:300],
        leg_id=leg_id,
        port_id=port_id,
        occurred_at=occ,
        recorded_by_id=recorded_by_id,
        receipt_url=receipt_url,
        receipt_mime=receipt_mime,
    )
    db.add(mov)
    await db.flush()
    return mov


async def reverse_movement(
    db: AsyncSession,
    cashbox: OnboardCashbox,
    movement: CashboxMovement,
    *,
    reason: str,
    corrected_amount: Decimal | None = None,
    recorded_by_id: int | None = None,
) -> tuple[CashboxMovement, CashboxMovement | None]:
    """Rectifie un mouvement par **contre-écriture**. Idempotent.

    Le grand livre de caisse n'a aucune route de modification ni de suppression,
    et c'est délibéré : une écriture passée fait foi. Rectifier une saisie
    erronée se fait donc comme en comptabilité — on ajoute un mouvement opposé,
    daté du **jour de la correction**, puis éventuellement le mouvement correct.

    Conséquence à assumer : la correction n'apparaît pas dans la période
    d'origine. C'est voulu — un contrôle de caisse déjà rendu ne se réécrit pas,
    la rectification apparaît dans le suivant.

    Renvoie ``(contre-écriture, mouvement corrigé | None)``. Un second appel sur
    le même mouvement est refusé : l'unicité de ``reverses_movement_id`` le
    garantit aussi en base.
    """
    if movement.cashbox_id != cashbox.id:
        raise CashboxError("Ce mouvement n'appartient pas à cette caisse.")
    if not reason.strip():
        raise CashboxError("Motif de rectification requis.")
    already = await db.scalar(
        select(CashboxMovement.id).where(CashboxMovement.reverses_movement_id == movement.id)
    )
    if already is not None:
        raise CashboxError("Ce mouvement a déjà été rectifié.")

    label = f"Rectification du mouvement #{movement.id} — {reason.strip()}"
    reversal = await add_movement(
        db,
        cashbox,
        amount=-Decimal(movement.amount),
        currency=movement.currency,
        category=movement.category,
        medium=movement.medium or "cash",
        description=f"Annulation : {label}"[:300],
        leg_id=movement.leg_id,
        port_id=movement.port_id,
        recorded_by_id=recorded_by_id,
    )
    reversal.reverses_movement_id = movement.id
    # Le rattachement à l'écart soldé suit la contre-écriture (ADR-014). Sans
    # cela, rectifier une régularisation laissait l'écart affiché comme
    # entièrement régularisé alors que l'écriture venait d'être annulée — et
    # aucune nouvelle régularisation n'était plus possible sur ce contrôle.
    reversal.settles_cash_count_id = movement.settles_cash_count_id
    await db.flush()

    replacement = None
    if corrected_amount is not None:
        replacement = await add_movement(
            db,
            cashbox,
            amount=Decimal(corrected_amount),
            currency=movement.currency,
            category=movement.category,
            medium=movement.medium or "cash",
            description=f"Montant corrigé : {label}"[:300],
            leg_id=movement.leg_id,
            port_id=movement.port_id,
            recorded_by_id=recorded_by_id,
        )
        replacement.settles_cash_count_id = movement.settles_cash_count_id
        await db.flush()
    return reversal, replacement


async def is_period_closed(
    db: AsyncSession, cashbox: OnboardCashbox, currency: str, when: datetime
) -> bool:
    """Vrai si une clôture (cashbox, devise) couvre la date ``when``."""
    stmt = (
        select(CashboxClosure.id)
        .where(
            CashboxClosure.cashbox_id == cashbox.id,
            CashboxClosure.currency == currency.upper(),
            CashboxClosure.period_start.is_not(None),
            CashboxClosure.period_start <= when,
            CashboxClosure.period_end >= when,
        )
        .limit(1)
    )
    return (await db.scalar(stmt)) is not None


async def balances(
    db: AsyncSession, cashbox: OnboardCashbox, *, medium: str | None = "cash"
) -> list[CurrencyBalance]:
    """Soldes par devise. Par défaut **espèces seules** (ADR-011).

    La caisse de bord décrit l'argent physique détenu à bord : un règlement par
    carte n'y est jamais. Le laisser dans le solde affichait une trésorerie qui
    n'existe pas et faussait la comparaison avec le comptage. Passer
    ``medium=None`` rend l'ancien comportement (tous supports confondus), utile
    pour un total de contrôle.
    """
    stmt = (
        select(
            CashboxMovement.currency,
            func.coalesce(func.sum(CashboxMovement.amount), 0).label("balance"),
            # `case` plutôt que `greatest`/`least` : ces deux fonctions n'existent
            # pas sous SQLite, ce qui rendait `balances()` inexécutable en test —
            # d'où sa couverture nulle, relevée à l'audit du 2026-08-27, sur une
            # fonction qui calcule pourtant le solde affiché à l'écran.
            func.coalesce(
                func.sum(case((CashboxMovement.amount > 0, CashboxMovement.amount), else_=0)),
                0,
            ).label("income"),
            func.coalesce(
                func.sum(case((CashboxMovement.amount < 0, CashboxMovement.amount), else_=0)),
                0,
            ).label("expense"),
            func.count(CashboxMovement.id).label("cnt"),
        )
        .where(CashboxMovement.cashbox_id == cashbox.id)
        .group_by(CashboxMovement.currency)
        .order_by(CashboxMovement.currency)
    )
    if medium is not None:
        stmt = stmt.where(CashboxMovement.medium == medium)
    rows = (await db.execute(stmt)).all()
    return [
        CurrencyBalance(
            currency=r.currency,
            balance=Decimal(r.balance or 0),
            income_total=Decimal(r.income or 0),
            expense_total=Decimal(r.expense or 0),  # negative
            movement_count=r.cnt,
        )
        for r in rows
    ]


async def card_totals(
    db: AsyncSession, cashbox: OnboardCashbox, *, year: int | None = None, month: int | None = None
) -> dict[str, Decimal]:
    """Encaissements carte par devise — matière du rapprochement bancaire.

    Ils sont exclus du solde d'espèces (ADR-011) mais restent au journal : le
    rapprochement se fait dans le logiciel comptable, à partir de l'export
    mensuel et de l'extrait bancaire. Cette fonction sert à afficher et à
    exporter le total, pas à le rapprocher ici.
    """
    stmt = (
        select(
            CashboxMovement.currency,
            func.coalesce(func.sum(CashboxMovement.amount), 0).label("total"),
        )
        .where(
            CashboxMovement.cashbox_id == cashbox.id,
            CashboxMovement.medium == "card",
        )
        .group_by(CashboxMovement.currency)
        .order_by(CashboxMovement.currency)
    )
    if year is not None and month is not None:
        start, end = month_bounds(year, month)
        stmt = stmt.where(CashboxMovement.occurred_at >= start, CashboxMovement.occurred_at <= end)
    return {r.currency: Decimal(r.total or 0) for r in (await db.execute(stmt)).all()}


async def recent_movements(
    db: AsyncSession,
    cashbox: OnboardCashbox,
    *,
    currency: str | None = None,
    limit: int = 50,
) -> list[CashboxMovement]:
    stmt = (
        select(CashboxMovement)
        .where(CashboxMovement.cashbox_id == cashbox.id)
        .order_by(CashboxMovement.occurred_at.desc(), CashboxMovement.id.desc())
        .limit(limit)
    )
    if currency:
        stmt = stmt.where(CashboxMovement.currency == currency.upper())
    return list((await db.execute(stmt)).scalars().all())


async def period_movements(
    db: AsyncSession,
    cashbox: OnboardCashbox,
    *,
    year: int,
    month: int,
    currency: str | None = None,
) -> list[CashboxMovement]:
    start, end = month_bounds(year, month)
    stmt = (
        select(CashboxMovement)
        .where(
            CashboxMovement.cashbox_id == cashbox.id,
            CashboxMovement.occurred_at >= start,
            CashboxMovement.occurred_at <= end,
        )
        .order_by(CashboxMovement.occurred_at.asc(), CashboxMovement.id.asc())
    )
    if currency:
        stmt = stmt.where(CashboxMovement.currency == currency.upper())
    return list((await db.execute(stmt)).scalars().all())


def export_csv(movements: list[CashboxMovement], *, vessel_code: str, period: str) -> str:
    """Construit l'export comptable CSV d'une liste de mouvements (période)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([f"Caisse de bord — {vessel_code} — période {period}"])
    w.writerow(
        [
            "Date",
            "Sens",
            "Catégorie",
            "Libellé",
            "Montant",
            "Devise",
            "Support",
            "Justificatif",
            "Saisi le",
            "Verrou",
        ]
    )
    for m in movements:
        kind = "Encaissement" if m.amount > 0 else "Décaissement"
        cat = CATEGORY_LABELS.get(m.category, m.category)
        w.writerow(
            [
                m.occurred_at.strftime("%Y-%m-%d"),
                kind,
                cat,
                (m.description or "").replace("\n", " "),
                f"{m.amount:.2f}",
                m.currency,
                "Carte" if m.medium == "card" else "Espèces",
                "oui" if m.receipt_url else "non",
                m.recorded_at.strftime("%Y-%m-%d %H:%M") if m.recorded_at else "",
                "verrouillé" if m.closure_id else "ouvert",
            ]
        )

    # Totaux par devise **et par support**. C'est la matière du rapprochement
    # bancaire, qui se fait dans le logiciel comptable (ADR-011) : sans cette
    # ventilation, le comptable ne peut pas séparer ce qui est passé en caisse
    # de ce qui est arrivé sur le compte bancaire.
    totals: dict[tuple[str, str], Decimal] = {}
    for m in movements:
        key = (m.currency, m.medium or "cash")
        totals[key] = totals.get(key, Decimal("0")) + Decimal(m.amount)
    if totals:
        w.writerow([])
        w.writerow(["Totaux par devise et support"])
        w.writerow(["Devise", "Support", "Total"])
        for (cur, med), total in sorted(totals.items()):
            w.writerow([cur, "Carte" if med == "card" else "Espèces", f"{total:.2f}"])
    return buf.getvalue()


async def close_month(
    db: AsyncSession,
    cashbox: OnboardCashbox,
    *,
    year: int,
    month: int,
    counted: dict[str, Decimal],
    closed_by_id: int | None = None,
    export_path: str | None = None,
) -> list[CashboxClosure]:
    """Clôture mensuelle : fige le solde par devise et **verrouille** les
    mouvements de la période (lecture seule après export comptable).

    Idempotent au sens où une devise déjà clôturée pour ce mois est ignorée
    (la contrainte ``uq_closure_period`` la protège aussi). Renvoie les
    clôtures créées.
    """
    start, end = month_bounds(year, month)
    movs = await period_movements(db, cashbox, year=year, month=month)
    if not movs:
        raise CashboxError("Aucun mouvement sur la période — rien à clôturer.")

    # Devises déjà clôturées pour ce period_end → on ne re-clôture pas.
    already = {
        row[0]
        for row in (
            await db.execute(
                select(CashboxClosure.currency).where(
                    CashboxClosure.cashbox_id == cashbox.id,
                    CashboxClosure.period_end == end,
                )
            )
        ).all()
    }

    currencies = sorted({m.currency for m in movs} - already)
    if not currencies:
        raise CashboxError("Période déjà clôturée pour toutes les devises concernées.")

    now = datetime.now(UTC)
    created: list[CashboxClosure] = []
    for cur in currencies:
        # Solde cumulé à la fin de la période — **espèces uniquement** (ADR-011).
        # `counted_balance` est un comptage physique de billets : y comparer un
        # total incluant les règlements carte produisait une variance fausse du
        # montant des ventes CB, chaque mois, derrière laquelle une perte
        # d'espèces réelle devenait indétectable.
        computed = await db.scalar(
            select(func.coalesce(func.sum(CashboxMovement.amount), 0)).where(
                CashboxMovement.cashbox_id == cashbox.id,
                CashboxMovement.currency == cur,
                CashboxMovement.medium == "cash",
                CashboxMovement.occurred_at <= end,
            )
        )
        computed = Decimal(computed or 0)
        counted_val = counted.get(cur, computed)
        period_movs = [m for m in movs if m.currency == cur]
        closure = CashboxClosure(
            cashbox_id=cashbox.id,
            currency=cur,
            period_start=start,
            period_end=end,
            counted_balance=counted_val,
            computed_balance=computed,
            variance=(counted_val - computed),
            movement_count=len(period_movs),
            exported_at=now if export_path else None,
            closed_at=now,
            closed_by_id=closed_by_id,
        )
        db.add(closure)
        await db.flush()
        # Verrouillage des mouvements de la devise sur la période.
        for m in period_movs:
            m.closure_id = closure.id
            m.locked_at = now
        created.append(closure)
    await db.flush()
    return created


async def list_closures(
    db: AsyncSession, cashbox: OnboardCashbox, limit: int = 50
) -> list[CashboxClosure]:
    stmt = (
        select(CashboxClosure)
        .where(CashboxClosure.cashbox_id == cashbox.id)
        .order_by(CashboxClosure.period_end.desc(), CashboxClosure.currency)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


# ── Régularisation d'un écart de caisse — geste du siège (ADR-014) ──────────


async def regularised_by_count(
    db: AsyncSession, count_id: int, currency: str
) -> list[CashboxMovement]:
    """Régularisations déjà passées sur l'écart d'un contrôle, pour une devise."""
    stmt = (
        select(CashboxMovement)
        .where(
            CashboxMovement.settles_cash_count_id == count_id,
            CashboxMovement.currency == currency.upper(),
        )
        .order_by(CashboxMovement.id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def remaining_variance(db: AsyncSession, block: CashCountCurrency) -> Decimal:
    """Part de l'écart d'un bloc devise **non encore régularisée**, signée.

    L'écart d'un contrôle est figé (c'est sa raison d'être) : il ne diminue pas
    quand le siège passe une régularisation. On soustrait donc explicitement ce
    qui a déjà été passé, sans quoi une seconde régularisation du même écart
    doublerait la correction sans que rien ne s'y oppose.
    """
    already = sum(
        (m.amount for m in await regularised_by_count(db, block.cash_count_id, block.currency)),
        Decimal("0"),
    )
    return (Decimal(block.variance) - already).quantize(CENTS)


async def regularise_variance(
    db: AsyncSession,
    cashbox: OnboardCashbox,
    count: CashCount,
    block: CashCountCurrency,
    *,
    amount: Decimal,
    reason: str,
    recorded_by_id: int | None = None,
) -> CashboxMovement:
    """Solde tout ou partie de l'écart constaté par un contrôle de caisse.

    **Geste du siège, jamais du bord** (ADR-014). Le contrôle appartenant à
    l'appelant, ce service ne vérifie pas la permission — la route le fait, sous
    ``finance:M`` — mais il pose les trois garde-fous qui donnent sa valeur à
    l'écriture :

    1. **elle est adossée à un écart déclaré** : pas de régularisation flottante,
       la contrepartie est un contrôle nommé, daté et signé d'un commandant ;
    2. **elle ne peut ni dépasser cet écart ni en inverser le sens** — sans
       cette borne, la catégorie ne serait qu'un « Autre encaissement » relabellé,
       et n'apporterait aucun contrôle ;
    3. **elle est datée du jour de la décision**, jamais antidatée dans la
       période contrôlée. Une écriture qui remonterait avant le comptage
       réécrirait par la bande un contrôle déjà rendu.

    Le motif est **obligatoire** : une régularisation sans cause écrite est
    exactement ce que cette décision cherche à empêcher.
    """
    # `count` est passé explicitement plutôt que lu par `block.count` : la
    # relation inverse n'est pas chargée par le `selectin` descendant, et un
    # accès paresseux en contexte async lève `MissingGreenlet`.
    if count.cashbox_id != cashbox.id:
        raise RegularisationRefused("Ce contrôle de caisse n'appartient pas à cette caisse.")
    if block.cash_count_id != count.id:
        raise RegularisationRefused("Cette devise n'appartient pas à ce contrôle de caisse.")
    if not reason.strip():
        raise RegularisationRefused("Motif de régularisation requis.")
    try:
        amount = ensure_finite(Decimal(amount), label="montant").quantize(CENTS)
    except DecimalInputError as e:
        raise RegularisationRefused(str(e)) from None
    if amount <= 0:
        raise RegularisationRefused("Le montant à régulariser doit être strictement positif.")

    remaining = await remaining_variance(db, block)
    if remaining == 0:
        raise RegularisationRefused(
            f"L'écart {block.currency} de ce contrôle est déjà entièrement régularisé."
        )
    if amount > abs(remaining):
        raise RegularisationRefused(
            f"Montant supérieur à l'écart restant à régulariser "
            f"({abs(remaining)} {block.currency})."
        )

    # Le sens suit celui de l'écart constaté, jamais celui d'une saisie : un
    # excédent se régularise par une entrée, un manquant par une sortie.
    if remaining > 0:
        signed, category = amount, "regularisation_excedent"
    else:
        signed, category = -amount, "regularisation_manquant"

    movement = await add_movement(
        db,
        cashbox,
        amount=signed,
        currency=block.currency,
        category=category,
        description=(
            f"Régularisation d'écart — contrôle du {count.counted_on} "
            f"({count.trigger_label}, déclaré par {count.declared_by_name}) : {reason.strip()}"
        ),
        recorded_by_id=recorded_by_id,
    )
    movement.settles_cash_count_id = count.id
    await db.flush()
    return movement
