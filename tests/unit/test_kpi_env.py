"""Tests unitaires — Dashboard Performance Environnementale (LOT 11).

Couvre les formules de ``services.kpi_env`` (spec §5) : EF méthode A « réel »
vs B « standardisé » sur fixtures laden/ballast (exclusion du dénominateur
en A, inclusion en B), méthode C toujours N/A motivé, CO2 évité paramétrable
(changer un ``dashboard_parameters`` change le résultat), tendance 12 mois
(mois vides = 0), et la garde « jamais de mélange » (méthode invalide lève).

Les tests des formules pures (``leg_ef``, ``aggregate_ef``,
``avoided_emissions``, ``monthly_trend``) construisent directement des
``LegEmissionRecord`` — aucun accès DB nécessaire. Seuls
``get_dashboard_parameters`` et ``fleet_summary`` (qui lisent
``dashboard_parameters``/``LegKPI``/``Leg`` via ``_emissions_provider``)
utilisent une base SQLite en mémoire locale à ce module (même patron que
``tests/unit/test_validation_engine.py`` — isolation, pas d'enforcement FK).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — enregistre tous les modèles sur Base.metadata
from app.database import Base
from app.models.finance import LegKPI
from app.models.leg import Leg
from app.models.port import Port
from app.models.validation import DashboardParameter
from app.models.vessel import Vessel
from app.models.voyage_emission_summary import VoyageEmissionSummary
from app.services.kpi_env import (
    DASHBOARD_PARAM_DEFAULTS,
    NA_BALLAST,
    NA_CARGO_MRV,
    NA_NO_LADEN_VOYAGE,
    AvoidedResult,
    EfResult,
    LegEmissionRecord,
    aggregate_ef,
    avoided_emissions,
    fleet_summary,
    get_dashboard_parameters,
    leg_ef,
    monthly_trend,
    vessel_operational,
)

OCC = Decimal("70")  # occupancy_rate_pct défaut
CAP = Decimal("1100")  # vessel_capacity_ref_t défaut

# ─────────────────────────────────────────────────────────────── Fixtures

# Voyage 1 — chargé : 500 t cargo B/L, 1000 nm, 50 t CO2 émis.
LEG_LADEN = LegEmissionRecord(
    leg_id=1,
    leg_code="1AFRBR6",
    vessel_id=1,
    co2_emitted_t=Decimal("50"),
    cargo_t=Decimal("500"),
    distance_nm=Decimal("1000"),
    etd=datetime(2026, 1, 5, tzinfo=UTC),
    ata=datetime(2026, 1, 25, tzinfo=UTC),
    has_kpi=True,
)

# Voyage 2 — sur lest : cargo nul, 800 nm, 30 t CO2 émis (le navire consomme
# même à vide — retour sans cargaison).
LEG_BALLAST = LegEmissionRecord(
    leg_id=2,
    leg_code="1BBRFR6",
    vessel_id=1,
    co2_emitted_t=Decimal("30"),
    cargo_t=Decimal("0"),
    distance_nm=Decimal("800"),
    etd=datetime(2026, 2, 1, tzinfo=UTC),
    ata=datetime(2026, 2, 18, tzinfo=UTC),
    has_kpi=True,
)


# ══════════════════════════════════════════════════ leg_ef (par voyage)


def test_leg_ef_method_a_laden_voyage_is_computed():
    result = leg_ef(LEG_LADEN, method="A", occupancy_pct=OCC, capacity_ref_t=CAP)
    assert result.method == "A"
    assert result.na_reason is None
    # 50 t CO2 × 1e6 / (500 t × (1000 nm × 1,852)) = 54,00 gCO2/t.km
    assert result.value_gco2_tkm == Decimal("54.00")


def test_leg_ef_method_a_ballast_voyage_is_na():
    """A « réel » : cargo nul ⇒ exclu (division par zéro évitée), marqué N/A."""
    result = leg_ef(LEG_BALLAST, method="A", occupancy_pct=OCC, capacity_ref_t=CAP)
    assert result.value_gco2_tkm is None
    assert result.na_reason == NA_BALLAST


def test_leg_ef_method_b_includes_ballast_voyage():
    """B « standardisé » : capacité×occupancy ne dépend pas du cargo réel —
    calculable même sur un voyage sur lest."""
    result = leg_ef(LEG_BALLAST, method="B", occupancy_pct=OCC, capacity_ref_t=CAP)
    assert result.na_reason is None
    # 30 t CO2 × 1e6 / (1100 × 0,70 × (800 × 1,852)) = 26,30 gCO2/t.km
    assert result.value_gco2_tkm == Decimal("26.30")


def test_leg_ef_method_c_always_na():
    result = leg_ef(LEG_LADEN, method="C", occupancy_pct=OCC, capacity_ref_t=CAP)
    assert result.value_gco2_tkm is None
    assert result.na_reason == NA_CARGO_MRV


def test_leg_ef_invalid_method_raises():
    with pytest.raises(ValueError):
        leg_ef(LEG_LADEN, method="D", occupancy_pct=OCC, capacity_ref_t=CAP)


# ═══════════════════════════════════════════ aggregate_ef (flotte/période)


def test_aggregate_ef_method_a_excludes_ballast_from_denominator():
    ef, denom = aggregate_ef(
        [LEG_LADEN, LEG_BALLAST], method="A", occupancy_pct=OCC, capacity_ref_t=CAP
    )
    assert ef.na_reason is None
    # numérateur = 50+30=80 t (les deux voyages) ; dénominateur = SEUL le
    # voyage chargé (500 t × 1852 km = 926 000 t.km) — le voyage sur lest est
    # exclu du dénominateur, conformément à la spec §5.1.
    assert denom == Decimal("926000")
    assert ef.value_gco2_tkm == Decimal("86.39")


def test_aggregate_ef_method_b_includes_ballast_in_denominator():
    ef, denom = aggregate_ef(
        [LEG_LADEN, LEG_BALLAST], method="B", occupancy_pct=OCC, capacity_ref_t=CAP
    )
    assert ef.na_reason is None
    # dénominateur = capacité×occupancy×distance sur LES DEUX voyages
    # (1100×0,70×1852) + (1100×0,70×1481,6) = 2 566 872 t.km.
    assert denom == Decimal("2566872")
    assert ef.value_gco2_tkm == Decimal("31.17")


def test_aggregate_ef_method_c_is_na_with_reason():
    ef, denom = aggregate_ef(
        [LEG_LADEN, LEG_BALLAST], method="C", occupancy_pct=OCC, capacity_ref_t=CAP
    )
    assert ef.value_gco2_tkm is None
    assert ef.na_reason == NA_CARGO_MRV
    assert denom == Decimal(0)


def test_aggregate_ef_method_a_no_laden_voyage_is_na():
    """Un agrégat composé uniquement de voyages sur lest ne fabrique pas de valeur."""
    ef, denom = aggregate_ef([LEG_BALLAST], method="A", occupancy_pct=OCC, capacity_ref_t=CAP)
    assert ef.value_gco2_tkm is None
    assert ef.na_reason == NA_NO_LADEN_VOYAGE
    assert denom == Decimal(0)


def test_aggregate_ef_invalid_method_raises():
    with pytest.raises(ValueError):
        aggregate_ef([LEG_LADEN], method="Z", occupancy_pct=OCC, capacity_ref_t=CAP)


# ══════════════════════════════════════════════════════ CO2 évité (§5.2)


def test_avoided_emissions_changes_when_reference_parameter_changes():
    """CO2 évité comparateurs — changer le paramètre EF_référence change le résultat.

    EF_TOWT très inférieur à la référence (véranda de test isolée du fixture
    laden/ballast ci-dessus, qui vise un ratio ballast/laden réaliste plutôt
    qu'une performance absolue) : le CO2 évité doit être positif et changer
    strictement quand on fait varier ``ef_reference_gco2_tkm`` — exactement
    le scénario de l'écran Administration (16 → 20 gCO2/t.km).
    """
    ef = EfResult(method="A", value_gco2_tkm=Decimal("5"), na_reason=None)
    denom_tkm = Decimal("1000000")  # 1 000 000 t.km

    avoided_16 = avoided_emissions(
        ef, denom_tkm, ef_reference_gco2_tkm=Decimal("16"), reference="container_ship"
    )
    avoided_20 = avoided_emissions(
        ef, denom_tkm, ef_reference_gco2_tkm=Decimal("20"), reference="container_ship"
    )

    assert avoided_16.avoided_t == Decimal("11.00")  # (16-5) × 1e6 / 1e6
    assert avoided_20.avoided_t == Decimal("15.00")  # (20-5) × 1e6 / 1e6
    assert avoided_16.avoided_t != avoided_20.avoided_t
    assert avoided_16.avoided_pct == Decimal("68.8")  # (16-5)/16*100
    assert avoided_20.avoided_pct == Decimal("75.0")  # (20-5)/20*100


def test_avoided_emissions_na_when_ef_is_na():
    ef = EfResult(method="C", value_gco2_tkm=None, na_reason=NA_CARGO_MRV)
    result = avoided_emissions(
        ef, Decimal(0), ef_reference_gco2_tkm=Decimal("16"), reference="container_ship"
    )
    assert isinstance(result, AvoidedResult)
    assert result.avoided_t is None
    assert result.avoided_pct is None
    assert result.na_reason == NA_CARGO_MRV


# ═══════════════════════════════════════════════════════ Tendance 12 mois


def test_monthly_trend_empty_months_are_zero_not_omitted():
    now = datetime(2026, 7, 9, tzinfo=UTC)
    records = [LEG_LADEN, LEG_BALLAST]  # ata en janvier et février 2026

    trend = monthly_trend(records, now=now)

    assert len(trend) == 12
    # Fenêtre glissante août 2025 → juillet 2026 (12 mois finissant à `now`).
    assert (trend[0].year, trend[0].month) == (2025, 8)
    assert (trend[-1].year, trend[-1].month) == (2026, 7)

    by_month = {(p.year, p.month): p.co2_emitted_t for p in trend}
    assert by_month[(2026, 1)] == Decimal("50.00")  # LEG_LADEN.ata
    assert by_month[(2026, 2)] == Decimal("30.00")  # LEG_BALLAST.ata
    # Tous les autres mois de la fenêtre sont à 0 — jamais omis de la série.
    other_months = {k: v for k, v in by_month.items() if k not in {(2026, 1), (2026, 2)}}
    assert len(other_months) == 10
    assert all(v == Decimal("0.00") for v in other_months.values())


def test_monthly_trend_ignores_records_without_ata():
    now = datetime(2026, 7, 9, tzinfo=UTC)
    no_arrival = LegEmissionRecord(
        leg_id=3,
        leg_code="1CFRBR6",
        vessel_id=1,
        co2_emitted_t=Decimal("99"),
        cargo_t=Decimal("100"),
        distance_nm=Decimal("500"),
        etd=datetime(2026, 6, 1, tzinfo=UTC),
        ata=None,  # pas encore arrivé
        has_kpi=False,
    )
    trend = monthly_trend([no_arrival], now=now)
    assert sum((p.co2_emitted_t for p in trend), Decimal(0)) == Decimal("0.00")


# ═══════════════════════════════════════ get_dashboard_parameters / fleet_summary


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_dashboard_parameters_coded_default_when_table_empty(db):
    params = await get_dashboard_parameters(db)
    assert set(params) == set(DASHBOARD_PARAM_DEFAULTS)
    for name, (default_value, default_unit) in DASHBOARD_PARAM_DEFAULTS.items():
        assert params[name].value == default_value
        assert params[name].unit == default_unit
        assert params[name].source == "coded_default"


@pytest.mark.asyncio
async def test_get_dashboard_parameters_reads_db_override(db):
    db.add(
        DashboardParameter(
            parameter_name="ef_container_ship_gco2_tkm",
            vessel_id=None,
            value=Decimal("20"),
            unit="gCO2/t.km",
        )
    )
    await db.flush()

    params = await get_dashboard_parameters(db)
    assert params["ef_container_ship_gco2_tkm"].value == Decimal("20")
    assert params["ef_container_ship_gco2_tkm"].source == "global"
    # Les autres paramètres, non présents en base, retombent sur le défaut codé.
    assert params["ef_airfreight_gco2_tkm"].source == "coded_default"


async def _seed_two_legs(db):
    """1 voyage chargé + 1 voyage sur lest, même flotte que LEG_LADEN/LEG_BALLAST."""
    db.add(Vessel(id=1, code="ANE", name="Anemos", is_active=True))
    db.add(Port(id=1, locode="FRLEH", name="Le Havre", country="FR"))
    db.add(Port(id=2, locode="BRSSZ", name="Santos", country="BR"))
    await db.flush()

    db.add(
        Leg(
            id=1,
            leg_code="1AFRBR6",
            vessel_id=1,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=LEG_LADEN.etd,
            eta_ref=LEG_LADEN.ata,
            etd=LEG_LADEN.etd,
            eta=LEG_LADEN.ata,
            ata=LEG_LADEN.ata,
            distance_nm=LEG_LADEN.distance_nm,
        )
    )
    db.add(
        Leg(
            id=2,
            leg_code="1BBRFR6",
            vessel_id=1,
            departure_port_id=2,
            arrival_port_id=1,
            etd_ref=LEG_BALLAST.etd,
            eta_ref=LEG_BALLAST.ata,
            etd=LEG_BALLAST.etd,
            eta=LEG_BALLAST.ata,
            ata=LEG_BALLAST.ata,
            distance_nm=LEG_BALLAST.distance_nm,
        )
    )
    await db.flush()

    db.add(
        LegKPI(
            leg_id=1,
            tonnage_kg=LEG_LADEN.cargo_t * Decimal(1000),
            co2_emitted_kg=LEG_LADEN.co2_emitted_t * Decimal(1000),
        )
    )
    db.add(
        LegKPI(
            leg_id=2,
            tonnage_kg=LEG_BALLAST.cargo_t * Decimal(1000),
            co2_emitted_kg=LEG_BALLAST.co2_emitted_t * Decimal(1000),
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_fleet_summary_end_to_end_method_a(db):
    await _seed_two_legs(db)

    summary = await fleet_summary(db, period=2026, method="A", now=datetime(2026, 7, 9, tzinfo=UTC))

    assert summary.fleet.leg_count == 2
    assert summary.fleet.laden_leg_count == 1
    assert summary.fleet.ballast_leg_count == 1
    assert summary.fleet.co2_emitted_t == Decimal("80.00")
    assert summary.fleet.ef.value_gco2_tkm == Decimal("86.39")
    # Complétude : les deux legs ont un LegKPI avec co2_emitted_kg renseigné.
    assert summary.fleet.completeness.legs_total == 2
    assert summary.fleet.completeness.legs_with_data == 2
    assert summary.fleet.completeness.legs_without_data == 0
    # selected == fleet quand aucun navire n'est filtré.
    assert summary.selected is summary.fleet


@pytest.mark.asyncio
async def test_fleet_summary_avoided_changes_when_parameter_is_edited(db):
    """Critère d'acceptation : modifier ef_container_ship_gco2_tkm change le CO2 évité affiché."""
    await _seed_two_legs(db)
    now = datetime(2026, 7, 9, tzinfo=UTC)

    before = await fleet_summary(db, period=2026, method="A", now=now)
    avoided_before = before.fleet.avoided_container.avoided_t
    assert avoided_before is not None

    db.add(
        DashboardParameter(
            parameter_name="ef_container_ship_gco2_tkm",
            vessel_id=None,
            value=Decimal("20"),
            unit="gCO2/t.km",
        )
    )
    await db.flush()

    after = await fleet_summary(db, period=2026, method="A", now=now)
    avoided_after = after.fleet.avoided_container.avoided_t

    assert avoided_after is not None
    assert avoided_after != avoided_before


@pytest.mark.asyncio
async def test_fleet_summary_method_c_is_na_end_to_end(db):
    await _seed_two_legs(db)
    summary = await fleet_summary(db, period=2026, method="C", now=datetime(2026, 7, 9, tzinfo=UTC))
    assert summary.fleet.ef.value_gco2_tkm is None
    assert summary.fleet.ef.na_reason == NA_CARGO_MRV
    assert summary.fleet.avoided_container.avoided_t is None
    assert summary.fleet.avoided_airfreight.avoided_t is None


@pytest.mark.asyncio
async def test_fleet_summary_invalid_method_raises(db):
    with pytest.raises(ValueError):
        await fleet_summary(db, period=2026, method="Z")


@pytest.mark.asyncio
async def test_fleet_summary_vessel_filter_selects_matching_block(db):
    await _seed_two_legs(db)
    now = datetime(2026, 7, 9, tzinfo=UTC)

    summary = await fleet_summary(db, period=2026, method="A", vessel_id=1, now=now)

    assert summary.selected is not summary.fleet
    assert summary.selected.vessel_id == 1
    assert len(summary.vessels) == 1
    assert summary.vessels[0].vessel_code == "ANE"
    # Le navire unique porte les deux legs — mêmes totaux que la flotte ici.
    assert summary.selected.co2_emitted_t == summary.fleet.co2_emitted_t


@pytest.mark.asyncio
async def test_fleet_summary_empty_db_returns_zeroed_summary_not_error(db):
    """Aucun leg en base : les KPI valent 0/N-A, jamais d'exception."""
    summary = await fleet_summary(db, period=2026, method="A")
    assert summary.fleet.leg_count == 0
    assert summary.fleet.co2_emitted_t == Decimal("0.00")
    assert summary.fleet.ef.value_gco2_tkm is None
    assert summary.fleet.completeness.legs_total == 0


@pytest.mark.asyncio
async def test_emissions_provider_marks_legs_without_kpi():
    """Une régression sur ``has_kpi`` casserait l'état de complétude — vérifié via fleet_summary."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        db.add(Vessel(id=1, code="ANE", name="Anemos", is_active=True))
        db.add(Port(id=1, locode="FRLEH", name="Le Havre", country="FR"))
        db.add(Port(id=2, locode="BRSSZ", name="Santos", country="BR"))
        await db.flush()
        now = datetime(2026, 7, 9, tzinfo=UTC)
        db.add(
            Leg(
                id=1,
                leg_code="1AFRBR6",
                vessel_id=1,
                departure_port_id=1,
                arrival_port_id=2,
                etd_ref=now,
                eta_ref=now,
                etd=now,
                eta=now,
                distance_nm=Decimal("1000"),
            )
        )
        await db.flush()
        # Aucun LegKPI créé pour ce leg — has_kpi doit valoir False.
        summary = await fleet_summary(db, period=2026, method="A", now=now)
        assert summary.fleet.leg_count == 1
        assert summary.fleet.completeness.legs_with_data == 0
        assert summary.fleet.completeness.legs_without_data == 1
    finally:
        await db.close()
        await engine.dispose()


# ═══════════════════════════════════════ NC-04 — mode strict (repli legacy)


@pytest.mark.asyncio
async def test_fleet_summary_strict_excludes_legacy_kpi_legs(db):
    """Aucun ``VoyageEmissionSummary`` pour ces 2 legs (source ``legacy_kpi``) :
    le mode strict doit tout exclure des totaux, jamais les compter en silence."""
    await _seed_two_legs(db)
    now = datetime(2026, 7, 9, tzinfo=UTC)

    lenient = await fleet_summary(db, period=2026, method="A", now=now, strict=False)
    assert lenient.fleet.leg_count == 2  # comportement inchangé par défaut

    strict = await fleet_summary(db, period=2026, method="A", now=now, strict=True)
    assert strict.fleet.leg_count == 0
    assert strict.fleet.co2_emitted_t == Decimal("0.00")
    assert strict.fleet.legs_excluded_non_event == 2


@pytest.mark.asyncio
async def test_fleet_summary_strict_includes_only_event_sourced_legs(db):
    """1 leg event-sourced + 1 leg en repli legacy : le mode strict ne garde
    que le premier dans les totaux, et compte le second comme exclu."""
    await _seed_two_legs(db)
    db.add(
        VoyageEmissionSummary(
            leg_id=1,
            source="events",
            co2_t=Decimal("42"),
            cargo_bl_t=Decimal("500"),
            distance_nm=Decimal("1000"),
        )
    )
    await db.flush()
    now = datetime(2026, 7, 9, tzinfo=UTC)

    strict = await fleet_summary(db, period=2026, method="A", now=now, strict=True)
    assert strict.fleet.leg_count == 1
    assert strict.fleet.co2_emitted_t == Decimal("42.00")
    assert strict.fleet.legs_excluded_non_event == 1


@pytest.mark.asyncio
async def test_vessel_operational_strict_keeps_rows_but_excludes_from_totals(db):
    """Le mode strict n'agrège que la donnée event-sourcée dans les totaux,
    mais n'escamote aucun voyage de la liste — chacun garde son ``source``."""
    await _seed_two_legs(db)
    db.add(
        VoyageEmissionSummary(
            leg_id=1,
            source="events",
            co2_t=Decimal("42"),
            cargo_bl_t=Decimal("500"),
            distance_nm=Decimal("1000"),
            conso_total_t=Decimal("5"),
        )
    )
    await db.flush()

    lenient = await vessel_operational(db, 1, period=2026, method="A", strict=False)
    assert len(lenient.voyages) == 2
    assert lenient.leg_count == 2  # comportement inchangé par défaut

    strict = await vessel_operational(db, 1, period=2026, method="A", strict=True)
    assert len(strict.voyages) == 2  # aucun voyage caché
    assert {r.source for r in strict.voyages} == {"events", "legacy_kpi"}
    assert strict.leg_count == 1
    assert strict.co2_total_t == Decimal("42.00")
    assert strict.excluded_non_event_count == 1
