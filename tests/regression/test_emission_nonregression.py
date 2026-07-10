"""SUITE GELÉE LOT 9 — non-régression des chiffres publics d'émission.

⚠ **SUITE GELÉE (lot 9 — grand livre d'émissions unifié) : toute modification
de ce module exige une justification d'architecte.** Ces tests verrouillent les
chiffres publics/persistés AVANT/APRÈS le rebranchement des consommateurs sur
``services.emission_ledger`` (plan §4 « non-régression verrouillée ») ; ils
sont rejoués à chaque lot suivant.

Les constantes attendues sont FIGÉES : elles ont été calculées avec la logique
d'AVANT le lot 9 (``services.carbon`` historique, ``co2.estimate``,
``quoting.compute_route_economics``, valeurs CFOTE_05/CFOTE_09 de référence de
``tests/unit/test_carbon.py`` et ``tests/unit/test_report_generation.py``) —
elles ne se recalculent jamais depuis le code sous test.

Couverture (lettres du plan lot 9) :

- (a) prix d'un devis booking via ``quoting``/``resolve_distance_nm`` inchangé ;
- (b) CO₂ évité d'un certificat émis = valeur stockée relue (jamais
  recalculée) + le template PDF privilégie ``certificate.*`` ;
- (c) compteur landing (``social_proof``) = Σ ``co2_avoided_kg`` des
  certificats ;
- (d) ``LegKPI`` d'un leg legacy : mêmes valeurs qu'avant rebranchement
  (constantes figées) ;
- (e) intensités CFOTE_09 : mêmes valeurs que ``test_carbon.py`` ;
- (f) leg à événements : ledger == valeurs CFOTE_05 de référence
  (4,43625 t DO → 14,2226175 tCO₂…) ;
- (g) ``source`` correct dans les 2 modes (``events`` / ``legacy_noon``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — enregistre tous les modèles sur Base.metadata
from app.database import Base
from app.models.anemos_certificate import AnemosCertificate
from app.models.booking import Booking
from app.models.client_account import ClientAccount
from app.models.leg import Leg
from app.models.nav_event import (
    ArrivalEvent,
    DepartureEvent,
    NavEventEngineReading,
    NoonEvent,
)
from app.models.noon_report import NoonReport
from app.models.port import Port
from app.models.vessel import Vessel
from app.models.voyage_emission_summary import VoyageEmissionSummary
from app.services import referential_env, social_proof
from app.services.co2 import invalidate_factors_cache
from app.services.validation_engine import invalidate_cache, seed_reference_data

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

# ═══════════════════════════ CONSTANTES FIGÉES (logique d'avant le lot 9) ═══
# NE PAS RECALCULER depuis le code sous test — cf. docstring module.

# (a) Devis FRFEC↔USNYC (table de ports 3200 NM, OPEX repli 12 000 €/j,
#     8 kn × 24 h, capacité 978 palettes) — quoting.compute_route_economics.
FROZEN_QUOTE_DISTANCE_NM = Decimal("3200.00")
FROZEN_QUOTE_NAV_DAYS = Decimal("16.667")
FROZEN_QUOTE_OPEX_DAILY = Decimal("12000")
FROZEN_QUOTE_BASE_RATE = Decimal("204.50")

# (d)/(e) Leg legacy « test_carbon » : 2 noon (1,3 + 0,7 t), cargo 100 t,
#     distance 200 NM, facteur MEPC 3,206 — valeurs de tests/unit/test_carbon.py.
FROZEN_LEGACY_DO_T = Decimal("2.000")
FROZEN_LEGACY_CO2_T = Decimal("6.412")
FROZEN_LEGACY_CO2_PER_NM_KG = Decimal("32.060")
FROZEN_LEGACY_CO2_PER_T_KG = Decimal("64.120")
FROZEN_LEGACY_CO2_PER_TNM_G = Decimal("320.600")
FROZEN_DO_CO2_FACTOR = Decimal("3.206")
# co2.estimate (1,5 / 13,7 g/t·km, NM→km 1,852) sur 200 NM × 100 t :
# km 370,40 ; t·km 37 040 ; towt 55,560 kg ; conv 507,448 kg ; évité 451,888 kg.
FROZEN_LEGACY_AVOIDED_KG = Decimal("451.888")
FROZEN_LEGACY_KPI_CO2_EMITTED_KG = Decimal("6412.00")
FROZEN_LEGACY_KPI_AVOIDED_KG = Decimal("451.89")

# (f) Chaîne CFOTE_05 de référence (Dep + 2 Noon + Arr, densité 0,845) —
#     valeurs de tests/unit/test_report_generation.py (multi-GES exact).
FROZEN_EVENTS_CONSO_TOTAL_T = Decimal("4.43625")
FROZEN_EVENTS_CONSO_ME_T = Decimal("3.380")
FROZEN_EVENTS_CONSO_AE_T = Decimal("1.05625")
FROZEN_EVENTS_CO2_T = Decimal("4.43625") * Decimal("3.206")  # = 14,2226175 t
FROZEN_EVENTS_CH4_G = Decimal("4.43625") * Decimal("0.00005") * Decimal("1000000")
FROZEN_EVENTS_N2O_G = Decimal("4.43625") * Decimal("0.00018") * Decimal("1000000")
# WtT = conso × PCI (42 700 MJ/t) × 17,7 gCO₂eq/MJ / 1e6.
FROZEN_EVENTS_WTT_T = Decimal("4.43625") * Decimal("42700") * Decimal("17.7") / Decimal("1000000")

DEP_FUEL = {
    "PME": 10000,
    "SME": 8000,
    "FWD_GEN": 5000,
    "AFT_GEN": 4000,
    "PORT_SHAFT_GEN": 2000,
    "STBD_SHAFT_GEN": 2000,
}
N1_FUEL = {
    "PME": 11000,
    "SME": 8600,
    "FWD_GEN": 5300,
    "AFT_GEN": 4200,
    "PORT_SHAFT_GEN": 2100,
    "STBD_SHAFT_GEN": 2100,
}
N2_FUEL = {
    "PME": 12000,
    "SME": 9200,
    "FWD_GEN": 5600,
    "AFT_GEN": 4400,
    "PORT_SHAFT_GEN": 2200,
    "STBD_SHAFT_GEN": 2200,
}
ARR_FUEL = {
    "PME": 12500,
    "SME": 9500,
    "FWD_GEN": 5750,
    "AFT_GEN": 4500,
    "PORT_SHAFT_GEN": 2250,
    "STBD_SHAFT_GEN": 2250,
}


def _reset_module_caches() -> None:
    """Purge tous les caches module-level touchés par la suite (isolation)."""
    invalidate_cache()
    invalidate_factors_cache()
    referential_env.invalidate_emission_factor_cache()
    social_proof.invalidate_counters_cache()


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _rec):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    _reset_module_caches()
    await seed_reference_data(session)
    invalidate_cache()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()
        _reset_module_caches()


async def _legacy_leg(db, *, with_booking: bool = True):
    """Leg legacy « test_carbon » : 2 noon reports (1,3 + 0,7 t), 200 NM.

    Aucun ``nav_event`` : le grand livre DOIT retomber sur ``legacy_noon``.
    Ports sans coordonnées (paire FRFEC/USNYC de la table historique) —
    réutilisés par le test devis (a).
    """
    vessel = Vessel(code="ANE", name="Anemos")
    db.add(vessel)
    p1 = Port(name="Fecamp", locode="FRFEC", country="FR")
    p2 = Port(name="New York", locode="USNYC", country="US")
    db.add_all([p1, p2])
    await db.flush()
    leg = Leg(
        leg_code="1AFRUS6",
        vessel_id=vessel.id,
        departure_port_id=p1.id,
        arrival_port_id=p2.id,
        etd_ref=T0,
        eta_ref=T0 + timedelta(days=20),
        etd=T0,
        eta=T0 + timedelta(days=20),
        distance_nm=Decimal("200"),
    )
    db.add(leg)
    await db.flush()
    db.add(
        NoonReport(leg_id=leg.id, recorded_at=T0, latitude=0, longitude=0, total_consumption_t=1.3)
    )
    db.add(
        NoonReport(
            leg_id=leg.id,
            recorded_at=T0 + timedelta(days=1),
            latitude=0,
            longitude=0,
            total_consumption_t=0.7,
        )
    )
    booking = None
    if with_booking:
        booking = Booking(
            reference="TUAW_REG1",
            leg_id=leg.id,
            status="confirmed",
            total_palettes=10,
            total_weight_kg=Decimal("100000"),
        )
        db.add(booking)
    await db.flush()
    return vessel, leg, booking


async def _events_leg(db):
    """Chaîne CFOTE_05 de référence — même jeu que test_report_generation."""
    from app.services.referential_env import ensure_vessel_env_defaults, get_vessel_engines

    vessel = Vessel(code="ART", name="Artemis")
    db.add(vessel)
    await db.flush()
    await ensure_vessel_env_defaults(db, vessel)
    engines = {e.engine_role: e for e in await get_vessel_engines(db, vessel.id)}
    p1 = Port(name="Le Havre", locode="FRLEH", country="FR", latitude=49.49, longitude=0.11)
    p2 = Port(name="Belem", locode="BRBEL", country="BR", latitude=-1.45, longitude=-48.5)
    db.add_all([p1, p2])
    await db.flush()
    leg = Leg(
        leg_code="2AFRBR6",
        vessel_id=vessel.id,
        departure_port_id=p1.id,
        arrival_port_id=p2.id,
        etd_ref=T0,
        eta_ref=T0 + timedelta(days=5),
        etd=T0,
        eta=T0 + timedelta(days=5),
    )
    db.add(leg)
    await db.flush()

    def _readings(fuel_map):
        return [
            NavEventEngineReading(
                engine_id=engines[role].id,
                fuel_counter_l=Decimal(str(fuel)),
                is_counter_reset=False,
            )
            for role, fuel in fuel_map.items()
        ]

    dep = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0,
        lat_decimal=Decimal("50.0"),
        lon_decimal=Decimal("-5.0"),
        rob_t=Decimal("100.000"),
        vessel_condition="laden",
        cargo_bl_t=Decimal("900.000"),
        cargo_mrv_t=Decimal("950.000"),
    )
    dep.engine_readings = _readings(DEP_FUEL)
    n1 = NoonEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=24),
        lat_decimal=Decimal("47.0"),
        lon_decimal=Decimal("-5.0"),
    )
    n1.engine_readings = _readings(N1_FUEL)
    n2 = NoonEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=48),
        lat_decimal=Decimal("44.0"),
        lon_decimal=Decimal("-5.0"),
    )
    n2.engine_readings = _readings(N2_FUEL)
    arr = ArrivalEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=60),
        lat_decimal=Decimal("42.0"),
        lon_decimal=Decimal("-5.0"),
        rob_t=Decimal("95.564"),
        vessel_condition="laden",
    )
    arr.engine_readings = _readings(ARR_FUEL)
    db.add_all([dep, n1, n2, arr])
    await db.flush()
    return vessel, leg


# ═══════════════════════════════════ (a) Prix d'un devis booking (quoting)


async def test_a_quote_price_unchanged(db):
    """Le pricing booking (quoting → resolve_distance_nm) rend les valeurs figées."""
    from app.services.quoting import compute_route_economics

    p1 = Port(name="Fecamp", locode="FRFEC", country="FR")
    p2 = Port(name="New York", locode="USNYC", country="US")
    db.add_all([p1, p2])
    await db.flush()

    distance, nav_days, opex_daily, base_rate = await compute_route_economics(
        db, pol_locode="FRFEC", pod_locode="USNYC"
    )
    assert distance == FROZEN_QUOTE_DISTANCE_NM
    assert nav_days == FROZEN_QUOTE_NAV_DAYS
    assert opex_daily == FROZEN_QUOTE_OPEX_DAILY
    assert base_rate == FROZEN_QUOTE_BASE_RATE


async def test_a_resolve_distance_sources_unchanged(db):
    """``resolve_distance_with_source`` : chaîne persistée → table intouchée."""
    from app.services.anemos import resolve_distance_with_source

    _vessel, leg, _booking = await _legacy_leg(db, with_booking=False)
    pol = await db.get(Port, leg.departure_port_id)
    pod = await db.get(Port, leg.arrival_port_id)

    # 1. Distance persistée sur le leg → prioritaire.
    dist, source = resolve_distance_with_source(leg, pol, pod)
    assert (dist, source) == (Decimal("200"), "leg_persisted")
    # 2. Sans leg, ports sans coordonnées mais paire connue → table historique.
    dist, source = resolve_distance_with_source(None, pol, pod)
    assert (dist, source) == (Decimal("3200"), "port_table")


# ═══════════════ (b) Certificat émis : jamais recalculé (record stocké relu)


async def test_b_issued_certificate_is_never_recalculated(db):
    from app.services.anemos import issue_for_booking

    _vessel, leg, booking = await _legacy_leg(db)
    client = ClientAccount(email="client@reg.test", hashed_password="x", company_name="Reg Co")
    db.add(client)
    await db.flush()
    booking.client_account_id = client.id
    booking.status = "delivered"

    # Certificat DÉJÀ émis, valeurs arbitraires figées ≠ de tout recalcul possible.
    stored = AnemosCertificate(
        reference=f"ANEMOS-{booking.reference}",
        booking_id=booking.id,
        client_account_id=client.id,
        leg_id=leg.id,
        tonnage_transported_t=Decimal("77.000"),
        distance_nm=Decimal("1234.56"),
        co2_emitted_kg=Decimal("111.111"),
        co2_conventional_kg=Decimal("999.999"),
        co2_avoided_kg=Decimal("888.888"),
        method="declared",
        distance_source="noon_reports",
    )
    db.add(stored)
    await db.flush()

    # Les données du leg donneraient d'autres chiffres — le record stocké prime.
    cert = await issue_for_booking(db, booking)
    assert cert.id == stored.id
    assert cert.co2_avoided_kg == Decimal("888.888")
    assert cert.co2_emitted_kg == Decimal("111.111")
    assert cert.co2_conventional_kg == Decimal("999.999")
    assert cert.distance_nm == Decimal("1234.56")
    assert cert.method == "declared"
    count = (await db.execute(select(func.count()).select_from(AnemosCertificate))).scalar_one()
    assert count == 1


def test_b_certificate_template_prefers_stored_record():
    """Le template PDF privilégie ``certificate.*`` (le recalcul n'est qu'un fallback)."""
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "pdf/anemos_certificate.html")[0]
    assert "certificate.co2_avoided_kg if certificate" in src
    assert "certificate.co2_emitted_kg if certificate" in src
    assert "certificate.distance_nm if certificate" in src


# ═══════════════════ (c) Compteur landing = Σ co2_avoided_kg des certificats


async def test_c_landing_counter_is_sum_of_certificates(db):
    client = ClientAccount(email="client2@reg.test", hashed_password="x", company_name="Reg Co 2")
    db.add(client)
    await db.flush()
    db.add(
        AnemosCertificate(
            reference="ANEMOS-REG-A",
            client_account_id=client.id,
            tonnage_transported_t=Decimal("10"),
            distance_nm=Decimal("100"),
            co2_emitted_kg=Decimal("1"),
            co2_conventional_kg=Decimal("2"),
            co2_avoided_kg=Decimal("1000.500"),
        )
    )
    db.add(
        AnemosCertificate(
            reference="ANEMOS-REG-B",
            client_account_id=client.id,
            tonnage_transported_t=Decimal("20"),
            distance_nm=Decimal("200"),
            co2_emitted_kg=Decimal("2"),
            co2_conventional_kg=Decimal("4"),
            co2_avoided_kg=Decimal("2000.250"),
        )
    )
    await db.flush()

    social_proof.invalidate_counters_cache()
    counters = await social_proof.counters(db)
    # Σ = 3000,75 kg → int (arrondi plancher historique du compteur).
    assert counters.co2_avoided_kg == 3000
    social_proof.invalidate_counters_cache()


# ═══════════ (d) LegKPI d'un leg legacy : mêmes valeurs avant/après lot 9


async def test_d_legacy_legkpi_values_frozen(db):
    from app.services.kpi import compute_for_leg as kpi_compute

    _vessel, leg, _booking = await _legacy_leg(db)
    kpi = await kpi_compute(db, leg)

    assert kpi.do_consumed_t == FROZEN_LEGACY_DO_T
    assert kpi.co2_emitted_kg == FROZEN_LEGACY_KPI_CO2_EMITTED_KG
    assert kpi.co2_per_nm_kg == FROZEN_LEGACY_CO2_PER_NM_KG
    assert kpi.co2_per_t_kg == FROZEN_LEGACY_CO2_PER_T_KG
    assert kpi.co2_per_tnm_g == FROZEN_LEGACY_CO2_PER_TNM_G
    assert kpi.co2_avoided_kg == FROZEN_LEGACY_KPI_AVOIDED_KG
    assert kpi.tonnage_kg == Decimal("100000")
    assert kpi.distance_nm == Decimal("200")


# ═══════════════ (e) Intensités CFOTE_09 : identiques à test_carbon (gelées)


async def test_e_carbon_intensities_frozen(db):
    from app.services.carbon import compute_carbon_for_leg

    _vessel, leg, _booking = await _legacy_leg(db, with_booking=False)
    r = await compute_carbon_for_leg(db, leg, cargo_t=Decimal("100"), distance_nm=Decimal("200"))
    assert r.do_consumed_t == FROZEN_LEGACY_DO_T
    assert r.co2_emitted_t == FROZEN_LEGACY_CO2_T
    assert r.co2_per_nm_kg == FROZEN_LEGACY_CO2_PER_NM_KG
    assert r.co2_per_t_kg == FROZEN_LEGACY_CO2_PER_T_KG
    assert r.co2_per_tnm_g == FROZEN_LEGACY_CO2_PER_TNM_G
    assert r.do_co2_factor == FROZEN_DO_CO2_FACTOR
    assert r.avoided_co2_kg == FROZEN_LEGACY_AVOIDED_KG


# ═══════════ (f) Leg à événements : ledger == valeurs CFOTE_05 de référence


async def test_f_events_ledger_matches_cfote05_reference(db):
    from app.services.emission_ledger import compute_for_leg as ledger_compute

    _vessel, leg = await _events_leg(db)
    r = await ledger_compute(db, leg)

    assert r.conso_total_t == FROZEN_EVENTS_CONSO_TOTAL_T
    assert r.conso_me_t == FROZEN_EVENTS_CONSO_ME_T
    assert r.conso_ae_t == FROZEN_EVENTS_CONSO_AE_T
    assert r.conso_mouillage_t == Decimal("0")
    assert r.conso_hors_mouillage_t == FROZEN_EVENTS_CONSO_TOTAL_T
    # Multi-GES exact : CO₂ TtW (t), CH₄/N₂O (g), WtT distinct (tCO₂eq).
    assert r.co2_emitted_t == FROZEN_EVENTS_CO2_T
    assert r.co2_emitted_t.quantize(Decimal("0.0001")) == Decimal("14.2226")
    assert r.ch4_g == FROZEN_EVENTS_CH4_G
    assert r.n2o_g == FROZEN_EVENTS_N2O_G
    assert r.wtt_co2eq_t == FROZEN_EVENTS_WTT_T
    # Cargo B/L ≠ cargo MRV, tous deux portés par le Departure.
    assert r.cargo_bl_t == Decimal("900.000")
    assert r.cargo_mrv_t == Decimal("950.000")
    # Méthode C réelle (cargo MRV disponible via les événements).
    assert r.ef_method_c is not None
    assert r.do_co2_factor == FROZEN_DO_CO2_FACTOR


# ═══════════════ (g) ``source`` correct dans les 2 modes + summary conforme


async def test_g_source_flag_and_summary_both_modes(db):
    from app.services.emission_ledger import compute_for_leg as ledger_compute
    from app.services.emission_ledger import refresh_summary

    _v1, legacy_leg, _b = await _legacy_leg(db)
    _v2, events_leg = await _events_leg(db)

    legacy = await ledger_compute(db, legacy_leg)
    events = await ledger_compute(db, events_leg)
    assert legacy.source == "legacy_noon"
    assert events.source == "events"

    s_legacy = await refresh_summary(db, legacy_leg)
    s_events = await refresh_summary(db, events_leg)
    assert s_legacy.source == "legacy_noon"
    assert s_events.source == "events"
    # Le summary matérialise fidèlement le ledger (cache, jamais source de vérité).
    assert s_legacy.co2_t == legacy.co2_emitted_t
    assert s_events.co2_t == events.co2_emitted_t
    rows = (await db.execute(select(func.count()).select_from(VoyageEmissionSummary))).scalar_one()
    assert rows == 2
