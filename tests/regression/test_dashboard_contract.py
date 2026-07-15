"""SUITE GELÉE — contrat d'interface Dashboard × couche événementielle (NC-01).

Le futur Dashboard consomme ``kpi_env``/``emission_ledger`` par **import
Python direct** (même dépôt/process — décision actée le 15/07/2026, pas
d'API HTTP séparée). Le contrat porte donc sur la **stabilité des
dataclasses et des signatures**, pas sur un schéma JSON public.

Ce fichier fige :
  - les champs (nom + annotation) des dataclasses retournées par
    ``fleet_summary``, ``vessel_operational``, ``voyage_detail``,
    ``quality_overview`` (et leurs sous-dataclasses imbriquées) ;
  - les clés du dict retourné par ``emission_ledger.emissions_breakdown`` ;
  - la signature exacte des 5 fonctions couvertes.

Politique de dépréciation (cf. ``kpi_env.DASHBOARD_CONTRACT_VERSION``) :
  - Ajouter un champ **avec une valeur par défaut** est une extension
    compatible : mettre à jour le gabarit correspondant ci-dessous dans le
    MÊME commit, sans incrémenter la version.
  - Renommer, retirer ou retyper un champ existant, ou changer une
    signature couverte, est un changement CASSANT : revue explicite avec le
    porteur du Dashboard avant fusion, et incrément de
    ``DASHBOARD_CONTRACT_VERSION``.

Les annotations sont comparées en tant que CHAÎNES (``from __future__ import
annotations`` est actif dans ``kpi_env``/``emission_ledger`` — les types ne
sont jamais résolus à l'exécution), donc lisibles telles qu'écrites dans le
code source.
"""

from __future__ import annotations

import dataclasses
import inspect

from app.services import emission_ledger, kpi_env

# ─────────────────────────────────────────────────── Aide de comparaison


def _fields(cls) -> dict[str, str]:
    """Nom de champ → annotation (chaîne), insensible à l'ordre de déclaration."""
    return {f.name: f.type for f in dataclasses.fields(cls)}


def _sig(fn) -> str:
    return str(inspect.signature(fn))


# ─────────────────────────────────────────────────── Gabarits — kpi_env

FLEET_SUMMARY_FIELDS = {
    "period": "int",
    "method": "str",
    "generated_at": "datetime",
    "fleet": "VesselKpiBlock",
    "vessels": "list[VesselKpiBlock]",
    "selected": "VesselKpiBlock",
    "trend": "list[TrendPoint]",
    "trend_max_t": "Decimal",
    "params": "dict[str, DashboardParamValue]",
}

VESSEL_KPI_BLOCK_FIELDS = {
    "vessel_id": "int | None",
    "vessel_code": "str | None",
    "label": "str",
    "leg_count": "int",
    "laden_leg_count": "int",
    "ballast_leg_count": "int",
    "co2_emitted_t": "Decimal",
    "distance_nm": "Decimal",
    "ef": "EfResult",
    "avoided_container": "AvoidedResult",
    "avoided_airfreight": "AvoidedResult",
    "completeness": "CompletenessBlock",
    "legs_excluded_non_event": "int",  # NC-04
}

COMPLETENESS_BLOCK_FIELDS = {
    "legs_total": "int",
    "legs_with_data": "int",
    "legs_without_data": "int",
}

EF_RESULT_FIELDS = {
    "method": "str",
    "value_gco2_tkm": "Decimal | None",
    "na_reason": "str | None",
}

AVOIDED_RESULT_FIELDS = {
    "reference": "str",
    "avoided_t": "Decimal | None",
    "avoided_pct": "Decimal | None",
    "ef_reference_gco2_tkm": "Decimal",
    "na_reason": "str | None",
}

TREND_POINT_FIELDS = {
    "year": "int",
    "month": "int",
    "label": "str",
    "co2_emitted_t": "Decimal",
}

VESSEL_OPERATIONAL_FIELDS = {
    "vessel_id": "int",
    "vessel_name": "str",
    "vessel_code": "str",
    "period": "int",
    "method": "str",
    "years": "list[int]",
    "voyages": "list[VoyageRow]",
    "co2_total_t": "Decimal",
    "conso_total_t": "Decimal",
    "distance_total_nm": "Decimal",
    "leg_count": "int",
    "laden_count": "int",
    "ballast_count": "int",
    "excluded_non_event_count": "int",  # NC-04
}

VOYAGE_ROW_FIELDS = {
    "leg_id": "int",
    "leg_code": "str",
    "dep_locode": "str | None",
    "arr_locode": "str | None",
    "dep_country": "str | None",
    "arr_country": "str | None",
    "etd": "datetime | None",
    "ata": "datetime | None",
    "status": "str",
    "conso_me_t": "Decimal | None",
    "conso_ae_t": "Decimal | None",
    "conso_total_t": "Decimal | None",
    "co2_t": "Decimal | None",
    "cargo_bl_t": "Decimal | None",
    "cargo_mrv_t": "Decimal | None",
    "distance_nm": "Decimal | None",
    "ef_gco2_tkm": "Decimal | None",
    "is_ballast": "bool",
    "source": "str",  # events | legacy_noon | legacy_kpi | none
}

VOYAGE_DETAIL_FIELDS = {
    "leg_id": "int",
    "leg_code": "str",
    "vessel_id": "int | None",
    "vessel_name": "str | None",
    "vessel_code": "str | None",
    "dep_port": "Port | None",
    "arr_port": "Port | None",
    "source": "str",  # events | legacy_noon
    "ledger": "emission_ledger.LedgerResult",
    "me_pct": "Decimal | None",
    "ae_pct": "Decimal | None",
    "conso": "ConsoTarget",
    "duration_days": "Decimal | None",
    "rob_chain": "list[iec.RobPoint]",
    "bunkers": "list[BunkerMarker]",
    "events": "list[EventChainItem]",
    "propulsion": "PropulsionProfile",
    "quality": "list[QualityCheckResult]",
    "map_points": "list[dict]",
    "map_segments": "list[dict]",
}

QUALITY_OVERVIEW_FIELDS = {
    "generated_at": "datetime",
    "severity_counts": "list[QualitySeverityCount]",
    "by_rule": "list[QualityRuleCount]",
    "pending_resets": "list[PendingReset]",
    "unreconciled_bunkers": "list[UnreconciledBunker]",
    "noon_completeness": "list[NoonCompleteness]",
    "trend": "list[QualityTrendPoint]",
    "trend_max": "int",
    "total_fails": "int",
    "total_unacknowledged": "int",
}

# ─────────────────────────────────────────────────── Gabarits — emission_ledger

LEDGER_RESULT_FIELDS = {
    "leg_id": "int",
    "source": "str",
    "conso_me_t": "Decimal | None",
    "conso_ae_t": "Decimal | None",
    "conso_total_t": "Decimal | None",
    "conso_mouillage_t": "Decimal | None",
    "conso_hors_mouillage_t": "Decimal | None",
    "conso_escale_t": "Decimal | None",
    "do_consumed_t": "Decimal | None",
    "distance_nm": "Decimal | None",
    "cargo_bl_t": "Decimal | None",
    "cargo_mrv_t": "Decimal | None",
    "factor": "ResolvedEmissionFactor",
    "do_co2_factor": "Decimal",
    "emissions": "dict[str, Any]",
    "co2_emitted_t": "Decimal | None",
    "ch4_g": "Decimal | None",
    "n2o_g": "Decimal | None",
    "wtt_co2eq_t": "Decimal | None",
    "avoided_co2_kg": "Decimal | None",
    "ef_method_a": "Decimal | None",
    "ef_method_b": "Decimal | None",
    "ef_method_c": "Decimal | None",
}

# Clés exactes du dict retourné par emission_ledger.emissions_breakdown() —
# stable que conso_t soit None ou renseigné (mêmes clés dans les deux cas).
EMISSIONS_BREAKDOWN_KEYS = {
    "conso_t",
    "co2_t",
    "ch4_g",
    "n2o_g",
    "wtt_gco2eq_per_mj",
    "wtt_co2eq_t",
    "ef_co2_kg_per_kg",
    "ef_ch4_kg_per_kg",
    "ef_n2o_kg_per_kg",
}

# ─────────────────────────────────────────────────── Signatures figées
#
# ``inspect.signature`` ne résout jamais les annotations-chaînes ici (pas
# d'``eval_str``) : chaque chaîne ci-dessous est EXACTEMENT le texte source
# de la signature (nom des types tels qu'importés dans le module, pas leur
# chemin qualifié).

FLEET_SUMMARY_SIG = (
    "(db: AsyncSession, *, period: int, method: str, vessel_id: int | None = None, "
    "now: datetime | None = None, strict: bool = False) -> FleetSummary"
)
VESSEL_OPERATIONAL_SIG = (
    "(db: AsyncSession, vessel_id: int, *, period: int, method: str = 'A', "
    "strict: bool = False) -> VesselOperational | None"
)
VOYAGE_DETAIL_SIG = (
    "(db: AsyncSession, leg_id: int, *, conso_target_l_j: Decimal | None = None) "
    "-> VoyageDetail | None"
)
QUALITY_OVERVIEW_SIG = (
    "(db: AsyncSession, *, vessel_id: int | None = None, now: datetime | None = None) "
    "-> QualityOverview"
)
EMISSIONS_BREAKDOWN_SIG = (
    "(conso_t: Decimal | None, factor: ResolvedEmissionFactor) -> dict[str, Any]"
)


def test_fleet_summary_contract():
    assert _fields(kpi_env.FleetSummary) == FLEET_SUMMARY_FIELDS
    assert _fields(kpi_env.VesselKpiBlock) == VESSEL_KPI_BLOCK_FIELDS
    assert _fields(kpi_env.CompletenessBlock) == COMPLETENESS_BLOCK_FIELDS
    assert _fields(kpi_env.EfResult) == EF_RESULT_FIELDS
    assert _fields(kpi_env.AvoidedResult) == AVOIDED_RESULT_FIELDS
    assert _fields(kpi_env.TrendPoint) == TREND_POINT_FIELDS
    assert _sig(kpi_env.fleet_summary) == FLEET_SUMMARY_SIG


def test_vessel_operational_contract():
    assert _fields(kpi_env.VesselOperational) == VESSEL_OPERATIONAL_FIELDS
    assert _fields(kpi_env.VoyageRow) == VOYAGE_ROW_FIELDS
    assert _sig(kpi_env.vessel_operational) == VESSEL_OPERATIONAL_SIG


def test_voyage_detail_contract():
    assert _fields(kpi_env.VoyageDetail) == VOYAGE_DETAIL_FIELDS
    assert _sig(kpi_env.voyage_detail) == VOYAGE_DETAIL_SIG


def test_quality_overview_contract():
    assert _fields(kpi_env.QualityOverview) == QUALITY_OVERVIEW_FIELDS
    assert _fields(kpi_env.QualitySeverityCount) == {
        "severity": "str",
        "total": "int",
        "unacknowledged": "int",
    }
    assert _fields(kpi_env.QualityRuleCount) == {"rule_id": "str", "count": "int"}
    assert _fields(kpi_env.PendingReset) == {
        "reading_id": "int",
        "event_id": "int",
        "engine_id": "int",
        "fuel_counter_l": "Decimal | None",
        "leg_id": "int | None",
        "leg_code": "str | None",
        "vessel_name": "str | None",
    }
    assert _fields(kpi_env.UnreconciledBunker) == {
        "bunker_id": "int",
        "bdn_number": "str",
        "vessel_name": "str | None",
        "leg_id": "int | None",
        "leg_code": "str | None",
        "delivery_datetime_utc": "datetime",
        "window_days": "Decimal",
    }
    assert _fields(kpi_env.NoonCompleteness) == {
        "leg_id": "int",
        "leg_code": "str",
        "vessel_name": "str | None",
        "noons_present": "int",
        "noons_expected": "int",
        "pct": "Decimal | None",
    }
    assert _fields(kpi_env.QualityTrendPoint) == {
        "year": "int",
        "month": "int",
        "label": "str",
        "count": "int",
    }
    assert _sig(kpi_env.quality_overview) == QUALITY_OVERVIEW_SIG


def test_emission_ledger_contract():
    assert _fields(emission_ledger.LedgerResult) == LEDGER_RESULT_FIELDS
    assert _sig(emission_ledger.emissions_breakdown) == EMISSIONS_BREAKDOWN_SIG

    from decimal import Decimal

    from app.services.referential_env import ResolvedEmissionFactor

    factor = ResolvedEmissionFactor(
        fuel_type="MDO",
        ef_co2_kg_per_kg=Decimal("3.206"),
        ef_ch4_kg_per_kg=Decimal("0"),
        ef_n2o_kg_per_kg=Decimal("0"),
        wtt_gco2eq_per_mj=Decimal("0"),
        source_reference="test",
        valid_from=None,
        valid_to=None,
        is_current=True,
        is_fallback=True,
    )
    none_conso = emission_ledger.emissions_breakdown(None, factor)
    assert set(none_conso) == EMISSIONS_BREAKDOWN_KEYS
    some_conso = emission_ledger.emissions_breakdown(Decimal("10"), factor)
    assert set(some_conso) == EMISSIONS_BREAKDOWN_KEYS


def test_contract_version_present():
    """La constante de version existe — tout changement cassant ci-dessus doit l'incrémenter."""
    assert isinstance(kpi_env.DASHBOARD_CONTRACT_VERSION, int)
    assert kpi_env.DASHBOARD_CONTRACT_VERSION >= 1
