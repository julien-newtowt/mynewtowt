"""Quoting — résolution de grille tarifaire multi-routes + calcul de devis.

Mécanique Module 6 (modèle multi-routes) :

- une grille tarifaire couvre **1 client (ou défaut) + 1 période + N routes** ;
  chaque **route** (``RateGridLine``) porte POL/POD, sa distance, son OPEX jour
  et son ``base_rate`` (OPEX × jours de mer / capacité navire, 978 EPAL) ;
- les **brackets de volume** (coefficients dégressifs) remontent au niveau
  **grille** (``brackets_json``), partagés par toutes les routes ;
- il existe une **grille par défaut** (``client_id NULL``, ``is_default=True``)
  multi-routes — sa route est créée à la demande si absente ;
- si le demandeur est un **client connu** (compte client relié à un client
  commercial), c'est **sa** grille qui s'applique dès qu'elle porte la route
  POL/POD demandée ; sinon repli sur la grille par défaut ;
- une grille porte des **options** (``RateGridOption``) tarifées à la
  palette, à la tonne chargée, à la réservation ou à la booking note, plus des
  forfaits documentaires (``bl_fee`` / ``booking_fee``) ; les options actives
  et les forfaits renseignés sont repris dans chaque devis.

Le prix public n'est plus affiché : il est restitué par l'outil de devis
(``/devis``) et par le wizard de réservation, via ce service.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.client_account import ClientAccount
from app.models.commercial import (
    DEFAULT_BRACKETS_SHIPPER,
    PALETTE_COEFFICIENTS,
    RATE_OPTION_UNIT_LABELS,
    RateGrid,
    RateGridLine,
    RateGridOption,
)
from app.models.finance import OpexParameter
from app.models.leg import Leg
from app.models.port import Port
from app.models.quote import Quote, QuoteLine
from app.models.vessel import Vessel

# Paramètres économiques de repli (formule historique NEWTOWT :
# base = OPEX jour × jours de navigation / capacité navire).
FALLBACK_OPEX_DAILY_EUR = Decimal("12000")
OPEX_PARAMETER_NAME = "opex_daily_sea"
# Capacité commerciale unique (P4, arbitrage direction) = capacité physique de
# cale (référentiel stowage Phoenix). Pilote le taux de base €/palette.
VESSEL_CAPACITY_PALETTES = Decimal("978")
TRANSIT_SPEED_KN = Decimal("8")
HAZARDOUS_SURCHARGE_RATE = Decimal("0.25")
QUOTE_VALIDITY_DAYS = 30

_TWO_PLACES = Decimal("0.01")


class QuotingError(Exception):
    """Erreur de calcul de devis (route inconnue, quantités invalides…)."""


@dataclass(frozen=True)
class QuoteLineDraft:
    kind: str  # freight | surcharge | option
    label: str
    unit: str | None
    quantity: Decimal
    unit_price_eur: Decimal
    total_eur: Decimal


@dataclass(frozen=True)
class GridQuote:
    grid_id: int
    grid_reference: str
    is_default_grid: bool
    base_rate_eur: Decimal
    bracket_label: str
    lines: list[QuoteLineDraft] = field(default_factory=list)
    freight_subtotal_eur: Decimal = Decimal("0")
    options_total_eur: Decimal = Decimal("0")
    total_eur: Decimal = Decimal("0")
    currency: str = "EUR"
    # Engagement minimum de volume (palettes) de la grille — None = aucun.
    volume_commitment: int | None = None
    # True si la quantité demandée est sous l'engagement minimum.
    below_commitment: bool = False


# ---------------------------------------------------------------------------
# Résolution de la grille applicable
# ---------------------------------------------------------------------------


def _grid_window_clause(on_date: date):
    return (
        RateGrid.status == "active",
        RateGrid.valid_from <= on_date,
        or_(RateGrid.valid_to.is_(None), RateGrid.valid_to >= on_date),
    )


def _match_route(grid: RateGrid, pol_locode: str, pod_locode: str) -> RateGridLine | None:
    """Ligne-route de la grille couvrant POL→POD (insensible à la casse)."""
    pol = pol_locode.upper().strip()
    pod = pod_locode.upper().strip()
    for line in grid.lines:
        if (line.pol_locode or "").upper() == pol and (line.pod_locode or "").upper() == pod:
            return line
    return None


async def resolve_grid(
    db: AsyncSession,
    *,
    pol_locode: str,
    pod_locode: str,
    on_date: date | None = None,
    commercial_client_id: int | None = None,
) -> tuple[RateGrid, RateGridLine]:
    """(grille, route) applicable : grille client (route POL/POD) sinon défaut.

    Recherche la grille active du client portant la route demandée ; à défaut,
    retombe sur la grille par défaut (dont la route est créée au besoin).
    """
    on_date = on_date or datetime.now(UTC).date()
    pol_locode = pol_locode.upper().strip()
    pod_locode = pod_locode.upper().strip()

    if commercial_client_id is not None:
        # Grille active du client portant la route exacte demandée.
        stmt = (
            select(RateGrid)
            .join(RateGridLine, RateGridLine.grid_id == RateGrid.id)
            .options(selectinload(RateGrid.lines), selectinload(RateGrid.options))
            .where(
                RateGrid.client_id == commercial_client_id,
                RateGridLine.pol_locode == pol_locode,
                RateGridLine.pod_locode == pod_locode,
                *_grid_window_clause(on_date),
            )
            .order_by(RateGrid.valid_from.desc())
        )
        grid = (await db.execute(stmt)).scalars().unique().first()
        if grid is not None:
            route = _match_route(grid, pol_locode, pod_locode)
            if route is not None:
                return grid, route

    # Repli : grille par défaut multi-routes (route créée si absente).
    return await ensure_default_grid(db, pol_locode=pol_locode, pod_locode=pod_locode)


async def ensure_default_grid(
    db: AsyncSession, *, pol_locode: str, pod_locode: str
) -> tuple[RateGrid, RateGridLine]:
    """(grille par défaut, route) — crée la grille et/ou la route au besoin."""
    pol_locode = pol_locode.upper().strip()
    pod_locode = pod_locode.upper().strip()
    today = datetime.now(UTC).date()
    stmt = (
        select(RateGrid)
        .options(selectinload(RateGrid.lines), selectinload(RateGrid.options))
        .where(
            RateGrid.is_default.is_(True),
            RateGrid.client_id.is_(None),
            *_grid_window_clause(today),
        )
        .order_by(RateGrid.valid_from.desc())
        .limit(1)
    )
    grid = (await db.execute(stmt)).scalar_one_or_none()
    if grid is None:
        grid = RateGrid(
            reference=_generate_grid_reference(default=True),
            client_id=None,
            is_default=True,
            status="active",
            valid_from=today,
            valid_to=None,
            currency="EUR",
            adjustment_index=Decimal("1.0000"),
            brackets_json=json.dumps(DEFAULT_BRACKETS_SHIPPER),
            notes="Grille par défaut générée automatiquement (formule OPEX).",
        )
        db.add(grid)
        await db.flush()
        # Options standard de la grille par défaut : la booking note est
        # facturée d'office ; la manutention est fournie comme exemple inactif
        # que le commercial active/ajuste.
        db.add(
            RateGridOption(
                grid_id=grid.id,
                code="BOOKING_NOTE",
                label="Booking note & dossier documentaire",
                unit="per_booking_note",
                amount_eur=Decimal("50.00"),
                is_active=True,
            )
        )
        db.add(
            RateGridOption(
                grid_id=grid.id,
                code="THC",
                label="Manutention portuaire (THC)",
                unit="per_palette",
                amount_eur=Decimal("12.00"),
                is_active=False,
            )
        )
        await db.flush()
        await db.refresh(grid, attribute_names=["lines", "options"])

    route = _match_route(grid, pol_locode, pod_locode)
    if route is None:
        distance, nav_days, opex_daily, base = await compute_route_economics(
            db, pol_locode=pol_locode, pod_locode=pod_locode, vessel_id=grid.vessel_id
        )
        route = RateGridLine(
            grid_id=grid.id,
            pol_locode=pol_locode,
            pod_locode=pod_locode,
            distance_nm=distance,
            nav_days=nav_days,
            opex_daily=opex_daily,
            base_rate=base,
            is_manual=False,
        )
        db.add(route)
        await db.flush()
        await db.refresh(grid, attribute_names=["lines"])
        route = _match_route(grid, pol_locode, pod_locode) or route
    return grid, route


async def backfill_default_grids(db: AsyncSession) -> int:
    """Crée une route par défaut pour chaque route POL/POD présente au planning."""
    pol = Port.__table__.alias("pol")
    pod = Port.__table__.alias("pod")
    stmt = (
        select(pol.c.locode, pod.c.locode)
        .select_from(
            Leg.__table__.join(pol, pol.c.id == Leg.departure_port_id).join(
                pod, pod.c.id == Leg.arrival_port_id
            )
        )
        .distinct()
    )
    created = 0
    for pol_locode, pod_locode in (await db.execute(stmt)).all():
        if not pol_locode or not pod_locode:
            continue
        existing = await db.scalar(
            select(RateGridLine.id)
            .join(RateGrid, RateGrid.id == RateGridLine.grid_id)
            .where(
                RateGrid.is_default.is_(True),
                RateGrid.client_id.is_(None),
                RateGridLine.pol_locode == pol_locode,
                RateGridLine.pod_locode == pod_locode,
            )
            .limit(1)
        )
        if existing is None:
            await ensure_default_grid(db, pol_locode=pol_locode, pod_locode=pod_locode)
            created += 1
    return created


async def _resolve_opex_daily(db: AsyncSession, vessel_id: int | None) -> Decimal:
    """OPEX jour : navire de la grille → paramètre global → repli historique."""
    if vessel_id is not None:
        vessel = await db.get(Vessel, vessel_id)
        if vessel is not None and vessel.opex_daily_sea_eur is not None:
            return Decimal(str(vessel.opex_daily_sea_eur))
    opex_daily = await db.scalar(
        select(OpexParameter.parameter_value).where(
            OpexParameter.parameter_name == OPEX_PARAMETER_NAME
        )
    )
    return Decimal(opex_daily) if opex_daily is not None else FALLBACK_OPEX_DAILY_EUR


def route_nav_days(distance_nm: Decimal) -> Decimal:
    """Jours de navigation = distance / (8 nœuds × 24 h)."""
    return (Decimal(distance_nm) / (TRANSIT_SPEED_KN * Decimal("24"))).quantize(
        Decimal("0.001"), rounding=ROUND_HALF_UP
    )


def route_base_rate(opex_daily: Decimal, nav_days: Decimal) -> Decimal:
    """Taux de base €/palette = OPEX jour × jours de mer / 978 (plancher 1 €)."""
    base = (Decimal(opex_daily) * Decimal(nav_days) / VESSEL_CAPACITY_PALETTES).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )
    return max(base, Decimal("1.00"))


async def compute_route_economics(
    db: AsyncSession,
    *,
    pol_locode: str,
    pod_locode: str,
    vessel_id: int | None = None,
    leg: Leg | None = None,
    distance_nm: Decimal | None = None,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """(distance_nm, nav_days, opex_daily, base_rate) d'une route.

    Distance : valeur fournie (saisie) → leg → ports (haversine/table de repli,
    cf. services.anemos). OPEX jour : navire de la grille → paramètre global →
    repli historique.
    """
    from app.services.anemos import resolve_distance_nm  # import tardif (cycle co2)

    if distance_nm is None:
        pol = (
            await db.execute(select(Port).where(Port.locode == pol_locode.upper().strip()))
        ).scalar_one_or_none()
        pod = (
            await db.execute(select(Port).where(Port.locode == pod_locode.upper().strip()))
        ).scalar_one_or_none()
        distance_nm = resolve_distance_nm(leg, pol, pod)
    distance = Decimal(distance_nm).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    opex_daily = await _resolve_opex_daily(db, vessel_id)
    nav_days = route_nav_days(distance)
    base = route_base_rate(opex_daily, nav_days)
    return distance, nav_days, opex_daily, base


def _generate_grid_reference(*, default: bool) -> str:
    year = datetime.now(UTC).year
    prefix = "RGD" if default else "RG"
    return f"{prefix}-{year}-{secrets.token_hex(2).upper()}"


# ---------------------------------------------------------------------------
# Calcul du devis sur une grille
# ---------------------------------------------------------------------------


def _bracket_label(bracket: dict) -> str:
    return str(bracket.get("label") or bracket.get("key") or bracket.get("max_qty") or "")


def bracket_for_quantity(grid: RateGrid, qty: int) -> tuple[str, Decimal]:
    """(label, coeff) de la bracket de volume applicable à ``qty`` palettes.

    Les brackets sont portés par la grille (``brackets_json``), partagés par
    toutes ses routes.
    """
    brackets = sorted(grid.brackets, key=lambda b: int(b["max_qty"]))
    for bracket in brackets:
        if qty <= int(bracket["max_qty"]):
            return _bracket_label(bracket), Decimal(str(bracket["coeff"]))
    if brackets:
        last = brackets[-1]
        return _bracket_label(last), Decimal(str(last["coeff"]))
    return "Tarif unique", Decimal("1.0")


def compute_grid_quote(
    grid: RateGrid,
    route: RateGridLine,
    *,
    items: list[tuple[str, int]],
    tonnage_t: Decimal | None = None,
    hazardous: bool = False,
) -> GridQuote:
    """Calcule un devis : fret (base_rate de la route) + surcharges + options.

    Fonction pure : la grille (brackets/options) et la ``route`` (base_rate)
    doivent être chargées. ``route`` est la ligne POL/POD de la grille issue de
    ``resolve_grid``.
    """
    total_palettes = sum(count for _fmt, count in items)
    if total_palettes <= 0:
        raise QuotingError("Au moins une palette est requise pour coter.")

    bracket_label, bracket_coeff = bracket_for_quantity(grid, total_palettes)
    effective_base = Decimal(route.base_rate) * Decimal(grid.adjustment_index) * bracket_coeff

    lines: list[QuoteLineDraft] = []
    freight_subtotal = Decimal("0")
    for fmt, count in items:
        if count <= 0:
            continue
        coef = Decimal(str(PALETTE_COEFFICIENTS.get(fmt, 1.0)))
        unit_price = (effective_base * coef).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        line_total = (unit_price * Decimal(count)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        freight_subtotal += line_total
        lines.append(
            QuoteLineDraft(
                kind="freight",
                label=f"Fret maritime — palette {fmt}",
                unit="per_palette",
                quantity=Decimal(count),
                unit_price_eur=unit_price,
                total_eur=line_total,
            )
        )

    if hazardous and freight_subtotal > 0:
        # Taux IMDG : configurable par grille (points de %), sinon défaut global.
        haz_rate = (
            (Decimal(grid.hazardous_surcharge_pct) / Decimal("100"))
            if grid.hazardous_surcharge_pct is not None
            else HAZARDOUS_SURCHARGE_RATE
        )
        surcharge = (freight_subtotal * haz_rate).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        lines.append(
            QuoteLineDraft(
                kind="surcharge",
                label="Majoration marchandises dangereuses (IMDG)",
                unit=None,
                quantity=Decimal("1"),
                unit_price_eur=surcharge,
                total_eur=surcharge,
            )
        )
    else:
        surcharge = Decimal("0")

    options_total = Decimal("0")
    for opt in grid.options:
        if not opt.is_active:
            continue
        qty = _option_quantity(opt.unit, total_palettes=total_palettes, tonnage_t=tonnage_t)
        if qty <= 0:
            continue
        amount = Decimal(opt.amount_eur)
        line_total = (amount * qty).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        options_total += line_total
        unit_label = RATE_OPTION_UNIT_LABELS.get(opt.unit, opt.unit)
        lines.append(
            QuoteLineDraft(
                kind="option",
                label=f"{opt.label} ({unit_label})",
                unit=opt.unit,
                quantity=qty,
                unit_price_eur=amount,
                total_eur=line_total,
            )
        )

    # Forfaits documentaires de l'en-tête (sucre au-dessus des options) — repris
    # une fois par devis lorsqu'ils sont renseignés.
    for fee, fee_label in (
        (grid.booking_fee, "Frais de réservation (booking)"),
        (grid.bl_fee, "Frais de connaissement (BL)"),
    ):
        if fee is None:
            continue
        amount = Decimal(fee).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        if amount <= 0:
            continue
        options_total += amount
        lines.append(
            QuoteLineDraft(
                kind="option",
                label=fee_label,
                unit="per_booking",
                quantity=Decimal("1"),
                unit_price_eur=amount,
                total_eur=amount,
            )
        )

    total = (freight_subtotal + surcharge + options_total).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )

    # Minimum de facturation (paramétrage fin) : si le total est en-deçà du
    # minimum de la grille, on ajoute une ligne d'ajustement portant au plancher.
    if grid.min_charge_eur is not None and total < Decimal(grid.min_charge_eur):
        topup = (Decimal(grid.min_charge_eur) - total).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        lines.append(
            QuoteLineDraft(
                kind="surcharge",
                label="Ajustement minimum de facturation",
                unit=None,
                quantity=Decimal("1"),
                unit_price_eur=topup,
                total_eur=topup,
            )
        )
        options_total += topup
        total = Decimal(grid.min_charge_eur).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)

    return GridQuote(
        grid_id=grid.id,
        grid_reference=grid.reference,
        is_default_grid=bool(grid.is_default),
        base_rate_eur=effective_base.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP),
        bracket_label=bracket_label,
        lines=lines,
        freight_subtotal_eur=freight_subtotal.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP),
        options_total_eur=options_total.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP),
        total_eur=total,
        currency=grid.currency,
        volume_commitment=grid.volume_commitment,
        below_commitment=bool(grid.volume_commitment and total_palettes < grid.volume_commitment),
    )


def _option_quantity(unit: str, *, total_palettes: int, tonnage_t: Decimal | None) -> Decimal:
    if unit == "per_palette":
        return Decimal(total_palettes)
    if unit == "per_tonne":
        return Decimal(tonnage_t) if tonnage_t else Decimal("0")
    if unit in ("per_booking", "per_booking_note"):
        return Decimal("1")
    return Decimal("0")


# ---------------------------------------------------------------------------
# Persistance d'un devis
# ---------------------------------------------------------------------------


def generate_quote_reference() -> str:
    year = datetime.now(UTC).year
    return f"DEV-{year}-{secrets.token_hex(3).upper()}"


async def create_quote(
    db: AsyncSession,
    *,
    computed: GridQuote,
    pol_locode: str,
    pod_locode: str,
    leg: Leg | None = None,
    client_account: ClientAccount | None = None,
    contact_name: str | None = None,
    contact_email: str | None = None,
    contact_company: str | None = None,
    palettes_total: int,
    tonnage_t: Decimal | None,
    hazardous: bool,
    items: list[tuple[str, int]] | None = None,
    lang: str = "fr",
) -> Quote:
    quote = Quote(
        reference=generate_quote_reference(),
        status="issued",
        pol_locode=pol_locode.upper(),
        pod_locode=pod_locode.upper(),
        leg_id=leg.id if leg is not None else None,
        etd_snapshot=leg.etd if leg is not None else None,
        grid_id=computed.grid_id,
        grid_reference=computed.grid_reference,
        client_account_id=client_account.id if client_account is not None else None,
        contact_name=contact_name,
        contact_email=contact_email,
        contact_company=contact_company,
        palettes_total=palettes_total,
        tonnage_t=tonnage_t,
        hazardous=hazardous,
        currency=computed.currency,
        freight_subtotal_eur=computed.freight_subtotal_eur,
        options_total_eur=computed.options_total_eur,
        total_eur=computed.total_eur,
        valid_until=(datetime.now(UTC) + timedelta(days=QUOTE_VALIDITY_DAYS)).date(),
        items_json=json.dumps([[f, c] for f, c in items]) if items else None,
        lang=lang,
    )
    db.add(quote)
    await db.flush()
    for idx, line in enumerate(computed.lines):
        db.add(
            QuoteLine(
                quote_id=quote.id,
                position=idx,
                kind=line.kind,
                label=line.label,
                unit=line.unit,
                quantity=line.quantity,
                unit_price_eur=line.unit_price_eur,
                total_eur=line.total_eur,
            )
        )
    await db.flush()
    return quote


async def find_quote(db: AsyncSession, reference: str) -> Quote | None:
    stmt = (
        select(Quote).options(selectinload(Quote.lines)).where(Quote.reference == reference.upper())
    )
    return (await db.execute(stmt)).scalar_one_or_none()
