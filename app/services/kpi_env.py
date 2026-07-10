"""Dashboard Performance Environnementale — LOT 11 (socle + page 1 Vue flotte).

Calcul serveur exclusivement (jamais côté client — le frontend HTMX/Alpine
n'affiche que des fragments déjà calculés, cf. Spec §8 / UX §1).

Ce module implémente les formules de
``Specification_Dashboard_Performance_Environnementale.md`` §5 :

- **3 méthodes de calcul de l'EF (facteur d'émission), jamais mélangées**
  (paramètre ``method`` explicite partout — API ET affichage) :

  - **A « réel »** — ``EF = CO2 / Σ(cargo B/L × distance)``. Un voyage sur
    lest (cargo nul) est **exclu du dénominateur** (division par zéro
    évitée) et affiché **N/A** — le CO2 qu'il a émis reste compté au
    numérateur agrégé (le navire consomme même à vide).
  - **B « standardisé »** — ``EF = CO2 / Σ(capacité_ref × occupancy × distance)``
    avec ``vessel_capacity_ref_t`` / ``occupancy_rate_pct`` paramétrables
    (``dashboard_parameters``). Neutralise le remplissage réel : les voyages
    sur lest sont **inclus** (même hypothèse de remplissage que les autres).
  - **C « Cargo MRV réglementaire »** — nécessite ``cargo_mrv_t`` et la
    consommation hors mouillage, **absents du modèle legacy** : renvoie
    proprement N/A avec motif (l'UI l'affiche grisé). Sera branché au lot 9
    (grand livre + modèle événementiel ``nav_events``).

- **CO2 évité** vs 2 comparateurs sectoriels paramétrables (porte-conteneurs
  classique, avion cargo — ``dashboard_parameters``, bandeau « référence
  provisoire » tant que non sourcés, cf. spec §7 point 1 / Q15), calculé
  avec la **même assiette** (même méthode/dénominateur) que l'EF affiché —
  jamais une combinaison de deux méthodes différentes (spec §5.2).
- **Tendance 12 mois** (agrégat mensuel glissant, CO2 émis par mois
  d'arrivée) — mois sans arrivée = 0 (jamais omis, jamais masqué).
- **État de complétude** simple (nb legs avec/sans KPI calculé).

Unités : Decimal partout ; conversion t·nm → t·km via ``× 1,852``
(``app.services.co2.NM_TO_KM`` — cohérent avec le reste de l'application).

SOURCE DES ÉMISSIONS — ``_emissions_provider()`` (rebranchée lot 9)
-----------------------------------------------------------
Toute la donnée d'émission consommée par ce module transite par
``_emissions_provider()``, rebranché au **lot 9** sur le **grand livre**
(``voyage_emission_summaries``, matérialisation de ``services.emission_ledger``)
avec repli sur ``leg_kpis`` (legacy) pour les voyages sans résumé encore
calculé — **lecture pure, aucune écriture** (ce module ne réalimente ni
``LegKPI`` ni le summary, à la différence de ``/kpi`` ; même posture que
``services.kpi_consolidated.consolidated_kpis``). La forme de sortie est
inchangée (liste de ``LegEmissionRecord``) : le reste du module n'a pas bougé.
Le record porte désormais ``cargo_mrv_t`` → la **méthode C** devient réelle dès
qu'un résumé événementiel fournit le cargo MRV (N/A sinon, comme avant). Aucun
moteur de calcul carbone n'est créé ici.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bunker import BunkerOperation
from app.models.finance import LegKPI
from app.models.leg import Leg
from app.models.nav_event import NAV_TIME_SLOTS, NoonEvent
from app.models.port import Port
from app.models.validation import DashboardParameter, QualityCheckResult
from app.models.vessel import Vessel
from app.models.voyage_emission_summary import VoyageEmissionSummary
from app.services import emission_ledger, flgo_sync
from app.services import inter_event_compute as iec
from app.services.co2 import NM_TO_KM
from app.services.validation_engine import get_threshold

# ─────────────────────────────────────────────────────────── Constantes

EF_METHODS: tuple[str, ...] = ("A", "B", "C")

# Motifs N/A (jamais de valeur fabriquée — cf. UX §1 « Jamais de valeur
# fabriquée »). La méthode C est réelle dès qu'un résumé événementiel fournit le
# cargo MRV (lot 9) ; N/A sinon (voyages legacy / sans capture événementielle).
NA_CARGO_MRV = "cargo MRV indisponible (voyage sans capture événementielle)"
NA_BALLAST = "voyage sur lest (cargo nul) — non calculable en méthode réelle"
NA_NO_LADEN_VOYAGE = "aucun voyage chargé sur la période sélectionnée"
NA_NO_DISTANCE = "aucune distance enregistrée sur la période sélectionnée"
NA_ZERO_DISTANCE = "distance nulle pour ce voyage"

_EF_QUANT = Decimal("0.01")  # gCO2/t.km
_T_QUANT = Decimal("0.01")  # tonnes / nm
_PCT_QUANT = Decimal("0.1")  # %

# Paramètres consommés par ce module (dashboard_parameters, seedés au LOT 2 —
# cf. app.services.validation_engine.DASHBOARD_SEED). Défauts codés = repli
# fail-closed si la table est vide/inaccessible (même posture que
# services.co2.get_factors).
DASHBOARD_PARAM_DEFAULTS: dict[str, tuple[Decimal, str]] = {
    "occupancy_rate_pct": (Decimal("70"), "%"),
    "vessel_capacity_ref_t": (Decimal("1100"), "t"),
    "ef_container_ship_gco2_tkm": (Decimal("16"), "gCO2/t.km"),
    "ef_airfreight_gco2_tkm": (Decimal("800"), "gCO2/t.km"),
}

# Paramètres non sourcés formellement (spec §7 point 1 / Q15 du plan) — le
# bandeau « référence provisoire » s'affiche pour ceux-ci (page 1 jauge EF +
# page 4 administration). ``occupancy_rate_pct`` et ``vessel_capacity_ref_t``
# sont eux considérés confirmés (spec §7 points 3-4).
PROVISIONAL_DASHBOARD_PARAMS: frozenset[str] = frozenset(
    {"ef_container_ship_gco2_tkm", "ef_airfreight_gco2_tkm"}
)

_REFERENCE_LABELS: dict[str, str] = {
    "ef_container_ship_gco2_tkm": "container_ship",
    "ef_airfreight_gco2_tkm": "airfreight",
}


def _check_method(method: str) -> None:
    if method not in EF_METHODS:
        raise ValueError(f"méthode EF inconnue : {method!r} (attendu {EF_METHODS})")


# ───────────────────────────────────────────────────────────── Dataclasses


@dataclass(frozen=True)
class LegEmissionRecord:
    """Émissions/cargo/distance d'un leg — forme de sortie de ``_emissions_provider``.

    Forme rebranchée au lot 9 sur le grand livre (``voyage_emission_summaries``,
    repli ``LegKPI``) : tant qu'une fonction renvoie une liste de
    ``LegEmissionRecord``, rien d'autre dans ce module ne change. ``cargo_mrv_t``
    (ajout lot 9, ``None`` par défaut) alimente la **méthode C** (réelle dès
    qu'un résumé événementiel le fournit).
    """

    leg_id: int
    leg_code: str
    vessel_id: int
    co2_emitted_t: Decimal
    cargo_t: Decimal  # tonnage B/L réel (0 = voyage sur lest / sans cargo confirmé)
    distance_nm: Decimal
    etd: datetime | None
    ata: datetime | None
    has_kpi: bool  # émission connue (résumé grand livre ou LegKPI co2 renseigné)
    cargo_mrv_t: Decimal | None = None  # cargo MRV (méthode C) — None si indispo


@dataclass(frozen=True)
class EfResult:
    """Facteur d'émission (gCO2/t.km) d'une méthode donnée — jamais mélangées."""

    method: str
    value_gco2_tkm: Decimal | None
    na_reason: str | None


@dataclass(frozen=True)
class AvoidedResult:
    """CO2 évité vs une référence sectorielle, même assiette que l'EF associé."""

    reference: str  # "container_ship" | "airfreight"
    avoided_t: Decimal | None
    avoided_pct: Decimal | None
    ef_reference_gco2_tkm: Decimal
    na_reason: str | None


@dataclass(frozen=True)
class CompletenessBlock:
    legs_total: int
    legs_with_data: int
    legs_without_data: int


@dataclass(frozen=True)
class VesselKpiBlock:
    """Bloc KPI — soit la flotte entière (``vessel_id is None``), soit un navire."""

    vessel_id: int | None
    vessel_code: str | None
    label: str
    leg_count: int
    laden_leg_count: int
    ballast_leg_count: int
    co2_emitted_t: Decimal
    distance_nm: Decimal
    ef: EfResult
    avoided_container: AvoidedResult
    avoided_airfreight: AvoidedResult
    completeness: CompletenessBlock


@dataclass(frozen=True)
class TrendPoint:
    year: int
    month: int
    label: str  # "MM/AAAA" — neutre, pas de dépendance à une locale de mois
    co2_emitted_t: Decimal


@dataclass(frozen=True)
class DashboardParamValue:
    parameter_name: str
    value: Decimal
    unit: str | None
    source: str  # "vessel" | "global" | "coded_default"


@dataclass(frozen=True)
class FleetSummary:
    period: int  # année (Leg.etd.year) — granularité mois/trimestre = backlog
    method: str
    generated_at: datetime
    fleet: VesselKpiBlock
    vessels: list[VesselKpiBlock]
    selected: VesselKpiBlock  # = fleet si vessel_id est None, sinon le bloc navire
    trend: list[TrendPoint]
    trend_max_t: Decimal
    params: dict[str, DashboardParamValue] = field(default_factory=dict)


# ──────────────────────────────────────────────── Paramètres (dashboard_parameters)


async def get_dashboard_parameters(
    db: AsyncSession, *, vessel_id: int | None = None
) -> dict[str, DashboardParamValue]:
    """Résout les paramètres dashboard : override navire → global → défaut codé.

    *Fail-closed* comme ``services.co2.get_factors`` / ``validation_engine.get_threshold``
    : toute erreur DB retombe sur ``DASHBOARD_PARAM_DEFAULTS``. Pas de cache
    (contrairement au moteur de règles) — cette table ne porte que 4 à
    quelques lignes, lue une fois par vue dashboard ; la fraîcheur immédiate
    après une édition en page Administration (LOT 11 page 4) prime sur un
    gain de perf marginal.
    """
    rows_by_key: dict[tuple[str, int | None], DashboardParameter] = {}
    try:
        rows = (await db.execute(select(DashboardParameter))).scalars().all()
        for row in rows:
            rows_by_key[(row.parameter_name, row.vessel_id)] = row
    except Exception:
        rows_by_key = {}

    resolved: dict[str, DashboardParamValue] = {}
    for name, (default_value, default_unit) in DASHBOARD_PARAM_DEFAULTS.items():
        row = rows_by_key.get((name, vessel_id)) if vessel_id is not None else None
        source = "vessel"
        if row is None:
            row = rows_by_key.get((name, None))
            source = "global"
        if row is not None:
            resolved[name] = DashboardParamValue(
                parameter_name=name,
                value=Decimal(row.value),
                unit=row.unit or default_unit,
                source=source,
            )
        else:
            resolved[name] = DashboardParamValue(
                parameter_name=name, value=default_value, unit=default_unit, source="coded_default"
            )
    return resolved


# ─────────────────────────── Source des émissions (grand livre, lot 9)


async def _emissions_provider(
    db: AsyncSession, *, vessel_id: int | None = None
) -> list[LegEmissionRecord]:
    """Grand livre → ``LegEmissionRecord`` (aucun recalcul, aucune écriture).

    Rebranché au lot 9 : lit d'abord ``voyage_emission_summaries`` (cache du
    grand livre ``services.emission_ledger`` — CO₂ TtW, cargo B/L, distance,
    cargo MRV), avec **repli sur ``LegKPI``** (legacy) pour les voyages sans
    résumé encore matérialisé. Le repli reproduit à l'identique l'ancien
    comportement (co2/tonnage LegKPI, distance leg) : un voyage sans résumé
    donne exactement le même ``LegEmissionRecord`` qu'avant la bascule.

    Ne déclenche jamais ``services.kpi.compute_for_leg`` ni ``refresh_summary``
    (contrairement à ``GET /kpi``) : lecture pure, comme ``kpi_consolidated``.
    Fail-closed : si la table des résumés est inaccessible, on retombe
    intégralement sur ``LegKPI``.
    """
    stmt = select(Leg)
    if vessel_id is not None:
        stmt = stmt.where(Leg.vessel_id == vessel_id)
    legs = list((await db.execute(stmt)).scalars().all())
    kpi_by_leg = {k.leg_id: k for k in (await db.execute(select(LegKPI))).scalars().all()}
    try:
        summary_by_leg = {
            s.leg_id: s for s in (await db.execute(select(VoyageEmissionSummary))).scalars().all()
        }
    except Exception:
        summary_by_leg = {}

    records: list[LegEmissionRecord] = []
    for leg in legs:
        summary = summary_by_leg.get(leg.id)
        if summary is not None:
            # Source de vérité env. : le résumé du grand livre.
            has_kpi = summary.co2_t is not None
            co2_t = summary.co2_t if summary.co2_t is not None else Decimal(0)
            cargo_t = summary.cargo_bl_t if summary.cargo_bl_t is not None else Decimal(0)
            distance_src = (
                summary.distance_nm if summary.distance_nm is not None else leg.distance_nm
            )
            cargo_mrv_t = summary.cargo_mrv_t
        else:
            # Repli legacy — identique à l'avant-lot-9 (aucune régression).
            k = kpi_by_leg.get(leg.id)
            has_kpi = k is not None and k.co2_emitted_kg is not None
            co2_t = (
                (k.co2_emitted_kg / Decimal(1000))
                if (k and k.co2_emitted_kg is not None)
                else Decimal(0)
            )
            cargo_t = (
                (k.tonnage_kg / Decimal(1000)) if (k and k.tonnage_kg is not None) else Decimal(0)
            )
            distance_src = leg.distance_nm
            cargo_mrv_t = None
        distance_nm = distance_src if distance_src is not None else Decimal(0)
        records.append(
            LegEmissionRecord(
                leg_id=leg.id,
                leg_code=leg.leg_code,
                vessel_id=leg.vessel_id,
                co2_emitted_t=co2_t,
                cargo_t=cargo_t,
                distance_nm=Decimal(distance_nm),
                etd=leg.etd,
                ata=leg.ata,
                has_kpi=has_kpi,
                cargo_mrv_t=cargo_mrv_t,
            )
        )
    return records


# ────────────────────────────────────────────────── Formules EF (spec §5.1)


def leg_ef(
    record: LegEmissionRecord,
    *,
    method: str,
    occupancy_pct: Decimal,
    capacity_ref_t: Decimal,
) -> EfResult:
    """EF (gCO2/t.km) d'UN voyage — méthode A/B/C, jamais mélangées (spec §5.1).

    Renvoie ``value_gco2_tkm=None`` + ``na_reason`` quand non calculable :
    méthode C sans ``cargo_mrv_t`` (voyage legacy/sans capture événementielle
    — **réelle** sinon, depuis le grand livre, lot 9), méthode A ou C sur un
    voyage sur lest (cargo nul — pas de valeur fabriquée), ou distance nulle.
    """
    _check_method(method)
    if method == "C" and record.cargo_mrv_t is None:
        return EfResult(method="C", value_gco2_tkm=None, na_reason=NA_CARGO_MRV)
    if record.distance_nm <= 0:
        return EfResult(method=method, value_gco2_tkm=None, na_reason=NA_ZERO_DISTANCE)

    distance_km = record.distance_nm * NM_TO_KM
    if method == "A":
        if record.cargo_t <= 0:
            return EfResult(method="A", value_gco2_tkm=None, na_reason=NA_BALLAST)
        denom = record.cargo_t * distance_km
    elif method == "C":  # cargo MRV réglementaire (grand livre — lot 9)
        if record.cargo_mrv_t <= 0:
            return EfResult(method="C", value_gco2_tkm=None, na_reason=NA_BALLAST)
        denom = record.cargo_mrv_t * distance_km
    else:  # "B" — standardisé, ballast inclus (hypothèse de remplissage fixe)
        denom = capacity_ref_t * (occupancy_pct / Decimal(100)) * distance_km

    if denom <= 0:
        return EfResult(method=method, value_gco2_tkm=None, na_reason=NA_ZERO_DISTANCE)
    value = (record.co2_emitted_t * Decimal(1_000_000) / denom).quantize(_EF_QUANT)
    return EfResult(method=method, value_gco2_tkm=value, na_reason=None)


def aggregate_ef(
    records: list[LegEmissionRecord],
    *,
    method: str,
    occupancy_pct: Decimal,
    capacity_ref_t: Decimal,
) -> tuple[EfResult, Decimal]:
    """EF agrégé (flotte / navire / période) + assiette (t.km) réutilisée pour le CO2 évité.

    Numérateur = CO2 total, **tous voyages compris** (un voyage sur lest
    émet quand même du CO2). Dénominateur, selon la méthode :

    - **A** — ``Σ(cargo_t × distance_km)`` sur les voyages **chargés
      uniquement** (cargo > 0) : un voyage sur lest est exclu du
      dénominateur (spec §5.1 — traitement identique à celui documenté pour
      la méthode C, appliqué ici à la méthode A par cohérence).
    - **B** — ``Σ(capacité_ref × occupancy × distance_km)`` sur **tous** les
      voyages (la méthode standardise le taux de remplissage : elle ne
      dépend pas du chargement réel, donc les voyages sur lest sont inclus
      au même titre que les autres).
    - **C** — ``Σ(cargo_mrv × distance_km)`` sur les voyages disposant d'un
      cargo MRV **chargé** (> 0) — réelle depuis le grand livre (lot 9) ;
      N/A motivé si aucun voyage de la période ne porte de cargo MRV
      (legacy/sans capture événementielle) ; les voyages sur lest
      (cargo MRV = 0) restent au numérateur, exclus du dénominateur.
    """
    _check_method(method)
    total_co2_t = sum((r.co2_emitted_t for r in records), Decimal(0))

    if method == "C" and not any(r.cargo_mrv_t is not None for r in records):
        return EfResult(method="C", value_gco2_tkm=None, na_reason=NA_CARGO_MRV), Decimal(0)

    if method == "A":
        denom = sum(
            (
                r.cargo_t * r.distance_nm * NM_TO_KM
                for r in records
                if r.cargo_t > 0 and r.distance_nm > 0
            ),
            Decimal(0),
        )
        na_reason = NA_NO_LADEN_VOYAGE
    elif method == "C":
        denom = sum(
            (
                r.cargo_mrv_t * r.distance_nm * NM_TO_KM
                for r in records
                if r.cargo_mrv_t is not None and r.cargo_mrv_t > 0 and r.distance_nm > 0
            ),
            Decimal(0),
        )
        na_reason = NA_NO_LADEN_VOYAGE
    else:  # "B"
        occ = occupancy_pct / Decimal(100)
        denom = sum(
            (capacity_ref_t * occ * r.distance_nm * NM_TO_KM for r in records if r.distance_nm > 0),
            Decimal(0),
        )
        na_reason = NA_NO_DISTANCE

    if denom <= 0:
        return EfResult(method=method, value_gco2_tkm=None, na_reason=na_reason), Decimal(0)
    value = (total_co2_t * Decimal(1_000_000) / denom).quantize(_EF_QUANT)
    return EfResult(method=method, value_gco2_tkm=value, na_reason=None), denom


def avoided_emissions(
    ef: EfResult,
    denom_tkm: Decimal,
    *,
    ef_reference_gco2_tkm: Decimal,
    reference: str,
) -> AvoidedResult:
    """CO2 évité vs une référence sectorielle (spec §5.2) — même assiette que ``ef``.

    ``denom_tkm`` doit provenir du même appel (méthode identique) que ``ef``
    — c'est la garde structurelle contre le mélange de méthodes : on ne
    recalcule jamais une assiette séparément pour le CO2 évité.
    """
    if ef.value_gco2_tkm is None:
        return AvoidedResult(
            reference=reference,
            avoided_t=None,
            avoided_pct=None,
            ef_reference_gco2_tkm=ef_reference_gco2_tkm,
            na_reason=ef.na_reason,
        )
    delta = ef_reference_gco2_tkm - ef.value_gco2_tkm
    avoided_t = (delta * denom_tkm / Decimal(1_000_000)).quantize(_T_QUANT)
    avoided_pct = (
        (delta / ef_reference_gco2_tkm * Decimal(100)).quantize(_PCT_QUANT)
        if ef_reference_gco2_tkm
        else None
    )
    return AvoidedResult(
        reference=reference,
        avoided_t=avoided_t,
        avoided_pct=avoided_pct,
        ef_reference_gco2_tkm=ef_reference_gco2_tkm,
        na_reason=None,
    )


# ──────────────────────────────────────────────────────── Tendance 12 mois


def monthly_trend(records: list[LegEmissionRecord], *, now: datetime) -> list[TrendPoint]:
    """CO2 émis agrégé par mois d'arrivée (``ata``), 12 mois glissants finissant à ``now``.

    Un mois sans arrivée vaut 0 (jamais omis de la série — cf. UX §1
    « jamais de valeur fabriquée » : 0 est la vraie valeur, pas un défaut
    masquant une absence de donnée).
    """
    months: list[tuple[int, int]] = []
    y, m = now.year, now.month
    for _ in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()

    totals: dict[tuple[int, int], Decimal] = dict.fromkeys(months, Decimal(0))
    for r in records:
        if r.ata is None:
            continue
        key = (r.ata.year, r.ata.month)
        if key in totals:
            totals[key] = totals[key] + r.co2_emitted_t

    return [
        TrendPoint(
            year=y, month=m, label=f"{m:02d}/{y}", co2_emitted_t=totals[(y, m)].quantize(_T_QUANT)
        )
        for (y, m) in months
    ]


# ──────────────────────────────────────────────────────────── Vue flotte


async def fleet_summary(
    db: AsyncSession,
    *,
    period: int,
    method: str,
    vessel_id: int | None = None,
    now: datetime | None = None,
) -> FleetSummary:
    """Page 1 — Vue flotte : bandeau KPI + cartes par navire + tendance + complétude.

    ``period`` : année (filtre sur ``Leg.etd.year`` — granularité mois/
    trimestre proposée par la spec §2 reste un raffinement backlog, hors
    périmètre LOT 11). ``method`` : ``"A"``/``"B"``/``"C"`` (§5.1), jamais
    mélangées. ``vessel_id`` : None = bandeau flotte ; sinon bandeau centré
    sur ce navire (les cartes par navire restent toutes affichées).
    """
    _check_method(method)
    now = now or datetime.now(UTC)

    all_records = await _emissions_provider(db)
    params = await get_dashboard_parameters(db)
    occupancy_pct = params["occupancy_rate_pct"].value
    capacity_ref_t = params["vessel_capacity_ref_t"].value
    ef_container = params["ef_container_ship_gco2_tkm"].value
    ef_airfreight = params["ef_airfreight_gco2_tkm"].value

    vessels = list(
        (await db.execute(select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.name)))
        .scalars()
        .all()
    )

    def _block(
        scoped_records: list[LegEmissionRecord], *, label: str, vid: int | None, code: str | None
    ) -> VesselKpiBlock:
        year_records = [r for r in scoped_records if r.etd is not None and r.etd.year == period]
        ef, denom = aggregate_ef(
            year_records, method=method, occupancy_pct=occupancy_pct, capacity_ref_t=capacity_ref_t
        )
        avoided_container = avoided_emissions(
            ef, denom, ef_reference_gco2_tkm=ef_container, reference="container_ship"
        )
        avoided_airfreight = avoided_emissions(
            ef, denom, ef_reference_gco2_tkm=ef_airfreight, reference="airfreight"
        )
        co2_t = sum((r.co2_emitted_t for r in year_records), Decimal(0))
        distance = sum((r.distance_nm for r in year_records), Decimal(0))
        laden = sum(1 for r in year_records if r.cargo_t > 0)
        legs_with_data = sum(1 for r in year_records if r.has_kpi)
        completeness = CompletenessBlock(
            legs_total=len(year_records),
            legs_with_data=legs_with_data,
            legs_without_data=len(year_records) - legs_with_data,
        )
        return VesselKpiBlock(
            vessel_id=vid,
            vessel_code=code,
            label=label,
            leg_count=len(year_records),
            laden_leg_count=laden,
            ballast_leg_count=len(year_records) - laden,
            co2_emitted_t=co2_t.quantize(_T_QUANT),
            distance_nm=distance.quantize(_T_QUANT),
            ef=ef,
            avoided_container=avoided_container,
            avoided_airfreight=avoided_airfreight,
            completeness=completeness,
        )

    fleet_block = _block(all_records, label="Flotte", vid=None, code=None)
    vessel_blocks = [
        _block([r for r in all_records if r.vessel_id == v.id], label=v.name, vid=v.id, code=v.code)
        for v in vessels
    ]
    selected = fleet_block
    if vessel_id is not None:
        selected = next((b for b in vessel_blocks if b.vessel_id == vessel_id), fleet_block)

    trend_source = (
        all_records if vessel_id is None else [r for r in all_records if r.vessel_id == vessel_id]
    )
    trend = monthly_trend(trend_source, now=now)
    trend_max_t = max((p.co2_emitted_t for p in trend), default=Decimal(0))
    if trend_max_t <= 0:
        trend_max_t = Decimal(1)  # évite une division par zéro à l'affichage (barres à 0%)

    return FleetSummary(
        period=period,
        method=method,
        generated_at=now,
        fleet=fleet_block,
        vessels=vessel_blocks,
        selected=selected,
        trend=trend,
        trend_max_t=trend_max_t,
        params=params,
    )


# ═══════════════════════════════════════════════════════════════════════════
# LOT 12 — Page 2 (Suivi opérationnel) + Page 3 (Qualité des données)
# ═══════════════════════════════════════════════════════════════════════════
#
# Même posture que le LOT 11 : calcul serveur exclusivement (Decimal), lecture
# pure (aucune écriture), et réutilisation du grand livre (``emission_ledger``)
# + des calculs inter-événements (``inter_event_compute``) déjà en place — pas
# de nouveau moteur (la règle d'or du plan §2.4 tient : la seule multiplication
# conso × facteur vit dans ``emission_ledger.emissions_breakdown``).

# Catégories de propulsion (spec §5.4) — ordre d'affichage fixe.
PROPULSION_CATEGORIES: tuple[str, ...] = ("velique_pur", "hybride", "mecanique", "statique")

# Couleurs charte « Nouvelle Étoile » par catégorie (vert = vélique / cuivre =
# hybride transition / teal = mécanique / gris = statique isolé).
PROPULSION_COLORS: dict[str, str] = {
    "velique_pur": "#87BD29",
    "hybride": "#B47148",
    "mecanique": "#0D5966",
    "statique": "#9AA0A6",
}
_PROPULSION_LABEL_KEYS: dict[str, str] = {
    "velique_pur": "dashenv_prop_velique",
    "hybride": "dashenv_prop_hybride",
    "mecanique": "dashenv_prop_mecanique",
    "statique": "dashenv_prop_statique",
}

# Cible de consommation journalière (jauge) — seuil paramétrable R08/R11
# (``seuil_conso_ref_l_j``), fail-closed sur 750 L/j (plan §6 / spec §3).
SEUIL_CONSO_REF_DEFAULT_L_J: Decimal = Decimal("750")
_SEUIL_CONSO_RULE_ID = "R08"
_SEUIL_CONSO_PARAM = "seuil_conso_ref_l_j"

# Règles de qualité mises en avant sur le détail d'un voyage (page 2).
VOYAGE_QUALITY_RULES: tuple[str, ...] = ("R14", "R22")

_L_QUANT = Decimal("1")  # L/j — entier (cohérent avec l'affichage maquette)

NA_NO_CONSO = "consommation indisponible (voyage sans capture ni durée exploitable)"
NA_NO_SLOTS = "aucun relevé de voilure exploitable sur ce voyage"


# ─────────────────────────────────────────── Profil de propulsion (spec §5.4)


def _positive(value: Decimal | None) -> bool:
    return value is not None and value > 0


def classify_propulsion_slot(reading) -> str:
    """Catégorie de propulsion d'UNE tranche de 4 h (spec §5.4).

    - ``voile_on = j0 ∨ fwd_j1 ∨ fwd_ms ∨ aft_j1 ∨ aft_ms`` (``j0`` = petit foc,
      voile à part entière — compte comme les 4 autres, cf. spec §5.4) ;
    - ``moteur_on = me_ps_load_pct > 0 ∨ me_sb_load_pct > 0``.

    → vélique pur (voile ∧ ¬moteur) / hybride (voile ∧ moteur) / mécanique
    (¬voile ∧ moteur) / statique (ni l'un ni l'autre — isolé, jamais confondu
    avec « mécanique »). ``reading`` est duck-typé (attributs du modèle
    ``NavEventSailReading``) — testable sans ORM.
    """
    voile_on = bool(
        reading.j0 or reading.fwd_j1 or reading.fwd_ms or reading.aft_j1 or reading.aft_ms
    )
    moteur_on = _positive(reading.me_ps_load_pct) or _positive(reading.me_sb_load_pct)
    if voile_on and not moteur_on:
        return "velique_pur"
    if voile_on and moteur_on:
        return "hybride"
    if moteur_on:  # ¬voile ∧ moteur
        return "mecanique"
    return "statique"


@dataclass(frozen=True)
class PropulsionSegment:
    """Une catégorie du profil : compteur + % (sur les tranches exploitables)."""

    category: str
    label_key: str
    count: int
    pct: Decimal | None
    color: str


@dataclass(frozen=True)
class PropulsionProfile:
    """Profil de propulsion d'un voyage (spec §5.4).

    ``filled_slots`` = tranches AVEC relevé exploitable (dénominateur des %) ;
    ``theoretical_slots`` = tranches théoriques du voyage (noons × 6 créneaux) ;
    ``completeness_pct`` = ``filled / theoretical`` (affiché en filigrane). Les
    tranches sans relevé sont EXCLUES du dénominateur — jamais classées
    « statique » par défaut (spec stricte).
    """

    counts: dict[str, int]
    filled_slots: int
    theoretical_slots: int
    completeness_pct: Decimal | None
    segments: list[PropulsionSegment]
    na_reason: str | None


def build_propulsion_profile(readings, *, theoretical_slots: int) -> PropulsionProfile:
    """Agrège une liste de relevés de voilure (tranches 4 h) en profil.

    Pur (aucun accès DB) : ``readings`` est un itérable de relevés duck-typés,
    ``theoretical_slots`` le nombre de tranches théoriques du voyage. Le
    dénominateur des pourcentages est le nombre de tranches RENSEIGNÉES
    (``filled_slots``), pas le théorique — un trou de saisie n'écrase donc
    jamais le % de vélique (spec §5.4).
    """
    counts = dict.fromkeys(PROPULSION_CATEGORIES, 0)
    for r in readings:
        counts[classify_propulsion_slot(r)] += 1
    filled = sum(counts.values())

    segments: list[PropulsionSegment] = []
    for cat in PROPULSION_CATEGORIES:
        pct = (
            (Decimal(counts[cat]) * Decimal(100) / Decimal(filled)).quantize(_PCT_QUANT)
            if filled
            else None
        )
        segments.append(
            PropulsionSegment(
                category=cat,
                label_key=_PROPULSION_LABEL_KEYS[cat],
                count=counts[cat],
                pct=pct,
                color=PROPULSION_COLORS[cat],
            )
        )
    completeness = (
        (Decimal(filled) * Decimal(100) / Decimal(theoretical_slots)).quantize(_PCT_QUANT)
        if theoretical_slots
        else None
    )
    return PropulsionProfile(
        counts=counts,
        filled_slots=filled,
        theoretical_slots=theoretical_slots,
        completeness_pct=completeness,
        segments=segments,
        na_reason=None if filled else NA_NO_SLOTS,
    )


def _dominant_category(readings) -> str | None:
    """Catégorie de propulsion majoritaire d'un jeu de relevés (None si vide)."""
    readings = list(readings)
    if not readings:
        return None
    counts = dict.fromkeys(PROPULSION_CATEGORIES, 0)
    for r in readings:
        counts[classify_propulsion_slot(r)] += 1
    # Ordre fixe des catégories = départage déterministe des ex æquo.
    return max(PROPULSION_CATEGORIES, key=lambda c: counts[c])


async def _finalized_noons(db: AsyncSession, leg_id: int) -> list[NoonEvent]:
    """NoonEvent finalisés/validés d'un leg (relevés voilure en ``selectin``)."""
    rows = await db.execute(
        select(NoonEvent).where(
            NoonEvent.leg_id == leg_id,
            NoonEvent.status.in_(iec.FINALIZED_STATUSES),
        )
    )
    return list(rows.scalars().all())


async def propulsion_profile(db: AsyncSession, leg_id: int) -> PropulsionProfile:
    """Profil de propulsion d'un voyage depuis ``nav_event_sail_readings``.

    Théorique = ``nb noons × 6 créneaux`` (une journée en mer = 6 tranches de
    4 h) ; renseigné = relevés effectivement présents. Un voyage sans capture
    événementielle (legacy) → profil vide + ``na_reason``.
    """
    noons = await _finalized_noons(db, leg_id)
    readings = [r for n in noons for r in n.sail_readings]
    theoretical = len(noons) * len(NAV_TIME_SLOTS)
    return build_propulsion_profile(readings, theoretical_slots=theoretical)


# ─────────────────────────────────────────── Consommation vs cible (spec §3)


@dataclass(frozen=True)
class ConsoTarget:
    """Consommation journalière (L/j) vs cible paramétrable — verdict d'affichage."""

    daily_l_j: Decimal | None
    target_l_j: Decimal
    over_target: bool
    delta_pct: Decimal | None
    na_reason: str | None


def conso_vs_target(daily_l_j: Decimal | None, target_l_j: Decimal) -> ConsoTarget:
    """Compare la conso journalière à la cible (jauge). Pur, testable.

    Le verdict (``over_target``) dépend du seuil paramétré : à conso égale,
    déplacer la cible (750 → 800) peut changer l'affichage (spec §3, jauge
    paramétrée)."""
    if daily_l_j is None:
        return ConsoTarget(
            daily_l_j=None,
            target_l_j=target_l_j,
            over_target=False,
            delta_pct=None,
            na_reason=NA_NO_CONSO,
        )
    over = daily_l_j > target_l_j
    delta_pct = (
        (abs(daily_l_j - target_l_j) / target_l_j * Decimal(100)).quantize(_PCT_QUANT)
        if target_l_j > 0
        else None
    )
    return ConsoTarget(
        daily_l_j=daily_l_j.quantize(_L_QUANT),
        target_l_j=target_l_j,
        over_target=over,
        delta_pct=delta_pct,
        na_reason=None,
    )


def _duration_days(duration_h: Decimal | None, leg: Leg) -> Decimal | None:
    """Durée du voyage en jours — depuis les intervalles calculés, sinon le leg."""
    if duration_h is not None and duration_h > 0:
        return duration_h / Decimal(24)
    start = leg.atd or leg.etd
    end = leg.ata or leg.eta
    if start is not None and end is not None:
        secs = Decimal(str((end - start).total_seconds()))
        if secs > 0:
            return secs / Decimal(86400)
    return None


def _daily_conso_l_j(
    conso_total_t: Decimal | None, density_t_m3: Decimal, duration_days: Decimal | None
) -> Decimal | None:
    """Conso totale (t) → L/j : ``t / densité × 1000 / jours`` (densité t/m³ ≡ kg/L)."""
    if conso_total_t is None or duration_days is None or duration_days <= 0 or density_t_m3 <= 0:
        return None
    volume_l = conso_total_t / density_t_m3 * Decimal(1000)
    return volume_l / duration_days


# ─────────────────────────────────────────── Page 2 — détail d'un voyage


@dataclass(frozen=True)
class BunkerMarker:
    """Soutage superposé à la timeline ROB (marqueur)."""

    bunker_id: int
    bdn_number: str
    delivery_datetime_utc: datetime
    mass_t: Decimal
    port_locode: str


@dataclass(frozen=True)
class EventChainItem:
    """Un maillon de la chaîne d'événements (statut + position + ROB)."""

    event_id: int
    event_type: str
    datetime_utc: datetime | None
    status: str
    lat: Decimal | None
    lon: Decimal | None
    rob_declared_t: Decimal | None
    rob_calculated_t: Decimal | None
    dominant_category: str | None


@dataclass(frozen=True)
class VoyageDetail:
    """Vue complète d'un voyage pour la page 2 (drill-down)."""

    leg_id: int
    leg_code: str
    vessel_id: int | None
    vessel_name: str | None
    vessel_code: str | None
    dep_port: Port | None
    arr_port: Port | None
    source: str  # events | legacy_noon
    ledger: emission_ledger.LedgerResult
    me_pct: Decimal | None
    ae_pct: Decimal | None
    conso: ConsoTarget
    duration_days: Decimal | None
    rob_chain: list[iec.RobPoint]
    bunkers: list[BunkerMarker]
    events: list[EventChainItem]
    propulsion: PropulsionProfile
    quality: list[QualityCheckResult]
    map_points: list[dict]
    map_segments: list[dict]


def _event_dominant_category(event) -> str | None:
    if isinstance(event, NoonEvent) and event.sail_readings:
        return _dominant_category(event.sail_readings)
    return None


async def voyage_detail(
    db: AsyncSession, leg_id: int, *, conso_target_l_j: Decimal | None = None
) -> VoyageDetail | None:
    """Détail d'un voyage : chaîne d'événements, ROB timeline, conso vs cible,
    répartition ME/AE, écarts R14/R22, profil de propulsion, carte colorée.

    Réutilise ``emission_ledger.compute_for_leg`` (KPI/EF/conso, source events
    ou legacy) et ``inter_event_compute.compute_leg`` (chaîne + ROB chaîné) —
    aucun recalcul. ``conso_target_l_j`` : override de la cible (défaut =
    seuil ``seuil_conso_ref_l_j`` résolu, fail-closed 750)."""
    leg = await db.get(Leg, leg_id)
    if leg is None:
        return None

    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id is not None else None
    dep_port = await db.get(Port, leg.departure_port_id) if leg.departure_port_id else None
    arr_port = await db.get(Port, leg.arrival_port_id) if leg.arrival_port_id else None

    ledger = await emission_ledger.compute_for_leg(db, leg)

    bunker_lookup = await emission_ledger.build_bunker_lookup(db, leg.id)
    comp = await iec.compute_leg(db, leg, bunkered_t_lookup=bunker_lookup)
    rob_by_event = {p.event_id: p for p in comp.rob_chain}

    # Répartition ME / AE (depuis le grand livre — None si legacy sans split).
    me, ae = ledger.conso_me_t, ledger.conso_ae_t
    me_pct = ae_pct = None
    if me is not None and ae is not None and (me + ae) > 0:
        total = me + ae
        me_pct = (me / total * Decimal(100)).quantize(_PCT_QUANT)
        ae_pct = (ae / total * Decimal(100)).quantize(_PCT_QUANT)

    # Conso journalière vs cible paramétrable.
    duration_h = comp.totals.duration_h if comp.totals is not None else None
    duration_days = _duration_days(duration_h, leg)
    density = await iec.resolve_density(db, leg.vessel_id)
    daily = _daily_conso_l_j(ledger.conso_total_t, density, duration_days)
    if conso_target_l_j is None:
        tv = await get_threshold(db, _SEUIL_CONSO_RULE_ID, _SEUIL_CONSO_PARAM, leg.vessel_id)
        conso_target_l_j = tv.value if tv is not None else SEUIL_CONSO_REF_DEFAULT_L_J
    conso = conso_vs_target(daily, conso_target_l_j)

    # Chaîne d'événements + carte colorée par propulsion.
    events: list[EventChainItem] = []
    map_points: list[dict] = []
    for ev in comp.events:
        rp = rob_by_event.get(ev.id)
        dominant = _event_dominant_category(ev)
        events.append(
            EventChainItem(
                event_id=ev.id,
                event_type=ev.event_type,
                datetime_utc=ev.datetime_utc,
                status=ev.status,
                lat=ev.lat_decimal,
                lon=ev.lon_decimal,
                rob_declared_t=(rp.rob_declared_t if rp is not None else None),
                rob_calculated_t=(rp.rob_calculated_t if rp is not None else None),
                dominant_category=dominant,
            )
        )
        if ev.lat_decimal is not None and ev.lon_decimal is not None:
            map_points.append(
                {
                    "lat": float(ev.lat_decimal),
                    "lon": float(ev.lon_decimal),
                    "type": ev.event_type,
                    "label": ev.event_type,
                }
            )

    # Segments carte : entre 2 positions consécutives, catégorie dominante de
    # la tranche 4 h de l'événement d'arrivée (sinon celle du départ), spec §5.4.
    positioned = [e for e in comp.events if e.lat_decimal is not None and e.lon_decimal is not None]
    map_segments: list[dict] = []
    for a, b in itertools.pairwise(positioned):
        cat = _event_dominant_category(b) or _event_dominant_category(a)
        map_segments.append(
            {
                "from": [float(a.lon_decimal), float(a.lat_decimal)],
                "to": [float(b.lon_decimal), float(b.lat_decimal)],
                "category": cat,
                "color": PROPULSION_COLORS.get(cat, "#9AA0A6"),
            }
        )

    # Soutages (marqueurs ROB timeline).
    bunker_rows = list(
        (
            await db.execute(
                select(BunkerOperation)
                .where(BunkerOperation.leg_id == leg.id)
                .order_by(BunkerOperation.delivery_datetime_utc)
            )
        )
        .scalars()
        .all()
    )
    bunkers = [
        BunkerMarker(
            bunker_id=b.id,
            bdn_number=b.bdn_number,
            delivery_datetime_utc=b.delivery_datetime_utc,
            mass_t=b.mass_t,
            port_locode=b.port_locode,
        )
        for b in bunker_rows
    ]

    # Écarts R14/R22 du voyage (journal qualité — jamais recalculés ici).
    quality = list(
        (
            await db.execute(
                select(QualityCheckResult)
                .where(
                    QualityCheckResult.leg_id == leg.id,
                    QualityCheckResult.rule_id.in_(VOYAGE_QUALITY_RULES),
                    QualityCheckResult.result == "fail",
                )
                .order_by(QualityCheckResult.executed_at.desc())
            )
        )
        .scalars()
        .all()
    )

    propulsion = await propulsion_profile(db, leg.id)

    return VoyageDetail(
        leg_id=leg.id,
        leg_code=leg.leg_code,
        vessel_id=leg.vessel_id,
        vessel_name=(vessel.name if vessel is not None else None),
        vessel_code=(vessel.code if vessel is not None else None),
        dep_port=dep_port,
        arr_port=arr_port,
        source=ledger.source,
        ledger=ledger,
        me_pct=me_pct,
        ae_pct=ae_pct,
        conso=conso,
        duration_days=duration_days,
        rob_chain=comp.rob_chain,
        bunkers=bunkers,
        events=events,
        propulsion=propulsion,
        quality=quality,
        map_points=map_points,
        map_segments=map_segments,
    )


# ─────────────────────────────────────────── Page 2 — liste voyages / navire


@dataclass(frozen=True)
class VoyageRow:
    """Ligne de la liste des voyages d'un navire (KPI issus du grand livre)."""

    leg_id: int
    leg_code: str
    dep_locode: str | None
    arr_locode: str | None
    dep_country: str | None
    arr_country: str | None
    etd: datetime | None
    ata: datetime | None
    status: str
    conso_me_t: Decimal | None
    conso_ae_t: Decimal | None
    conso_total_t: Decimal | None
    co2_t: Decimal | None
    cargo_bl_t: Decimal | None
    cargo_mrv_t: Decimal | None
    distance_nm: Decimal | None
    ef_gco2_tkm: Decimal | None  # méthode sélectionnée
    is_ballast: bool
    source: str  # events | legacy_noon | legacy_kpi | none


@dataclass(frozen=True)
class VesselOperational:
    """Suivi opérationnel d'un navire sur une période (année)."""

    vessel_id: int
    vessel_name: str
    vessel_code: str
    period: int
    method: str
    years: list[int]
    voyages: list[VoyageRow]
    co2_total_t: Decimal
    conso_total_t: Decimal
    distance_total_nm: Decimal
    leg_count: int
    laden_count: int
    ballast_count: int


def _summary_ef_for_method(summary: VoyageEmissionSummary, method: str) -> Decimal | None:
    return {
        "A": summary.ef_method_a,
        "B": summary.ef_method_b,
        "C": summary.ef_method_c,
    }.get(method)


async def vessel_operational(
    db: AsyncSession, vessel_id: int, *, period: int, method: str = "A"
) -> VesselOperational | None:
    """Page 2 — liste des voyages d'un navire + KPI par voyage (grand livre).

    Lit ``voyage_emission_summaries`` (source de vérité env., cache du grand
    livre) avec repli ``LegKPI`` (legacy) pour les voyages sans résumé. Filtre
    par année d'ETD. ``method`` sélectionne l'EF affiché (A/B/C), jamais mélangé.
    """
    _check_method(method)
    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        return None

    legs = list(
        (await db.execute(select(Leg).where(Leg.vessel_id == vessel_id).order_by(Leg.etd.asc())))
        .scalars()
        .all()
    )
    summary_by_leg: dict[int, VoyageEmissionSummary] = {}
    try:
        summary_by_leg = {
            s.leg_id: s for s in (await db.execute(select(VoyageEmissionSummary))).scalars().all()
        }
    except Exception:
        summary_by_leg = {}
    kpi_by_leg = {k.leg_id: k for k in (await db.execute(select(LegKPI))).scalars().all()}

    port_ids = {leg.departure_port_id for leg in legs} | {leg.arrival_port_id for leg in legs}
    ports_by_id: dict[int, Port] = {}
    if port_ids:
        ports_by_id = {
            p.id: p
            for p in (await db.execute(select(Port).where(Port.id.in_(port_ids)))).scalars().all()
        }

    years = sorted({leg.etd.year for leg in legs if leg.etd is not None}, reverse=True)

    rows: list[VoyageRow] = []
    for leg in legs:
        if leg.etd is None or leg.etd.year != period:
            continue
        dep = ports_by_id.get(leg.departure_port_id)
        arr = ports_by_id.get(leg.arrival_port_id)
        summary = summary_by_leg.get(leg.id)
        if summary is not None:
            cargo_bl = summary.cargo_bl_t if summary.cargo_bl_t is not None else Decimal(0)
            rows.append(
                VoyageRow(
                    leg_id=leg.id,
                    leg_code=leg.leg_code,
                    dep_locode=(dep.locode if dep else None),
                    arr_locode=(arr.locode if arr else None),
                    dep_country=(dep.country if dep else None),
                    arr_country=(arr.country if arr else None),
                    etd=leg.etd,
                    ata=leg.ata,
                    status=leg.status,
                    conso_me_t=summary.conso_me_t,
                    conso_ae_t=summary.conso_ae_t,
                    conso_total_t=summary.conso_total_t,
                    co2_t=summary.co2_t,
                    cargo_bl_t=summary.cargo_bl_t,
                    cargo_mrv_t=summary.cargo_mrv_t,
                    distance_nm=(
                        summary.distance_nm if summary.distance_nm is not None else leg.distance_nm
                    ),
                    ef_gco2_tkm=_summary_ef_for_method(summary, method),
                    is_ballast=cargo_bl <= 0,
                    source=summary.source,
                )
            )
        else:
            k = kpi_by_leg.get(leg.id)
            co2_t = (
                (k.co2_emitted_kg / Decimal(1000)) if (k and k.co2_emitted_kg is not None) else None
            )
            cargo_bl = (
                (k.tonnage_kg / Decimal(1000)) if (k and k.tonnage_kg is not None) else Decimal(0)
            )
            rows.append(
                VoyageRow(
                    leg_id=leg.id,
                    leg_code=leg.leg_code,
                    dep_locode=(dep.locode if dep else None),
                    arr_locode=(arr.locode if arr else None),
                    dep_country=(dep.country if dep else None),
                    arr_country=(arr.country if arr else None),
                    etd=leg.etd,
                    ata=leg.ata,
                    status=leg.status,
                    conso_me_t=None,
                    conso_ae_t=None,
                    conso_total_t=None,
                    co2_t=co2_t,
                    cargo_bl_t=(cargo_bl if cargo_bl > 0 else None),
                    cargo_mrv_t=None,
                    distance_nm=leg.distance_nm,
                    ef_gco2_tkm=None,
                    is_ballast=cargo_bl <= 0,
                    source=("legacy_kpi" if k is not None else "none"),
                )
            )

    co2_total = sum((r.co2_t for r in rows if r.co2_t is not None), Decimal(0))
    conso_total = sum((r.conso_total_t for r in rows if r.conso_total_t is not None), Decimal(0))
    dist_total = sum((r.distance_nm for r in rows if r.distance_nm is not None), Decimal(0))
    laden = sum(1 for r in rows if not r.is_ballast)

    return VesselOperational(
        vessel_id=vessel.id,
        vessel_name=vessel.name,
        vessel_code=vessel.code,
        period=period,
        method=method,
        years=(years or [period]),
        voyages=rows,
        co2_total_t=co2_total.quantize(_T_QUANT),
        conso_total_t=conso_total.quantize(_T_QUANT),
        distance_total_nm=dist_total.quantize(_T_QUANT),
        leg_count=len(rows),
        laden_count=laden,
        ballast_count=len(rows) - laden,
    )


# ═══════════════════════════════════════════ Page 3 — Qualité des données


@dataclass(frozen=True)
class QualitySeverityCount:
    severity: str
    total: int  # fails
    unacknowledged: int


@dataclass(frozen=True)
class QualityRuleCount:
    rule_id: str
    count: int


@dataclass(frozen=True)
class PendingReset:
    reading_id: int
    event_id: int
    engine_id: int
    fuel_counter_l: Decimal | None
    leg_id: int | None
    leg_code: str | None
    vessel_name: str | None


@dataclass(frozen=True)
class UnreconciledBunker:
    bunker_id: int
    bdn_number: str
    vessel_name: str | None
    leg_id: int | None
    leg_code: str | None
    delivery_datetime_utc: datetime
    window_days: Decimal


@dataclass(frozen=True)
class NoonCompleteness:
    leg_id: int
    leg_code: str
    vessel_name: str | None
    noons_present: int
    noons_expected: int
    pct: Decimal | None


@dataclass(frozen=True)
class QualityTrendPoint:
    year: int
    month: int
    label: str
    count: int


@dataclass(frozen=True)
class QualityOverview:
    generated_at: datetime
    severity_counts: list[QualitySeverityCount]
    by_rule: list[QualityRuleCount]
    pending_resets: list[PendingReset]
    unreconciled_bunkers: list[UnreconciledBunker]
    noon_completeness: list[NoonCompleteness]
    trend: list[QualityTrendPoint]
    trend_max: int
    total_fails: int
    total_unacknowledged: int


_QUALITY_SEVERITIES: tuple[str, ...] = ("bloquant", "warning", "info")


def _leg_is_active(leg: Leg) -> bool:
    """Voyage « actif » = non clôturé (pas d'approbation) et non annulé
    (miroir de ``validation_rules_catalog._leg_is_active`` — non importé pour
    ne pas coupler la page à la catalogue de règles, cf. périmètre LOT 12)."""
    return leg.closure_approved_at is None and leg.status != "cancelled"


def _quality_trend(rows: list[QualityCheckResult], *, now: datetime) -> list[QualityTrendPoint]:
    """Nombre d'anomalies (fails) par mois d'exécution, 12 mois glissants."""
    months: list[tuple[int, int]] = []
    y, m = now.year, now.month
    for _ in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    totals: dict[tuple[int, int], int] = dict.fromkeys(months, 0)
    for r in rows:
        if r.executed_at is None:
            continue
        key = (r.executed_at.year, r.executed_at.month)
        if key in totals:
            totals[key] += 1
    return [
        QualityTrendPoint(year=yy, month=mm, label=f"{mm:02d}/{yy}", count=totals[(yy, mm)])
        for (yy, mm) in months
    ]


async def quality_overview(
    db: AsyncSession, *, vessel_id: int | None = None, now: datetime | None = None
) -> QualityOverview:
    """Page 3 — tour de contrôle qualité (synthèse des ``quality_check_results``
    + resets compteur en attente + soutages non recoupés FLGO + complétude noon).

    Lecture pure : agrège le journal du moteur de règles (lot 8) et recoupe en
    direct les soutages via ``flgo_sync.flgo_matches_for_bunker`` (R24) — aucune
    écriture, aucune nouvelle règle. ``vessel_id`` restreint aux voyages du
    navire (les résultats sans leg restent hors filtre navire)."""
    now = now or datetime.now(UTC)

    # Décor legs/navires (une passe).
    legs_by_id = {leg.id: leg for leg in (await db.execute(select(Leg))).scalars().all()}
    vessels_by_id = {v.id: v for v in (await db.execute(select(Vessel))).scalars().all()}

    def _leg_in_scope(leg_id: int | None) -> bool:
        if vessel_id is None:
            return True
        leg = legs_by_id.get(leg_id) if leg_id is not None else None
        return leg is not None and leg.vessel_id == vessel_id

    # QCR fails.
    qcr_rows = list(
        (await db.execute(select(QualityCheckResult).where(QualityCheckResult.result == "fail")))
        .scalars()
        .all()
    )
    if vessel_id is not None:
        qcr_rows = [r for r in qcr_rows if _leg_in_scope(r.leg_id)]

    sev_total = dict.fromkeys(_QUALITY_SEVERITIES, 0)
    sev_unack = dict.fromkeys(_QUALITY_SEVERITIES, 0)
    rule_counts: dict[str, int] = {}
    for r in qcr_rows:
        sev = r.severity_applied if r.severity_applied in sev_total else "info"
        sev_total[sev] += 1
        if r.acknowledged_at is None:
            sev_unack[sev] += 1
        rule_counts[r.rule_id] = rule_counts.get(r.rule_id, 0) + 1

    severity_counts = [
        QualitySeverityCount(severity=s, total=sev_total[s], unacknowledged=sev_unack[s])
        for s in _QUALITY_SEVERITIES
    ]
    by_rule = sorted(
        (QualityRuleCount(rule_id=rid, count=n) for rid, n in rule_counts.items()),
        key=lambda x: (-x.count, x.rule_id),
    )

    # Resets compteur en attente (R10) — même critère que /mrv/qualite.
    from app.models.nav_event import NavEventEngineReading

    reset_rows = list(
        (
            await db.execute(
                select(NavEventEngineReading)
                .where(
                    NavEventEngineReading.is_counter_reset.is_(True),
                    NavEventEngineReading.reset_confirmed_by.is_(None),
                )
                .order_by(NavEventEngineReading.id.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    # Résolution leg/navire du relevé (via son événement).
    reset_event_ids = {r.event_id for r in reset_rows}
    events_by_id = {}
    if reset_event_ids:
        from app.models.nav_event import NavEvent

        events_by_id = {
            e.id: e
            for e in (await db.execute(select(NavEvent).where(NavEvent.id.in_(reset_event_ids))))
            .scalars()
            .all()
        }
    pending_resets: list[PendingReset] = []
    for rd in reset_rows:
        ev = events_by_id.get(rd.event_id)
        leg = legs_by_id.get(ev.leg_id) if ev is not None else None
        if vessel_id is not None and (leg is None or leg.vessel_id != vessel_id):
            continue
        vessel = vessels_by_id.get(leg.vessel_id) if leg is not None and leg.vessel_id else None
        pending_resets.append(
            PendingReset(
                reading_id=rd.id,
                event_id=rd.event_id,
                engine_id=rd.engine_id,
                fuel_counter_l=rd.fuel_counter_l,
                leg_id=(leg.id if leg is not None else None),
                leg_code=(leg.leg_code if leg is not None else None),
                vessel_name=(vessel.name if vessel is not None else None),
            )
        )

    # Soutages non recoupés FLGO (R24) — recoupement en direct (flgo_sync).
    bunker_rows = list(
        (
            await db.execute(
                select(BunkerOperation)
                .where(BunkerOperation.status == "valide_master")
                .order_by(BunkerOperation.delivery_datetime_utc.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    unreconciled: list[UnreconciledBunker] = []
    for b in bunker_rows:
        if vessel_id is not None and b.vessel_id != vessel_id:
            continue
        match = await flgo_sync.flgo_matches_for_bunker(db, b)
        if match.matched:
            continue
        leg = legs_by_id.get(b.leg_id) if b.leg_id is not None else None
        vessel = vessels_by_id.get(b.vessel_id)
        unreconciled.append(
            UnreconciledBunker(
                bunker_id=b.id,
                bdn_number=b.bdn_number,
                vessel_name=(vessel.name if vessel is not None else None),
                leg_id=(leg.id if leg is not None else None),
                leg_code=(leg.leg_code if leg is not None else None),
                delivery_datetime_utc=b.delivery_datetime_utc,
                window_days=match.window_days,
            )
        )

    # Complétude des noons par voyage actif (attendus vs présents).
    noon_completeness: list[NoonCompleteness] = []
    for leg in legs_by_id.values():
        if not _leg_is_active(leg):
            continue
        if vessel_id is not None and leg.vessel_id != vessel_id:
            continue
        noons_present = len(await _finalized_noons(db, leg.id))
        start = leg.atd or leg.etd
        end = min((leg.ata or leg.eta or now), now) if start is not None else None
        expected = 0
        if start is not None and end is not None and end > start:
            expected = int((end - start).total_seconds() // 86400)
        # Un voyage sans jour de mer écoulé (à quai) n'a pas d'attendu.
        if expected == 0 and noons_present == 0:
            continue
        vessel = vessels_by_id.get(leg.vessel_id) if leg.vessel_id else None
        pct = (
            (Decimal(noons_present) * Decimal(100) / Decimal(expected)).quantize(_PCT_QUANT)
            if expected > 0
            else None
        )
        noon_completeness.append(
            NoonCompleteness(
                leg_id=leg.id,
                leg_code=leg.leg_code,
                vessel_name=(vessel.name if vessel is not None else None),
                noons_present=noons_present,
                noons_expected=expected,
                pct=pct,
            )
        )
    noon_completeness.sort(key=lambda n: (n.pct if n.pct is not None else Decimal(0)))

    trend = _quality_trend(qcr_rows, now=now)
    trend_max = max((p.count for p in trend), default=0)

    return QualityOverview(
        generated_at=now,
        severity_counts=severity_counts,
        by_rule=by_rule,
        pending_resets=pending_resets,
        unreconciled_bunkers=unreconciled,
        noon_completeness=noon_completeness,
        trend=trend,
        trend_max=trend_max,
        total_fails=len(qcr_rows),
        total_unacknowledged=sum(sev_unack.values()),
    )
