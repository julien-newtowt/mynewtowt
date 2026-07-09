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

SOURCE ACTUELLE DES ÉMISSIONS — ``_emissions_provider()``
-----------------------------------------------------------
Toute la donnée d'émission consommée par ce module transite par
``_emissions_provider()``, qui lit les agrégats **legacy** déjà calculés par
``services.kpi.compute_for_leg`` / ``services.carbon.compute_carbon_for_leg``
(table ``leg_kpis``) — **lecture pure, aucune écriture** (ce module ne
réalimente jamais ``LegKPI``, à la différence de ``/kpi`` ; même posture que
``services.kpi_consolidated.consolidated_kpis``). C'est **l'unique** fonction
qui devra être rebranchée sur ``services/emission_ledger`` au **lot 9** —
aucun autre code de ce module ne doit être modifié pour cette bascule (même
forme de sortie attendue : liste de ``LegEmissionRecord``). Aucun nouveau
moteur de calcul carbone n'est créé ici.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import LegKPI
from app.models.leg import Leg
from app.models.validation import DashboardParameter
from app.models.vessel import Vessel
from app.services.co2 import NM_TO_KM

# ─────────────────────────────────────────────────────────── Constantes

EF_METHODS: tuple[str, ...] = ("A", "B", "C")

# Motifs N/A (jamais de valeur fabriquée — cf. UX §1 « Jamais de valeur
# fabriquée »). Le libellé de la méthode C reprend le vocabulaire du LOT 11.
NA_CARGO_MRV = "nécessite cargo MRV — lot 9"
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

    Cette forme est le contrat que le lot 9 devra respecter en rebranchant
    ``_emissions_provider`` sur ``services/emission_ledger`` : tant qu'une
    fonction renvoie une liste de ``LegEmissionRecord``, rien d'autre dans ce
    module n'a besoin de changer.
    """

    leg_id: int
    leg_code: str
    vessel_id: int
    co2_emitted_t: Decimal
    cargo_t: Decimal  # tonnage B/L réel (0 = voyage sur lest / sans cargo confirmé)
    distance_nm: Decimal
    etd: datetime | None
    ata: datetime | None
    has_kpi: bool  # LegKPI existe et porte un co2_emitted_kg renseigné


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


# ──────────────────────────────────────────── Source des émissions (legacy)


async def _emissions_provider(
    db: AsyncSession, *, vessel_id: int | None = None
) -> list[LegEmissionRecord]:
    """Legacy — agrège les ``LegKPI`` déjà calculés (aucun recalcul, aucune écriture).

    À REBRANCHER SUR ``services/emission_ledger`` AU LOT 9 : c'est la SEULE
    fonction de ce module qui touche la donnée d'émission brute. Le lot 9
    n'a qu'à réécrire son corps (même signature, même forme de sortie —
    ``list[LegEmissionRecord]``) pour que tout ``kpi_env.py`` (formules EF,
    CO2 évité, tendance, complétude) bascule sur le grand livre sans autre
    modification.

    Ne déclenche jamais ``services.kpi.compute_for_leg`` (contrairement à
    ``GET /kpi`` qui auto-alimente ``LegKPI`` à la visite) : ce module lit
    ce qui est déjà calculé, à l'image de ``services.kpi_consolidated``.
    """
    stmt = select(Leg)
    if vessel_id is not None:
        stmt = stmt.where(Leg.vessel_id == vessel_id)
    legs = list((await db.execute(stmt)).scalars().all())
    kpi_by_leg = {k.leg_id: k for k in (await db.execute(select(LegKPI))).scalars().all()}

    records: list[LegEmissionRecord] = []
    for leg in legs:
        k = kpi_by_leg.get(leg.id)
        has_kpi = k is not None and k.co2_emitted_kg is not None
        co2_t = (
            (k.co2_emitted_kg / Decimal(1000))
            if (k and k.co2_emitted_kg is not None)
            else Decimal(0)
        )
        cargo_t = (k.tonnage_kg / Decimal(1000)) if (k and k.tonnage_kg is not None) else Decimal(0)
        distance_nm = leg.distance_nm if leg.distance_nm is not None else Decimal(0)
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
    méthode C toujours (Cargo MRV indisponible sur le legacy), méthode A sur
    un voyage sur lest (cargo nul — pas de valeur fabriquée), ou distance
    nulle (les deux méthodes).
    """
    _check_method(method)
    if method == "C":
        return EfResult(method="C", value_gco2_tkm=None, na_reason=NA_CARGO_MRV)
    if record.distance_nm <= 0:
        return EfResult(method=method, value_gco2_tkm=None, na_reason=NA_ZERO_DISTANCE)

    distance_km = record.distance_nm * NM_TO_KM
    if method == "A":
        if record.cargo_t <= 0:
            return EfResult(method="A", value_gco2_tkm=None, na_reason=NA_BALLAST)
        denom = record.cargo_t * distance_km
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
    - **C** — toujours N/A (Cargo MRV indisponible sur le legacy — lot 9).
    """
    _check_method(method)
    total_co2_t = sum((r.co2_emitted_t for r in records), Decimal(0))

    if method == "C":
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
