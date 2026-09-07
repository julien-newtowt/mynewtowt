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
    DEFAULT_TARGET_MARGIN_PCT,
    PALETTE_COEFFICIENTS,
    RATE_OPTION_UNIT_LABELS,
    RATE_UNIT_PALETTE,
    RATE_UNIT_SHORT_LABELS,
    RATE_UNIT_TONNE,
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
# Aucune capacité en tonnes de référence n'est codée en dur : le port en lourd
# vit dans le référentiel flotte (``Vessel.dwt``, écran Admin → Flotte). Sans
# lui, le coût de revient d'une route tarifée **à la tonne** n'est pas
# calculable — et c'est ce que dit ``cost_rate = None``, plutôt qu'un nombre
# inventé qui se propagerait dans une marge affichée comme un fait.
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
    # Unité du ``base_rate_eur`` : « palette » ou « tonne ». Rendre un montant
    # sans son unité laisserait un €/tonne se lire comme un €/palette.
    rate_unit: str = RATE_UNIT_PALETTE
    lines: list[QuoteLineDraft] = field(default_factory=list)
    freight_subtotal_eur: Decimal = Decimal("0")
    options_total_eur: Decimal = Decimal("0")
    total_eur: Decimal = Decimal("0")
    currency: str = "EUR"
    # Engagement minimum de volume (palettes) de la grille — None = aucun.
    volume_commitment: int | None = None
    # True si la quantité demandée est sous l'engagement minimum.
    below_commitment: bool = False

    @property
    def rate_unit_short(self) -> str:
        """« palette » / « tonne » — suffixe d'affichage du taux de base."""
        return RATE_UNIT_SHORT_LABELS.get(self.rate_unit, RATE_UNIT_SHORT_LABELS[RATE_UNIT_PALETTE])


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
        # Grilles actives du client portant la route exacte demandée. Un client
        # peut en avoir plusieurs valides au même instant (périodes ou conditions
        # distinctes) : celle marquée **par défaut sur cette route**
        # (``is_route_default``) l'emporte, sinon la plus récemment ouverte.
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
            .order_by(RateGridLine.is_route_default.desc(), RateGrid.valid_from.desc())
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
        distance, nav_days, opex_daily, cost = await compute_route_economics(
            db, pol_locode=pol_locode, pod_locode=pod_locode, vessel_id=grid.vessel_id
        )
        # Route matérialisée automatiquement (grille par défaut) : personne n'a
        # annoncé de prix, donc le prix part **au coût** — marge nulle, ce qui
        # est la vérité tant qu'un commercial ne l'a pas repris.
        route = RateGridLine(
            grid_id=grid.id,
            pol_locode=pol_locode,
            pod_locode=pod_locode,
            distance_nm=distance,
            nav_days=nav_days,
            opex_daily=opex_daily,
            base_rate=cost if cost is not None else Decimal("1.00"),
            cost_rate=cost,
            rate_unit=RATE_UNIT_PALETTE,
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


def route_nav_days(distance_nm: Decimal, speed_kn: Decimal | None = None) -> Decimal:
    """Jours de navigation = distance / (vitesse × 24 h).

    ``speed_kn`` vient du leg visé quand il est connu (``Leg.transit_speed_kn``) ;
    sinon on retombe sur la vitesse de référence. Une vitesse nulle ou négative
    est ignorée — elle donnerait une durée infinie, donc un tarif absurde.
    """
    speed = Decimal(speed_kn) if speed_kn is not None else TRANSIT_SPEED_KN
    if speed <= 0:
        speed = TRANSIT_SPEED_KN
    return (Decimal(distance_nm) / (speed * Decimal("24"))).quantize(
        Decimal("0.001"), rounding=ROUND_HALF_UP
    )


def route_cost_rate(
    opex_daily: Decimal, nav_days: Decimal, capacity_palettes: Decimal | int | None = None
) -> Decimal:
    """Coût de revient €/palette = OPEX jour × jours de mer / capacité (plancher 1 €).

    La capacité est celle du **navire de référence** quand il est connu ; sinon la
    capacité commerciale de référence. Calculer sur une capacité fictive alors
    que la disponibilité affichée utilise la capacité réelle du navire faisait
    diverger le coût et la cale vendue dès qu'un navire s'écartait de 978.
    """
    capacity = Decimal(capacity_palettes) if capacity_palettes else VESSEL_CAPACITY_PALETTES
    if capacity <= 0:
        capacity = VESSEL_CAPACITY_PALETTES
    base = (Decimal(opex_daily) * Decimal(nav_days) / capacity).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )
    return max(base, Decimal("1.00"))


#: Nom historique de ``route_cost_rate``. La valeur n'a pas changé — c'est le
#: **sens** qui a été corrigé (COM-12) : elle a toujours été un coût, et non un
#: prix. L'alias reste pour les appelants existants.
route_base_rate = route_cost_rate


def route_cost_per_tonne(
    opex_daily: Decimal, nav_days: Decimal, capacity_tonnes: Decimal | None
) -> Decimal | None:
    """Coût de revient €/tonne = OPEX jour × jours de mer / port en lourd.

    ``None`` quand le port en lourd du navire de référence est inconnu : sans
    lui, il n'existe aucune façon honnête de ramener un coût journalier à la
    tonne. L'écran affiche « — » et renvoie vers Admin → Flotte, exactement
    comme la distance théorique d'un leg dont un port n'a pas de coordonnées.
    """
    if capacity_tonnes is None or Decimal(capacity_tonnes) <= 0:
        return None
    cost = (Decimal(opex_daily) * Decimal(nav_days) / Decimal(capacity_tonnes)).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )
    return max(cost, Decimal("1.00"))


def suggested_price(
    cost_rate: Decimal | None, target_margin_pct: Decimal | None = None
) -> Decimal | None:
    """Prix **proposé** à partir du coût et d'un taux de marge sur prix de vente.

    ``prix = coût / (1 − taux)``. Le logiciel propose, le commercial confirme :
    rien n'écrit cette valeur en base sans une action explicite de l'opérateur.
    ``None`` quand le coût est inconnu — proposer un prix sans coût reviendrait
    à inventer la marge qu'on prétend calculer.
    """
    if cost_rate is None:
        return None
    rate = Decimal(
        target_margin_pct if target_margin_pct is not None else DEFAULT_TARGET_MARGIN_PCT
    )
    if rate < 0 or rate >= 100:
        rate = DEFAULT_TARGET_MARGIN_PCT
    price = (Decimal(cost_rate) / (Decimal("1") - rate / Decimal("100"))).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )
    return max(price, Decimal("1.00"))


async def _fleet_deadweight_t(db: AsyncSession) -> Decimal | None:
    """Port en lourd de référence de la flotte : moyenne des navires actifs qui
    en déclarent un.

    Sert quand aucun navire de référence n'est désigné — ce qui est désormais le
    cas général, la grille ne portant plus de navire (les OPEX sont les mêmes
    pour toute la flotte). La flotte étant composée de sisterships TSC 80, la
    moyenne est exacte ; elle resterait défendable si elle ne l'était plus. Sans
    aucun port en lourd renseigné, renvoie ``None`` — et le coût à la tonne est
    déclaré non calculable au lieu d'être approché.
    """
    rows = (
        (
            await db.execute(
                select(Vessel.dwt).where(Vessel.is_active.is_(True), Vessel.dwt.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    values = [Decimal(str(v)) for v in rows if v and Decimal(str(v)) > 0]
    if not values:
        return None
    return (sum(values) / Decimal(len(values))).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


async def compute_route_economics(
    db: AsyncSession,
    *,
    pol_locode: str,
    pod_locode: str,
    vessel_id: int | None = None,
    leg: Leg | None = None,
    distance_nm: Decimal | None = None,
    rate_unit: str = RATE_UNIT_PALETTE,
) -> tuple[Decimal, Decimal, Decimal, Decimal | None]:
    """(distance_nm, nav_days, opex_daily, **cost_rate**) d'une route.

    Le quatrième terme est le **coût de revient**, dans l'unité ``rate_unit`` —
    ce n'est pas un prix : le prix est annoncé par le commercial (COM-12).

    Distance : valeur fournie (saisie) → leg → ports (haversine/table de repli,
    cf. services.anemos). OPEX jour : navire de la grille → paramètre global →
    repli historique. **Vitesse et capacité** : celles du leg / du navire quand
    ils sont connus, sinon les valeurs de référence (cf. ``route_nav_days``,
    ``route_cost_rate`` et ``route_cost_per_tonne``).

    Le coût vaut ``None`` **uniquement** pour une route à la tonne dont aucun
    port en lourd n'est connu : à l'emplacement, la capacité de référence (978)
    est toujours disponible.
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

    # Vitesse réelle du leg visé, capacité réelle du navire de référence — à
    # défaut, les valeurs de référence. Le navire du leg prime sur ``vessel_id``
    # (grille) : c'est lui qui portera effectivement la marchandise.
    speed_kn = (
        Decimal(str(leg.transit_speed_kn)) if leg is not None and leg.transit_speed_kn else None
    )
    capacity: Decimal | None = None
    deadweight_t: Decimal | None = None
    reference_vessel_id = (leg.vessel_id if leg is not None else None) or vessel_id
    if reference_vessel_id is not None:
        vessel = await db.get(Vessel, reference_vessel_id)
        if vessel is not None and vessel.capacity_palettes:
            capacity = Decimal(vessel.capacity_palettes)
        if vessel is not None and vessel.dwt:
            deadweight_t = Decimal(str(vessel.dwt))

    nav_days = route_nav_days(distance, speed_kn)
    if rate_unit == RATE_UNIT_TONNE:
        if deadweight_t is None:
            deadweight_t = await _fleet_deadweight_t(db)
        cost = route_cost_per_tonne(opex_daily, nav_days, deadweight_t)
    else:
        cost = route_cost_rate(opex_daily, nav_days, capacity)
    return distance, nav_days, opex_daily, cost


def _generate_grid_reference(*, default: bool) -> str:
    year = datetime.now(UTC).year
    prefix = "RGD" if default else "RG"
    return f"{prefix}-{year}-{secrets.token_hex(2).upper()}"


# ---------------------------------------------------------------------------
# Calcul du devis sur une grille
# ---------------------------------------------------------------------------


def _bracket_label(bracket: dict) -> str:
    return str(bracket.get("label") or bracket.get("key") or bracket.get("max_qty") or "")


def bracket_upper_bound(bracket: dict) -> float:
    """Borne haute **incluse** d'un palier ; ``inf`` pour le palier non borné.

    Le palier « navire complet » n'a pas de plafond métier : le représenter par
    un nombre en dur (l'ancienne capacité 850) faisait retomber toute quantité
    supérieure sur un repli silencieux. ``max_qty`` absent ou ``None`` vaut donc
    « sans limite ».
    """
    raw = bracket.get("max_qty")
    if raw is None or raw == "":
        return float("inf")
    try:
        return float(int(raw))
    except (TypeError, ValueError):
        return float("inf")


def bracket_for_quantity(grid: RateGrid, qty: int) -> tuple[str, Decimal]:
    """(label, coeff) de la bracket de volume applicable à ``qty`` palettes.

    Les brackets sont portés par la grille (``brackets_json``), partagés par
    toutes ses routes. ``max_qty`` est la **borne haute incluse** ; ``None``
    désigne le palier non borné (« navire complet »).
    """
    brackets = sorted(grid.brackets, key=bracket_upper_bound)
    for bracket in brackets:
        if qty <= bracket_upper_bound(bracket):
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
    rate_unit = getattr(route, "rate_unit", RATE_UNIT_PALETTE) or RATE_UNIT_PALETTE

    lines: list[QuoteLineDraft] = []
    freight_subtotal = Decimal("0")
    if rate_unit == RATE_UNIT_TONNE:
        # Route vendue **au poids**. Le tonnage n'est pas déductible du nombre
        # de palettes : refuser est la seule réponse honnête, sinon le devis
        # facturerait un poids inventé. Le palier de volume et la capacité de
        # cale restent comptés en emplacements — c'est bien de la cale qui est
        # occupée, quelle que soit l'unité de facturation.
        if tonnage_t is None or Decimal(tonnage_t) <= 0:
            raise QuotingError(
                "Cette route est tarifée à la tonne : indiquez le tonnage de la marchandise."
            )
        tonnage = Decimal(tonnage_t)
        unit_price = effective_base.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        line_total = (unit_price * tonnage).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        freight_subtotal = line_total
        lines.append(
            QuoteLineDraft(
                kind="freight",
                label="Fret maritime — au poids",
                unit="per_tonne",
                quantity=tonnage,
                unit_price_eur=unit_price,
                total_eur=line_total,
            )
        )
    else:
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
        rate_unit=rate_unit,
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
    """Référence d'estimation — suffixe 48 bits (E-1 : 24 bits étaient énumérables).

    ``Quote.reference`` est ``String(24)`` : ``DEV-AAAA-`` (9) + 12 caractères = 21.
    """
    year = datetime.now(UTC).year
    return f"DEV-{year}-{secrets.token_hex(6).upper()}"


async def create_estimation_request(
    db: AsyncSession,
    *,
    pol_locode: str,
    pod_locode: str,
    leg: Leg | None = None,
    client_account: ClientAccount | None = None,
    commercial_client=None,
    contact_name: str | None = None,
    contact_email: str | None = None,
    contact_company: str | None = None,
    palettes_total: int,
    tonnage_t: Decimal | None = None,
    hazardous: bool = False,
    items: list[tuple[str, int]] | None = None,
    lang: str = "fr",
) -> Quote:
    """Demande d'estimation **non chiffrée**, déposée depuis la vitrine publique.

    Elle enregistre le besoin (route, volume, coordonnées) sans calculer de
    tarif : le demandeur n'a pas de grille négociée, et publier un prix avant
    qualification exposerait la politique tarifaire. Les montants restent à zéro
    et ``is_priced`` est faux — les écrans s'appuient dessus pour ne rien
    afficher plutôt que d'afficher « 0 € ».
    """
    from app.services.references import unique_reference

    quote = Quote(
        reference=await unique_reference(
            db, column=Quote.reference, factory=generate_quote_reference
        ),
        status="issued",
        origin="public_request",
        pol_locode=pol_locode.upper(),
        pod_locode=pod_locode.upper(),
        leg_id=leg.id if leg is not None else None,
        etd_snapshot=leg.etd if leg is not None else None,
        client_account_id=client_account.id if client_account is not None else None,
        commercial_client_id=(commercial_client.id if commercial_client is not None else None),
        contact_name=contact_name,
        contact_email=contact_email,
        contact_company=contact_company,
        palettes_total=palettes_total,
        tonnage_t=tonnage_t,
        hazardous=hazardous,
        currency="EUR",
        items_json=json.dumps([[f, c] for f, c in items]) if items else None,
        lang=lang,
    )
    db.add(quote)
    await db.flush()
    return quote


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
    from app.services.references import unique_reference

    quote = Quote(
        reference=await unique_reference(
            db, column=Quote.reference, factory=generate_quote_reference
        ),
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
