"""Grand livre d'émissions unifié (MRV lot 9) — l'UNIQUE moteur de calcul.

**Règle d'or (plan §2.4).** Après ce lot, aucun autre service ne multiplie une
consommation par un facteur d'émission : ``emission_ledger`` est la seule
implémentation des formules d'émission. ``services.co2.estimate`` (forfait
théorique 1,5/13,7 — devis + comparateur conventionnel) et
``services.emissions`` (NOx/SOx) restent les **comparateurs officiels** — ils ne
calculent pas d'émission réelle de carburant.

Le grand livre :

- lit la **source EVENTS** (``nav_events`` finalisés/validés via
  ``inter_event_compute.compute_leg`` + soutages réels
  ``bunkering``) quand le voyage en a, sinon **repli ``legacy_noon``**
  (agrégats des ``noon_reports`` — chiffres IDENTIQUES à l'ancien
  ``services.carbon``) ; ``source`` porte l'origine ;
- calcule le **multi-GES** (CO₂ TtW, CH₄/N₂O en grammes, WtT FuelEU distinct)
  sur l'assiette **hors mouillage** — la fonction ``emissions_breakdown`` est la
  logique déplacée de ``report_generation._compute_emissions`` (déplacée, pas
  dupliquée : ``report_generation`` l'appelle désormais) ;
- expose les périmètres (total / hors mouillage / mouillage / escale), les
  intensités et les 3 méthodes d'EF (A réel cargo B/L, B standardisé
  capacité×occupancy, C cargo MRV — réelle dès que ``cargo_mrv`` est
  disponible via les événements, sinon N/A) ;
- calcule le **CO₂ évité** avec les comparateurs ``co2_variables`` existants
  (``co2.estimate`` — mêmes valeurs que pour un même leg) ;
- se **matérialise** dans ``voyage_emission_summaries`` (``refresh_summary``,
  idempotent) — un cache recalculable, jamais source de vérité.

Les consommateurs (``carbon.compute_carbon_for_leg`` adaptateur,
``anemos.issue_for_booking`` branche declared, ``kpi_env._emissions_provider``,
``report_generation``) sont rebranchés dessus, à interfaces conservées.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.bunker import BunkerOperation
from app.models.emission_factor import EmissionFactor
from app.models.leg import Leg
from app.models.nav_event import ArrivalEvent, DepartureEvent, NavEvent, PortCallEvent
from app.models.noon_report import NoonReport, NoonReportEngine
from app.models.validation import DashboardParameter
from app.models.vessel import Vessel
from app.models.voyage_emission_summary import VoyageEmissionSummary
from app.services import inter_event_compute as iec
from app.services import referential_env
from app.services.co2 import NM_TO_KM
from app.services.referential_env import ResolvedEmissionFactor

# ════════════════════════════════════════════════════════════ Constantes

_MILLION = Decimal("1000000")

# Pouvoir calorifique inférieur du MDO (MJ/t ≡ 42,7 MJ/kg) — base énergie du
# WtT FuelEU. **Déplacé depuis ``report_generation`` (lot 9)** : c'est ici que
# vit désormais la constante consommée par ``emissions_breakdown``.
MDO_LHV_MJ_PER_T = Decimal("42700")

# Statuts de booking comptant dans la cargaison d'un leg (repli legacy_noon) —
# aligné sur ``services.carbon`` d'origine.
_ACTIVE_BOOKING = ("confirmed", "loaded", "at_sea", "discharged", "delivered")

# Méthodes d'EF (spec dashboard §5.1) — jamais mélangées.
EF_METHODS: tuple[str, ...] = ("A", "B", "C")

# Défauts des paramètres dashboard consommés par la méthode B (miroir de
# ``kpi_env.DASHBOARD_PARAM_DEFAULTS`` — non importés pour éviter un cycle
# d'import ``kpi_env`` ↔ ``emission_ledger``).
_OCCUPANCY_DEFAULT = Decimal("70")
_CAPACITY_REF_DEFAULT = Decimal("1100")

_EF_QUANT = Decimal("0.0001")  # gCO₂/t·km (méthodes A/B/C matérialisées)

# GWP-100 (Annexe I, règlement EU 2015/757) — CH₄ = 25, N₂O = 298 (G13).
# Constantes réglementaires stables (pas des seuils métier à calibrer par
# voyage pilote, contrairement à ``ValidationRuleThreshold``) — même posture
# que ``MDO_LHV_MJ_PER_T`` ci-dessus : à réviser uniquement si le règlement
# change son horizon GWP, pas un paramètre par carburant (``EmissionFactor``).
GWP_CH4 = Decimal("25")
GWP_N2O = Decimal("298")


def _num(value: Decimal | int | float | None) -> str | None:
    """Decimal → str (précision préservée, JSON-safe)."""
    return None if value is None else str(value)


# ════════════════════════════════ Émissions multi-GES (déplacé de report_generation)


def emissions_breakdown(conso_t: Decimal | None, factor: ResolvedEmissionFactor) -> dict[str, Any]:
    """Émissions multi-GES d'une assiette de consommation (CFOTE_09).

    ⚠ **Unique implémentation** de la multiplication conso × facteur (règle
    d'or). Déplacée de ``report_generation._compute_emissions`` — ``report_
    generation`` appelle désormais cette fonction (payload IDENTIQUE).

    - CO₂ (TtW) [t] = ``conso_t × ef_co2`` (facteur sans dimension g/g ≡ t/t) ;
    - CH₄ / N₂O [g] = ``conso_t × ef × 1e6`` (tonnes de GES → grammes) ;
    - WtT (Well-to-Tank, FuelEU) = ``conso_t × PCI × wtt_gco2eq_per_mj / 1e6`` —
      **distinct du TtW, jamais sommé** au CO₂ TtW sans l'expliciter ;
    - CO₂eq (GWP-100, tank-to-wake, G13) = ``conso_t × (ef_co2 + ef_ch4 × 25 +
      ef_n2o × 298)`` (Annexe I, EU 2015/757) — additionne les 3 GES TtW en
      équivalent CO₂ ; **distinct du WtT** (qui reste hors périmètre TtW).
    """
    wtt_intensity = factor.wtt_gco2eq_per_mj
    if conso_t is None:
        return {
            "conso_t": None,
            "co2_t": None,
            "ch4_g": None,
            "n2o_g": None,
            "co2eq_t": None,
            "wtt_gco2eq_per_mj": _num(wtt_intensity),
            "wtt_co2eq_t": None,
            "ef_co2_kg_per_kg": _num(factor.ef_co2_kg_per_kg),
            "ef_ch4_kg_per_kg": _num(factor.ef_ch4_kg_per_kg),
            "ef_n2o_kg_per_kg": _num(factor.ef_n2o_kg_per_kg),
        }
    co2_t = conso_t * factor.ef_co2_kg_per_kg
    ch4_g = conso_t * factor.ef_ch4_kg_per_kg * _MILLION
    n2o_g = conso_t * factor.ef_n2o_kg_per_kg * _MILLION
    # WtT : énergie du carburant × intensité amont, converti g → t. DISTINCT.
    wtt_co2eq_t = conso_t * MDO_LHV_MJ_PER_T * wtt_intensity / _MILLION
    # CO₂eq TtW (GWP-100) : les 3 GES en équivalent CO₂, DISTINCT du WtT.
    co2eq_kg_per_kg = (
        factor.ef_co2_kg_per_kg
        + factor.ef_ch4_kg_per_kg * GWP_CH4
        + factor.ef_n2o_kg_per_kg * GWP_N2O
    )
    co2eq_t = conso_t * co2eq_kg_per_kg
    return {
        "conso_t": _num(conso_t),
        "co2_t": _num(co2_t),
        "ch4_g": _num(ch4_g),
        "n2o_g": _num(n2o_g),
        "co2eq_t": _num(co2eq_t),
        "wtt_gco2eq_per_mj": _num(wtt_intensity),
        "wtt_co2eq_t": _num(wtt_co2eq_t),
        "ef_co2_kg_per_kg": _num(factor.ef_co2_kg_per_kg),
        "ef_ch4_kg_per_kg": _num(factor.ef_ch4_kg_per_kg),
        "ef_n2o_kg_per_kg": _num(factor.ef_n2o_kg_per_kg),
    }


# ════════════════════════════════════════════════════════════ Résultat typé


@dataclass(frozen=True)
class LedgerResult:
    """Vue consolidée du grand livre pour un voyage (unités : plan §2.7).

    ``source`` ∈ {``events``, ``legacy_noon``}. Les consommations sont en
    tonnes ; ``co2_emitted_t`` est **brut** (non arrondi — l'adaptateur carbone
    applique ses propres arrondis) ; ``ch4_g``/``n2o_g`` en grammes ;
    ``co2eq_t`` (GWP-100 tank-to-wake, G13) et ``wtt_co2eq_t`` (Well-to-Tank)
    sont deux grandeurs **distinctes**, jamais sommées entre elles. Les
    méthodes EF sont en gCO₂/t·km (None si N/A). ``do_consumed_t`` est
    l'assiette d'émission (legacy : total noon ; events : hors mouillage).
    """

    leg_id: int
    source: str

    # Consommations par périmètre (tonnes).
    conso_me_t: Decimal | None
    conso_ae_t: Decimal | None
    conso_total_t: Decimal | None
    conso_mouillage_t: Decimal | None
    conso_hors_mouillage_t: Decimal | None
    conso_escale_t: Decimal | None

    # Assiette d'émission (legacy : total noon ; events : hors mouillage).
    do_consumed_t: Decimal | None

    # Distance / cargo.
    distance_nm: Decimal | None
    cargo_bl_t: Decimal | None
    cargo_mrv_t: Decimal | None

    # Facteur appliqué (multi-GES) + son composant CO₂ (pour l'adaptateur carbone).
    factor: ResolvedEmissionFactor
    do_co2_factor: Decimal

    # Émissions multi-GES (brutes) + le dict sérialisé (report_generation).
    emissions: dict[str, Any]
    co2_emitted_t: Decimal | None
    ch4_g: Decimal | None
    n2o_g: Decimal | None
    co2eq_t: Decimal | None  # GWP-100 tank-to-wake (G13) — DISTINCT du WtT.
    wtt_co2eq_t: Decimal | None

    # CO₂ évité (kg) vs comparateur conventionnel (``co2.estimate``).
    avoided_co2_kg: Decimal | None

    # Facteur d'émission par méthode (gCO₂/t·km) — None si N/A.
    ef_method_a: Decimal | None
    ef_method_b: Decimal | None
    ef_method_c: Decimal | None


# ════════════════════════════════════════════════════════════ Helpers datetime


def _naive_utc(dt: datetime | None) -> datetime | None:
    """Ramène un datetime en naïf-UTC (compare uniformément PG aware / SQLite naïf)."""
    if dt is None:
        return None
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo is not None else dt


# ════════════════════════════════════════════════════════════ Soutages (ROB)


async def build_bunker_lookup(db: AsyncSession, leg_id: int) -> iec.BunkerLookup:
    """Ferme une fonction ``(from, to] → tonnes soutées`` sur les soutages
    **validés Master** du voyage (ROB chaîné, lot 6). Source réelle des
    soutages injectée dans ``inter_event_compute.compute_leg``."""
    rows = (
        await db.execute(
            select(BunkerOperation.delivery_datetime_utc, BunkerOperation.mass_t)
            .where(BunkerOperation.leg_id == leg_id)
            .where(BunkerOperation.status == "valide_master")
        )
    ).all()
    ops = [(_naive_utc(dt), m) for dt, m in rows if dt is not None and m is not None]

    def lookup(frm: datetime, to: datetime) -> Decimal:
        f, t = _naive_utc(frm), _naive_utc(to)
        return sum((m for (dt, m) in ops if dt is not None and f < dt <= t), Decimal("0"))

    return lookup


# ════════════════════════════════════════════════════ Conso d'escale (G12)


async def _next_departure_event(
    db: AsyncSession, vessel_id: int, after: datetime
) -> NavEvent | None:
    """Premier Departure finalisé du navire après ``after`` (chronologique,
    tous legs confondus) — borne de fin de l'escale qui suit une Arrival."""
    after_naive = _naive_utc(after)
    rows = await db.execute(
        select(NavEvent)
        .where(
            NavEvent.vessel_id == vessel_id,
            NavEvent.event_type == "departure",
            NavEvent.status.in_(iec.FINALIZED_STATUSES),
        )
        .order_by(NavEvent.datetime_utc.asc())
    )
    for ev in rows.scalars().all():
        if _naive_utc(ev.datetime_utc) is not None and _naive_utc(ev.datetime_utc) > after_naive:
            return ev
    return None


async def _bunkered_t_between(
    db: AsyncSession, vessel_id: int, frm: datetime | None, to: datetime | None
) -> Decimal:
    """Soutages validés Master du navire livrés dans la fenêtre (``frm``, ``to``]."""
    rows = (
        await db.execute(
            select(BunkerOperation.delivery_datetime_utc, BunkerOperation.mass_t).where(
                BunkerOperation.vessel_id == vessel_id,
                BunkerOperation.status == "valide_master",
            )
        )
    ).all()
    f, t = _naive_utc(frm), _naive_utc(to)
    total = Decimal("0")
    for dt, mass in rows:
        d = _naive_utc(dt)
        if d is not None and mass is not None and f is not None and t is not None and f < d <= t:
            total += mass
    return total


async def _escale_consumption(
    db: AsyncSession, vessel_id: int, arrival: NavEvent
) -> Decimal | None:
    """Conso d'escale — formule R14b résolue pour ``Consommation_escale``
    (architecture §2.4, second usage de la formule de continuité ROB) : port
    stay qui suit l'Arrival d'un leg jusqu'au prochain Departure finalisé du
    même navire.

    ``Consommation_escale = ROB_arrivée + Σ soutage − ROB_départ`` — méthode
    du dashboard tant que la méthode par compteurs n'est pas systématiquement
    peuplée en prod (repli sur le delta de compteurs moteur, désormais fiable
    depuis G2, si le ROB déclaré manque à l'une des deux bornes). ``None`` si
    le prochain Departure n'est pas encore finalisé (escale en cours) ou si
    aucune des deux méthodes n'est calculable."""
    departure = await _next_departure_event(db, vessel_id, arrival.datetime_utc)
    if departure is None:
        return None

    rob_arrival = arrival.rob_t if isinstance(arrival, PortCallEvent) else None
    rob_departure = departure.rob_t if isinstance(departure, PortCallEvent) else None
    if rob_arrival is not None and rob_departure is not None:
        bunkered_t = await _bunkered_t_between(
            db, vessel_id, arrival.datetime_utc, departure.datetime_utc
        )
        return Decimal(rob_arrival) + bunkered_t - Decimal(rob_departure)

    engines = {e.id: e for e in await referential_env.get_vessel_engines(db, vessel_id)}
    density = await iec.resolve_density(db, vessel_id)
    interval = iec.compute_interval(arrival, departure, engines, density)
    return interval.total_conso_t


# ════════════════════════════════════════════════════ Repli legacy (noon_reports)


async def _legacy_do_consumed_t(db: AsyncSession, leg_id: int) -> Decimal:
    """Consommation DO totale (t) agrégée depuis les noon reports du leg.

    Logique IDENTIQUE à l'ancien ``services.carbon._do_consumed_t`` (repli
    ``legacy_noon`` — chiffres inchangés) : ``total_consumption_t`` par report,
    sinon somme des ``do_consumption_t`` de ses moteurs.
    """
    reports = list(
        (await db.execute(select(NoonReport).where(NoonReport.leg_id == leg_id))).scalars().all()
    )
    total = Decimal("0")
    for nr in reports:
        if nr.total_consumption_t is not None:
            total += Decimal(str(nr.total_consumption_t))
            continue
        engine_sum = (
            (
                await db.execute(
                    select(NoonReportEngine).where(NoonReportEngine.noon_report_id == nr.id)
                )
            )
            .scalars()
            .all()
        )
        total += sum(
            (
                Decimal(str(e.do_consumption_t))
                for e in engine_sum
                if e.do_consumption_t is not None
            ),
            Decimal("0"),
        )
    return total


async def _legacy_cargo_t(db: AsyncSession, leg_id: int) -> Decimal:
    """Cargo (t) depuis les bookings actifs — IDENTIQUE à ``carbon._cargo_t``."""
    bookings = list(
        (
            await db.execute(
                select(Booking).where(Booking.leg_id == leg_id, Booking.status.in_(_ACTIVE_BOOKING))
            )
        )
        .scalars()
        .all()
    )
    total_kg = sum((b.total_weight_kg or Decimal(0)) for b in bookings)
    return (Decimal(str(total_kg)) / Decimal("1000")).quantize(Decimal("0.001"))


# ════════════════════════════════════════════════════ Mouillages (assiette MRV)


def _anchoring_windows(events: list[NavEvent]) -> list[tuple[datetime, datetime]]:
    by_id = {e.id: e for e in events}
    windows: list[tuple[datetime, datetime]] = []
    for pair in iec.pair_anchorings(events):
        begin = by_id.get(pair.begin_event_id)
        end = by_id.get(pair.end_event_id)
        if begin is None or end is None:
            continue
        bf, ef = _naive_utc(begin.datetime_utc), _naive_utc(end.datetime_utc)
        if bf is not None and ef is not None:
            windows.append((bf, ef))
    return windows


def _interval_in_anchoring(
    interval: iec.IntervalResult, windows: list[tuple[datetime, datetime]]
) -> bool:
    f, t = _naive_utc(interval.from_dt), _naive_utc(interval.to_dt)
    if f is None or t is None:
        return False
    return any(wf <= f and t <= wt for (wf, wt) in windows)


# ════════════════════════════════════════════════════════════ Paramètres B


async def _dashboard_param(db: AsyncSession, name: str, default: Decimal) -> Decimal:
    """Paramètre dashboard global (méthode B) — fail-closed sur ``default``."""
    try:
        row = (
            (
                await db.execute(
                    select(DashboardParameter).where(
                        DashboardParameter.parameter_name == name,
                        DashboardParameter.vessel_id.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is not None:
            return Decimal(row.value)
    except Exception:
        pass
    return default


# ════════════════════════════════════════════════════════════ Intensités / EF


def _ef_method(
    co2_t: Decimal | None, denom_t: Decimal | None, distance_km: Decimal | None
) -> Decimal | None:
    """EF (gCO₂/t·km) = co2_t × 1e6 / (denom_t × distance_km) — None si non calculable."""
    if co2_t is None or denom_t is None or denom_t <= 0 or distance_km is None or distance_km <= 0:
        return None
    return (co2_t * _MILLION / (denom_t * distance_km)).quantize(_EF_QUANT)


# ════════════════════════════════════════════════════════════ Facteur


async def _resolve_factor(db: AsyncSession, fuel_type: str, at_date: Any) -> ResolvedEmissionFactor:
    """Facteur multi-GES applicable — même chaîne que l'existant, unifiée.

    1. ``emission_factors`` daté/courant (``referential_env`` — chemin de
       ``report_generation``) ;
    2. si repli codé : le composant CO₂ est recouvert par la variable
       versionnée ``do_co2_ef`` (``co2_variables``, écran ``/admin/co2``) via
       ``co2.get_do_co2_factor`` — chemin historique de ``carbon.py``, préservé
       pour ne perdre aucun réglage admin existant ;
    3. sinon : constantes codées (3,206 / 5e-5 / 1,8e-4 / 17,7).
    """
    factor = await referential_env.resolve_emission_factor(db, fuel_type, at_date)
    if factor.is_fallback and fuel_type == "MDO":
        try:
            from app.services.co2 import get_do_co2_factor

            do_co2 = await get_do_co2_factor(db)
            if do_co2 != factor.ef_co2_kg_per_kg:
                factor = replace(factor, ef_co2_kg_per_kg=do_co2)
        except Exception:
            pass
    return factor


# ════════════════════════════════════════════════════════════ Grand livre


async def compute_for_leg(
    db: AsyncSession,
    leg: Leg,
    *,
    method: str | None = None,
    cargo_t: Decimal | None = None,
    distance_nm: Decimal | None = None,
) -> LedgerResult:
    """Calcule la vue consolidée d'un voyage — events si dispo, sinon legacy_noon.

    ``cargo_t`` / ``distance_nm`` : overrides (l'adaptateur carbone / KPI passent
    le tonnage bookings et ``leg.distance_nm`` — mêmes valeurs qu'avant). Ils
    remplacent la distance/cargo canoniques dans les intensités, EF et CO₂ évité.
    ``method`` : sélecteur A/B/C explicite (jamais mélangées) — validé ; les 3
    méthodes sont calculées quoi qu'il arrive (le sélecteur sert l'API/UI).
    """
    if method is not None and method not in EF_METHODS:
        raise ValueError(f"méthode EF inconnue : {method!r} (attendu {EF_METHODS})")

    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id is not None else None
    fuel_type = getattr(vessel, "default_fuel_type", None) or "MDO"
    at_date = leg.etd.date() if leg.etd is not None else None
    factor = await _resolve_factor(db, fuel_type, at_date)

    events = await iec.finalized_events_for_leg(db, leg.id)

    conso_me = conso_ae = conso_total = conso_mouillage = conso_hors = None
    conso_escale: Decimal | None = None
    cargo_bl_canonical: Decimal | None = None
    cargo_mrv: Decimal | None = None
    distance_canonical: Decimal | None = None

    if events:
        source = "events"
        bunker_lookup = await build_bunker_lookup(db, leg.id)
        comp = await iec.compute_leg(db, leg, bunkered_t_lookup=bunker_lookup)
        totals = comp.totals
        windows = _anchoring_windows(comp.events)
        conso_mouillage = sum(
            (
                iv.total_conso_t
                for iv in comp.intervals
                if iv.total_conso_t is not None and _interval_in_anchoring(iv, windows)
            ),
            Decimal("0"),
        )
        conso_total = totals.conso_total_t if totals is not None else None
        conso_hors = (conso_total - conso_mouillage) if conso_total is not None else None
        conso_me = totals.conso_me_t if totals is not None else None
        conso_ae = totals.conso_ae_t if totals is not None else None
        distance_canonical = totals.distance_nm if totals is not None else None
        # Cargo porté par le Departure (B/L commercial + MRV « DWT carried »).
        dep = next((e for e in comp.events if isinstance(e, DepartureEvent)), None)
        cargo_bl_canonical = getattr(dep, "cargo_bl_t", None) if dep is not None else None
        dep_cargo = comp.cargo_mrv.get(dep.id) if dep is not None else None
        cargo_mrv = dep_cargo.cargo_mrv_t if dep_cargo is not None else None
        do_consumed = conso_hors
        # Conso d'escale (G12) : port stay qui suit l'Arrival de CE leg,
        # jusqu'au prochain Departure du même navire (peut appartenir au leg
        # suivant) — None tant que ce voyage n'est pas encore arrivé.
        arr = comp.events[-1] if comp.events else None
        if isinstance(arr, ArrivalEvent) and leg.vessel_id is not None:
            conso_escale = await _escale_consumption(db, leg.vessel_id, arr)
    else:
        source = "legacy_noon"
        do_consumed = await _legacy_do_consumed_t(db, leg.id)
        # Legacy : pas de granularité intervalle — tout est « hors mouillage ».
        conso_total = do_consumed
        conso_hors = do_consumed
        distance_canonical = leg.distance_nm
        cargo_bl_canonical = await _legacy_cargo_t(db, leg.id)

    # Overrides (adaptateur carbone / KPI) : remplacent distance / cargo B/L.
    distance = distance_nm if distance_nm is not None else distance_canonical
    cargo_bl = cargo_t if cargo_t is not None else cargo_bl_canonical

    # Émissions multi-GES sur l'assiette (règle d'or : seule multiplication ici).
    em = emissions_breakdown(do_consumed, factor)
    co2_emitted_t = Decimal(em["co2_t"]) if em["co2_t"] is not None else None
    ch4_g = Decimal(em["ch4_g"]) if em["ch4_g"] is not None else None
    n2o_g = Decimal(em["n2o_g"]) if em["n2o_g"] is not None else None
    co2eq_t = Decimal(em["co2eq_t"]) if em["co2eq_t"] is not None else None
    wtt_co2eq_t = Decimal(em["wtt_co2eq_t"]) if em["wtt_co2eq_t"] is not None else None

    # CO₂ évité : comparateur conventionnel ``co2.estimate`` (mêmes valeurs).
    avoided = await _avoided_co2_kg(db, distance, cargo_bl)

    # Méthodes EF (gCO₂/t·km) — jamais mélangées ; C réelle dès cargo_mrv dispo.
    distance_km = (distance * NM_TO_KM) if distance is not None else None
    occupancy = await _dashboard_param(db, "occupancy_rate_pct", _OCCUPANCY_DEFAULT)
    capacity_ref = await _dashboard_param(db, "vessel_capacity_ref_t", _CAPACITY_REF_DEFAULT)
    ef_a = _ef_method(co2_emitted_t, cargo_bl, distance_km)
    ef_b = _ef_method(co2_emitted_t, capacity_ref * (occupancy / Decimal("100")), distance_km)
    ef_c = _ef_method(co2_emitted_t, cargo_mrv, distance_km)

    return LedgerResult(
        leg_id=leg.id,
        source=source,
        conso_me_t=conso_me,
        conso_ae_t=conso_ae,
        conso_total_t=conso_total,
        conso_mouillage_t=conso_mouillage,
        conso_hors_mouillage_t=conso_hors,
        conso_escale_t=conso_escale,
        do_consumed_t=do_consumed,
        distance_nm=distance,
        cargo_bl_t=cargo_bl,
        cargo_mrv_t=cargo_mrv,
        factor=factor,
        do_co2_factor=factor.ef_co2_kg_per_kg,
        emissions=em,
        co2_emitted_t=co2_emitted_t,
        ch4_g=ch4_g,
        n2o_g=n2o_g,
        co2eq_t=co2eq_t,
        wtt_co2eq_t=wtt_co2eq_t,
        avoided_co2_kg=avoided,
        ef_method_a=ef_a,
        ef_method_b=ef_b,
        ef_method_c=ef_c,
    )


async def _avoided_co2_kg(
    db: AsyncSession, distance: Decimal | None, cargo: Decimal | None
) -> Decimal | None:
    """CO₂ évité (kg) vs conventionnel — délègue au comparateur ``co2.estimate``.

    Mêmes valeurs que l'ancien ``services.carbon`` (facteurs ``co2_variables``
    versionnés, repli constantes). ``co2.estimate`` est un comparateur officiel,
    pas un calcul d'émission réelle : cet appel ne viole pas la règle d'or.
    """
    if not (distance and Decimal(str(distance)) > 0 and cargo and cargo > 0):
        return None
    try:
        from app.services.co2 import estimate as co2_estimate
        from app.services.co2 import get_factors

        factors = await get_factors(db)
        return co2_estimate(
            distance_nm=Decimal(str(distance)), tonnage_t=cargo, factors=factors
        ).avoided_co2_kg
    except Exception:
        return None


# ════════════════════════════════════════════════════════ Matérialisation


async def _resolve_factor_row_id(db: AsyncSession, fuel_type: str, at_date: Any) -> int | None:
    """Id (best-effort) de la ligne ``emission_factors`` appliquée — même ordre
    de résolution que ``referential_env.resolve_emission_factor``. None si repli."""
    try:
        rows = (
            (await db.execute(select(EmissionFactor).where(EmissionFactor.fuel_type == fuel_type)))
            .scalars()
            .all()
        )
        if at_date is not None:
            windowed = [
                r
                for r in rows
                if r.valid_from <= at_date and (r.valid_to is None or r.valid_to >= at_date)
            ]
            if windowed:
                windowed.sort(key=lambda r: r.valid_from, reverse=True)
                return windowed[0].id
        current = [r for r in rows if r.is_current]
        if current:
            current.sort(key=lambda r: r.valid_from, reverse=True)
            return current[0].id
    except Exception:
        pass
    return None


async def refresh_summary(db: AsyncSession, leg: Leg) -> VoyageEmissionSummary:
    """(Re)calcule et upsert la ligne ``voyage_emission_summaries`` du voyage.

    Idempotent : deux appels laissent une seule ligne, à jour. Recalculé depuis
    la source de vérité (events sinon noon legacy) — le summary reste un **cache**
    (jamais lu comme référence de calcul). Appelé par le hook
    ``event_capture`` (finalisation/validation) et à la demande.
    """
    result = await compute_for_leg(db, leg)

    factors_ref: int | None = None
    if not result.factor.is_fallback:
        at_date = leg.etd.date() if leg.etd is not None else None
        factors_ref = await _resolve_factor_row_id(db, result.factor.fuel_type, at_date)

    existing = (
        await db.execute(
            select(VoyageEmissionSummary).where(VoyageEmissionSummary.leg_id == leg.id)
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    fields = {
        "conso_me_t": result.conso_me_t,
        "conso_ae_t": result.conso_ae_t,
        "conso_total_t": result.conso_total_t,
        "conso_escale_t": result.conso_escale_t,
        "conso_mouillage_t": result.conso_mouillage_t,
        "conso_hors_mouillage_t": result.conso_hors_mouillage_t,
        "co2_t": result.co2_emitted_t,
        "ch4_g": result.ch4_g,
        "n2o_g": result.n2o_g,
        "co2eq_t": result.co2eq_t,
        "wtt_co2eq_t": result.wtt_co2eq_t,
        "distance_nm": result.distance_nm,
        "cargo_bl_t": result.cargo_bl_t,
        "cargo_mrv_t": result.cargo_mrv_t,
        "ef_method_a": result.ef_method_a,
        "ef_method_b": result.ef_method_b,
        "ef_method_c": result.ef_method_c,
        "factors_ref": factors_ref,
        "source": result.source,
        "computed_at": now,
    }
    if existing is not None:
        for key, value in fields.items():
            setattr(existing, key, value)
        summary = existing
    else:
        summary = VoyageEmissionSummary(leg_id=leg.id, **fields)
        db.add(summary)
    await db.flush()
    return summary
