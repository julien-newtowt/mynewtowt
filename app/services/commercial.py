"""Commercial — generators de référence, calc tarifs dégressifs, conversions.

Logique reprise de la V3.0.0 :
- Référence orders ORD-YYYY-NNNN (séquence par année).
- Référence grilles RG-YYYY-NNNN.
- Référence offres RO-YYYY-NNNN.
- Bracket lookup : retourne la 1re bracket dont max_qty >= qty.
- Bracket rate : base_rate × coeff × adjustment_index.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, time
from datetime import date as _date
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial import (
    DEFAULT_BRACKETS_FF,
    DEFAULT_BRACKETS_SHIPPER,
    MAX_PAYMENT_TERMS,
    PAYMENT_TRIGGERS,
    Order,
    RateGrid,
    RateOffer,
)

# ─────────────────────────── References ────────────────────────────


async def _next_seq(db: AsyncSession, model, prefix: str, year: int) -> int:
    """Return the next sequence number for {prefix}-{year}-NNNN."""
    pattern = f"{prefix}-{year}-%"
    stmt = select(func.count(model.id)).where(model.reference.like(pattern))
    count = (await db.scalar(stmt)) or 0
    return count + 1


async def next_order_reference(db: AsyncSession, year: int | None = None) -> str:
    y = year or _date.today().year
    n = await _next_seq(db, Order, "ORD", y)
    return f"ORD-{y}-{n:04d}"


async def next_grid_reference(db: AsyncSession, year: int | None = None) -> str:
    y = year or _date.today().year
    n = await _next_seq(db, RateGrid, "RG", y)
    return f"RG-{y}-{n:04d}"


async def next_offer_reference(db: AsyncSession, year: int | None = None) -> str:
    y = year or _date.today().year
    n = await _next_seq(db, RateOffer, "RO", y)
    return f"RO-{y}-{n:04d}"


def route_tariff_reference(
    *,
    valid_from: _date,
    valid_to: _date | None,
    pol_country: str | None,
    pod_country: str | None,
) -> str:
    """Référence tarifaire codifiée d'une route : ``P-MMAA-MMAA-XX-YY``.

    * ``MMAA`` — mois et année de début, puis de fin de validité.
    * ``XX`` / ``YY`` — code pays **ISO alpha-2** du POL puis du POD.

    Une grille sans date de fin (validité ouverte) porte ``----`` à la place du
    second ``MMAA`` : inventer une échéance serait un mensonge contractuel, et
    laisser le segment vide rendrait la référence ambiguë à la lecture.
    Un pays inconnu donne ``??`` — visible, plutôt que silencieusement faux.
    """

    def _mmaa(d: _date | None) -> str:
        return f"{d.month:02d}{d.year % 100:02d}" if d is not None else "----"

    def _cc(code: str | None) -> str:
        clean = (code or "").strip().upper()
        return clean[:2] if len(clean) >= 2 else "??"

    return (
        f"P-{_mmaa(valid_from)}-{_mmaa(valid_to)}-{_cc(pol_country)}-{_cc(pod_country)}"
    )


# ─────────────────────────── Pricing ───────────────────────────────


def default_brackets_for(client_type: str) -> list[dict]:
    return list(
        DEFAULT_BRACKETS_FF if client_type == "freight_forwarder" else DEFAULT_BRACKETS_SHIPPER
    )


async def assign_tariff_reference(db: AsyncSession, grid: RateGrid, route) -> str:
    """Calcule et pose la référence codifiée d'une route (``P-MMAA-MMAA-XX-YY``).

    Le pays vient du référentiel ``ports`` (ISO alpha-2), pas du LOCODE : les deux
    premiers caractères d'un LOCODE **sont** le code pays dans la norme UN, mais
    s'y fier ferait porter la référence par une convention de nommage plutôt que
    par la donnée — et un port mal saisi passerait inaperçu.
    """
    from app.models.port import Port

    countries: dict[str, str | None] = {}
    for locode in (route.pol_locode, route.pod_locode):
        clean = (locode or "").strip().upper()
        if clean and clean not in countries:
            port = (
                await db.execute(select(Port).where(Port.locode == clean))
            ).scalar_one_or_none()
            countries[clean] = port.country if port is not None else None

    route.tariff_reference = route_tariff_reference(
        valid_from=grid.valid_from,
        valid_to=grid.valid_to,
        pol_country=countries.get((route.pol_locode or "").strip().upper()),
        pod_country=countries.get((route.pod_locode or "").strip().upper()),
    )
    return route.tariff_reference


async def refresh_grid_references(db: AsyncSession, grid: RateGrid) -> int:
    """Recalcule la référence codifiée de **toutes** les routes d'une grille.

    À appeler dès que la période de validité change : la référence encode les
    mois de début et de fin, elle mentirait sinon.
    """
    for route in grid.lines:
        await assign_tariff_reference(db, grid, route)
    return len(grid.lines)


# ─────────────────── Conditions de règlement (déclaratif) ───────────────────


class PaymentTermError(ValueError):
    """Échéancier de règlement invalide."""


def validate_payment_terms(terms: list[dict]) -> list[dict]:
    """Valide et normalise un échéancier (1 à 3 règlements).

    Règles : au plus ``MAX_PAYMENT_TERMS`` échéances, déclencheur connu, part
    strictement positive, **somme exactement égale à 100 %**, et nombre de jours
    obligatoire pour ``days_before_etd``. Un échéancier qui ne totalise pas 100 %
    n'est pas un contrat : il laisserait une part du fret sans date d'exigibilité.

    Renvoie la liste normalisée (positions renumérotées à partir de 1) ; lève
    :class:`PaymentTermError` sinon. Une liste vide est acceptée — la grille n'a
    alors pas de conditions particulières.
    """
    if not terms:
        return []
    if len(terms) > MAX_PAYMENT_TERMS:
        raise PaymentTermError(
            f"Au plus {MAX_PAYMENT_TERMS} règlements par grille tarifaire."
        )

    normalised: list[dict] = []
    total = Decimal("0")
    for position, raw in enumerate(terms, start=1):
        trigger = (raw.get("trigger") or "").strip()
        if trigger not in PAYMENT_TRIGGERS:
            raise PaymentTermError(f"Déclencheur de règlement inconnu : {trigger or '—'}.")

        try:
            percentage = Decimal(str(raw.get("percentage") or "0").replace(",", "."))
        except (InvalidOperation, TypeError) as exc:
            raise PaymentTermError("Part de règlement invalide.") from exc
        if percentage <= 0 or percentage > 100:
            raise PaymentTermError("Chaque part de règlement doit être comprise entre 0 et 100 %.")

        offset_days: int | None = None
        if trigger == "days_before_etd":
            raw_days = raw.get("offset_days")
            try:
                offset_days = int(raw_days)
            except (TypeError, ValueError) as exc:
                raise PaymentTermError(
                    "Indiquez le nombre de jours avant le départ du navire."
                ) from exc
            if offset_days < 0:
                raise PaymentTermError("Le nombre de jours avant départ ne peut être négatif.")

        total += percentage
        normalised.append(
            {
                "position": position,
                "trigger": trigger,
                "offset_days": offset_days,
                "percentage": percentage.quantize(Decimal("0.01")),
                "label": (raw.get("label") or "").strip() or None,
            }
        )

    if total != Decimal("100.00"):
        raise PaymentTermError(
            f"La somme des règlements doit faire exactement 100 % (actuellement {total} %)."
        )
    return normalised


def pick_bracket(brackets: Iterable[dict], qty: int) -> dict | None:
    """Premier palier dont la borne haute **incluse** couvre ``qty``.

    ``max_qty`` à ``None`` désigne le palier non borné (« navire complet »).
    """
    from app.services.quoting import bracket_upper_bound

    sorted_b = sorted(brackets, key=bracket_upper_bound)
    for b in sorted_b:
        if qty <= bracket_upper_bound(b):
            return b
    return sorted_b[-1] if sorted_b else None


def bracket_rate(
    *,
    base_rate: Decimal,
    coeff: Decimal | float,
    adjustment_index: Decimal | float = Decimal("1.0"),
) -> Decimal:
    return (Decimal(base_rate) * Decimal(coeff) * Decimal(adjustment_index)).quantize(
        Decimal("0.01")
    )


def compute_offer_total(
    *,
    base_rate: Decimal,
    coeff: Decimal | float,
    adjustment_index: Decimal | float,
    qty: int,
) -> Decimal:
    return (
        bracket_rate(base_rate=base_rate, coeff=coeff, adjustment_index=adjustment_index) * qty
    ).quantize(Decimal("0.01"))


# ───────────────────────── Affectation commande → leg (COM-01) ──────────────


def leg_is_late_for_order(leg, order) -> bool:
    """True si l'ETA du ``leg`` dépasse la fin de la fenêtre de livraison
    souhaitée de la ``order`` (``delivery_date_end``). Sans fenêtre ou sans
    ETA, aucune commande n'est « hors délai ».

    On compare des **instants** (pas ``eta.date()``) contre la fin de journée
    UTC de la date butoir : un navire arrivant à 23 h UTC le jour J reste dans
    les délais ; à 00 h 30 le lendemain il est en retard. Cela évite l'aléa de
    troncature ``.date()`` aux abords de minuit (retard faussement posé d'un
    jour selon le fuseau). Un ETA naïf (SQLite de test) est interprété en UTC.
    """
    if order.delivery_date_end is None or leg.eta is None:
        return False
    deadline = datetime.combine(order.delivery_date_end, time(23, 59, 59), tzinfo=UTC)
    eta = leg.eta if leg.eta.tzinfo is not None else leg.eta.replace(tzinfo=UTC)
    return eta > deadline


def suggest_leg_for_order(legs: Iterable, order):
    """Suggère le meilleur leg pour une commande : le premier compatible
    livrant dans les délais ; à défaut, le premier compatible (le plus tôt).
    ``legs`` est supposé déjà trié par ETD croissant.
    """
    legs = list(legs)
    on_time = [lg for lg in legs if not leg_is_late_for_order(lg, order)]
    if on_time:
        return on_time[0]
    return legs[0] if legs else None


async def compatible_legs_for_order(db: AsyncSession, order) -> list:
    """Legs candidats à l'affectation d'une commande : filtrés sur la route
    souhaitée (POL/POD locodes de la commande) et non encore partis
    (``atd`` NULL), triés par ETD croissant.

    Sans route renseignée, retourne tous les legs à venir (le broker affine).
    """
    from sqlalchemy.orm import aliased

    from app.models.leg import Leg
    from app.models.port import Port

    dep = aliased(Port)
    arr = aliased(Port)
    stmt = (
        select(Leg)
        .join(dep, Leg.departure_port_id == dep.id)
        .join(arr, Leg.arrival_port_id == arr.id)
        .where(Leg.atd.is_(None))
    )
    if order.departure_locode:
        stmt = stmt.where(dep.locode == order.departure_locode.upper())
    if order.arrival_locode:
        stmt = stmt.where(arr.locode == order.arrival_locode.upper())
    stmt = stmt.order_by(Leg.etd.asc())
    return list((await db.execute(stmt)).scalars().all())
