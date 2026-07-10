"""Calculs inter-événements MRV (LOT 3) — tout est dérivé, jamais ressaisi.

Opère sur la **chaîne d'événements FINALISÉS/VALIDÉS** d'un leg, ordonnée par
``datetime_utc`` (les brouillons sont EXCLUS — CDC §9.1). Fournit :

- distance / temps / vitesse entre événements consécutifs (haversine sur
  positions décimales ; vitesse = distance / durée) ;
- **consommation par moteur** par delta de compteur carburant :
  ``conso_t = ΔL × 0,001 × densité`` (t/m³) — formule CFOTE_05 ; densité résolue
  via ``validation_engine.get_threshold("R16", "densite_defaut_t_m3", vessel)``
  (0,845 par défaut, fail-closed) ; **gestion reset R10** (delta négatif :
  reset confirmé ⇒ conso = valeur aval ; sinon anomalie signalée, conso None) ;
- agrégation par groupe : ME (PME+SME), AE (FWD+AFT_GEN) ; lignes d'arbre
  (``engine_group`` NULL) exclues des totaux (règle dictionnaire §2.1) ;
- heures moteur par delta de ``running_hours_counter_h`` (même logique reset) ;
- **ROB chaîné** ancré sur le dernier ROB de référence (``rob_t`` d'un
  PortCallEvent) : ``ROB(evt) = ROB(précédent) − conso_totale + soutages`` ;
  les soutages (lot 6) sont injectés via ``bunkered_t_lookup`` (défaut → 0) ;
- **cargo MRV** (EU 2016/1928) : interpolation hydrostatique si dispo, sinon
  repli sur la valeur saisie ``cargo_mrv_t`` (Q11) ; ballast ⇒ 0.

Convention (plan §2.7) : Decimal partout, unités suffixées, UTC partout.

TODO lots aval :
- ``bunkered_t_lookup`` : interface prête pour le lot 6 (soutages/BDN) et le
  lot 9 (grand livre) — brancher ``bunker_operations`` sur l'escale.
- ``consumables_t`` (cargo MRV) : ROB/eau douce/urée précis — affiné au lot 9.
- persistance des résultats dans ``voyage_emission_summaries`` — lot 9.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import Leg
from app.models.nav_event import (
    BeginAnchoringEvent,
    EndAnchoringEvent,
    NavEvent,
    NavEventEngineReading,
    PortCallEvent,
)
from app.models.vessel import Vessel
from app.models.vessel_env import VesselEngine, VesselHydrostatics
from app.services.ports import haversine_nm
from app.services.validation_engine import get_threshold

# ════════════════════════════════════════════════════════════ Constantes

# Densité MDO de repli fail-closed (t/m³ ≡ kg/L) — cf. seuil R16.
FALLBACK_DENSITY_T_M3 = Decimal("0.845")
# Litres → m³.
L_TO_M3 = Decimal("0.001")
# Densité eau de mer de repli (t/m³) si non renseignée par navire.
DEFAULT_WATER_DENSITY_T_M3 = Decimal("1.025")
# Statuts entrant dans la chaîne de calcul (brouillons exclus, CDC §9.1).
FINALIZED_STATUSES: tuple[str, ...] = ("finalise", "valide")
# Groupes agrégés en totaux MRV (les lignes d'arbre NULL sont exclues).
COUNTED_ENGINE_GROUPS: tuple[str, ...] = ("ME", "AE")

# Interface soutages (lots 6/9) : tonnes soutées dans l'intervalle (from, to].
BunkerLookup = Callable[[datetime, datetime], Decimal]


def _zero_bunker(_from: datetime, _to: datetime) -> Decimal:
    """Repli par défaut : aucun soutage connu (lot 6 branchera la vraie source)."""
    return Decimal("0")


# ════════════════════════════════════════════════════════════ Résultats typés


@dataclass
class EngineConsumption:
    """Conso + heures d'un moteur sur un intervalle (delta de compteurs)."""

    engine_id: int
    engine_role: str | None
    engine_group: str | None
    fuel_l_prev: Decimal | None
    fuel_l_cur: Decimal | None
    delta_l: Decimal | None
    conso_t: Decimal | None
    running_hours_prev: Decimal | None
    running_hours_cur: Decimal | None
    running_hours_h: Decimal | None
    counter_anomaly: bool = False
    reset_applied: bool = False


@dataclass
class IntervalResult:
    """Grandeurs dérivées entre deux événements consécutifs (finalisés)."""

    from_event_id: int
    to_event_id: int
    from_dt: datetime
    to_dt: datetime
    distance_nm: Decimal | None
    duration_h: Decimal | None
    speed_kn: Decimal | None
    engines: dict[int, EngineConsumption]
    group_conso_t: dict[str, Decimal | None]
    total_conso_t: Decimal | None
    total_running_hours_h: Decimal | None
    bunkered_t: Decimal
    counter_anomaly: bool = False


@dataclass
class RobPoint:
    """ROB chaîné (calculé) + ROB déclaré (référence PortCall) à un événement."""

    event_id: int
    datetime_utc: datetime
    event_type: str
    rob_calculated_t: Decimal | None
    rob_declared_t: Decimal | None


@dataclass
class CargoMrvResult:
    """Cargo MRV « deadweight carried » d'un événement + sa méthode."""

    event_id: int
    cargo_mrv_t: Decimal | None
    method: str  # hydrostatics | declared_fallback | ballast_zero | none
    mean_draft_m: Decimal | None = None
    displacement_m3: Decimal | None = None


@dataclass
class AnchoringPair:
    """Appariement Begin↔End d'un mouillage + durée (h)."""

    begin_event_id: int
    end_event_id: int
    sequence_no: int | None
    duration_h: Decimal | None


@dataclass
class LegTotals:
    conso_me_t: Decimal | None
    conso_ae_t: Decimal | None
    conso_total_t: Decimal | None
    distance_nm: Decimal | None
    duration_h: Decimal | None


@dataclass
class LegComputation:
    leg_id: int
    events: list[NavEvent]
    intervals: list[IntervalResult]
    rob_chain: list[RobPoint]
    cargo_mrv: dict[int, CargoMrvResult] = field(default_factory=dict)
    totals: LegTotals | None = None


# ════════════════════════════════════════════════════════════ Chargement


async def finalized_events_for_leg(db: AsyncSession, leg_id: int) -> list[NavEvent]:
    """Événements finalisés/validés d'un leg, ordonnés par ``datetime_utc``.

    Les **brouillons sont exclus** (CDC §9.1). Chargement polymorphe
    (``with_polymorphic="*"`` sur la mère) + relevés en ``selectin``.
    """
    rows = await db.execute(
        select(NavEvent)
        .where(
            NavEvent.leg_id == leg_id,
            NavEvent.status.in_(FINALIZED_STATUSES),
            NavEvent.datetime_utc.isnot(None),
        )
        .order_by(NavEvent.datetime_utc.asc(), NavEvent.id.asc())
    )
    return list(rows.scalars().all())


async def _load_engines(db: AsyncSession, vessel_id: int | None) -> dict[int, VesselEngine]:
    if vessel_id is None:
        return {}
    rows = await db.execute(select(VesselEngine).where(VesselEngine.vessel_id == vessel_id))
    return {e.id: e for e in rows.scalars().all()}


async def resolve_density(db: AsyncSession, vessel_id: int | None) -> Decimal:
    """Densité MDO (t/m³) — seuil R16 paramétrable, fail-closed sur 0,845."""
    tv = await get_threshold(db, "R16", "densite_defaut_t_m3", vessel_id)
    return tv.value if tv is not None else FALLBACK_DENSITY_T_M3


# ════════════════════════════════════════════════════════════ Compteurs / conso


def _readings_by_engine(event: NavEvent) -> dict[int, NavEventEngineReading]:
    """{engine_id: relevé} pour un événement (1 relevé/moteur attendu)."""
    out: dict[int, NavEventEngineReading] = {}
    for r in event.engine_readings:
        out.setdefault(r.engine_id, r)
    return out


def _reset_confirmed(reading: NavEventEngineReading | None) -> bool:
    """R10 : reset légitime = drapeau posé ET confirmé par l'Administrateur."""
    return bool(
        reading is not None
        and reading.is_counter_reset
        and reading.reset_confirmed_by is not None
    )


def _counter_usage(
    prev_val: Decimal | None,
    cur_val: Decimal | None,
    *,
    reset_confirmed: bool,
) -> tuple[Decimal | None, bool, bool]:
    """Usage d'un compteur sur l'intervalle → (usage, anomaly, reset_applied).

    - delta ≥ 0 → usage = delta ;
    - delta < 0 & reset confirmé → usage = valeur aval (compteur reparti de ~0) ;
    - delta < 0 & non confirmé → anomalie (usage None) — R10.
    """
    if prev_val is None or cur_val is None:
        return None, False, False
    delta = cur_val - prev_val
    if delta >= 0:
        return delta, False, False
    if reset_confirmed:
        return cur_val, False, True
    return None, True, False


def engine_consumptions(
    prev: NavEvent,
    cur: NavEvent,
    engines: dict[int, VesselEngine],
    density: Decimal,
) -> dict[int, EngineConsumption]:
    """Conso (t) et heures par moteur entre ``prev`` et ``cur`` (deltas de compteurs)."""
    prev_readings = _readings_by_engine(prev)
    cur_readings = _readings_by_engine(cur)
    out: dict[int, EngineConsumption] = {}
    for engine_id, cur_r in cur_readings.items():
        prev_r = prev_readings.get(engine_id)
        engine = engines.get(engine_id)
        reset_confirmed = _reset_confirmed(cur_r)

        # Carburant (litres) → tonnes.
        fuel_prev = prev_r.fuel_counter_l if prev_r is not None else None
        fuel_cur = cur_r.fuel_counter_l
        usage_l, fuel_anomaly, reset_applied = _counter_usage(
            fuel_prev, fuel_cur, reset_confirmed=reset_confirmed
        )
        conso_t = usage_l * L_TO_M3 * density if usage_l is not None else None

        # Heures moteur.
        rh_prev = prev_r.running_hours_counter_h if prev_r is not None else None
        rh_cur = cur_r.running_hours_counter_h
        usage_h, rh_anomaly, _rh_reset = _counter_usage(
            rh_prev, rh_cur, reset_confirmed=reset_confirmed
        )

        out[engine_id] = EngineConsumption(
            engine_id=engine_id,
            engine_role=(engine.engine_role if engine is not None else None),
            engine_group=(engine.engine_group if engine is not None else None),
            fuel_l_prev=fuel_prev,
            fuel_l_cur=fuel_cur,
            delta_l=(fuel_cur - fuel_prev) if (fuel_prev is not None and fuel_cur is not None) else None,
            conso_t=conso_t,
            running_hours_prev=rh_prev,
            running_hours_cur=rh_cur,
            running_hours_h=usage_h,
            counter_anomaly=(fuel_anomaly or rh_anomaly),
            reset_applied=reset_applied,
        )
    return out


# ════════════════════════════════════════════════════════════ Distance / vitesse


def _distance_nm(prev: NavEvent, cur: NavEvent) -> Decimal | None:
    if None in (prev.lat_decimal, prev.lon_decimal, cur.lat_decimal, cur.lon_decimal):
        return None
    nm = haversine_nm(
        float(prev.lat_decimal),
        float(prev.lon_decimal),
        float(cur.lat_decimal),
        float(cur.lon_decimal),
    )
    return Decimal(str(nm))


def _utc(dt: datetime | None) -> datetime | None:
    """Normalise en UTC aware — ``datetime_utc`` est UTC par contrat (§2.7),
    mais les backends sans timezone (SQLite des tests) restituent du naïf."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _duration_h(prev: NavEvent, cur: NavEvent) -> Decimal | None:
    prev_dt, cur_dt = _utc(prev.datetime_utc), _utc(cur.datetime_utc)
    if prev_dt is None or cur_dt is None:
        return None
    seconds = (cur_dt - prev_dt).total_seconds()
    return Decimal(str(seconds)) / Decimal("3600")


# ════════════════════════════════════════════════════════════ Intervalle


def compute_interval(
    prev: NavEvent,
    cur: NavEvent,
    engines: dict[int, VesselEngine],
    density: Decimal,
    *,
    bunkered_t_lookup: BunkerLookup = _zero_bunker,
) -> IntervalResult:
    """Toutes les grandeurs dérivées entre deux événements consécutifs."""
    per_engine = engine_consumptions(prev, cur, engines, density)

    distance = _distance_nm(prev, cur)
    duration = _duration_h(prev, cur)
    speed = (
        distance / duration
        if (distance is not None and duration is not None and duration > 0)
        else None
    )

    # Agrégats par groupe ME/AE (lignes d'arbre NULL exclues). Une anomalie de
    # compteur sur un moteur compté ⇒ total du groupe indéterminé (None).
    group_conso: dict[str, Decimal | None] = {}
    group_anomaly: dict[str, bool] = {}
    for ec in per_engine.values():
        if ec.engine_group not in COUNTED_ENGINE_GROUPS:
            continue
        g = ec.engine_group
        if ec.conso_t is None:
            group_anomaly[g] = True
        else:
            group_conso[g] = (group_conso.get(g) or Decimal("0")) + ec.conso_t
    for g, is_anom in group_anomaly.items():
        if is_anom:
            group_conso[g] = None

    counted_anomaly = any(
        ec.counter_anomaly for ec in per_engine.values() if ec.engine_group in COUNTED_ENGINE_GROUPS
    )
    if counted_anomaly:
        total_conso: Decimal | None = None
    else:
        counted = [
            ec.conso_t
            for ec in per_engine.values()
            if ec.engine_group in COUNTED_ENGINE_GROUPS and ec.conso_t is not None
        ]
        total_conso = sum(counted, Decimal("0")) if counted else None

    rh_values = [
        ec.running_hours_h
        for ec in per_engine.values()
        if ec.engine_group in COUNTED_ENGINE_GROUPS and ec.running_hours_h is not None
    ]
    total_rh = sum(rh_values, Decimal("0")) if rh_values else None

    bunkered = Decimal("0")
    prev_dt, cur_dt = _utc(prev.datetime_utc), _utc(cur.datetime_utc)
    if prev_dt is not None and cur_dt is not None:
        bunkered = bunkered_t_lookup(prev_dt, cur_dt)

    return IntervalResult(
        from_event_id=prev.id,
        to_event_id=cur.id,
        from_dt=prev_dt,
        to_dt=cur_dt,
        distance_nm=distance,
        duration_h=duration,
        speed_kn=speed,
        engines=per_engine,
        group_conso_t=group_conso,
        total_conso_t=total_conso,
        total_running_hours_h=total_rh,
        bunkered_t=bunkered,
        counter_anomaly=counted_anomaly,
    )


# ════════════════════════════════════════════════════════════ ROB chaîné


def _declared_rob(event: NavEvent) -> Decimal | None:
    """ROB de référence déclaré — porté UNIQUEMENT par les PortCallEvent (R14-v2)."""
    return event.rob_t if isinstance(event, PortCallEvent) else None


def compute_rob_chain(
    events: list[NavEvent], intervals: list[IntervalResult]
) -> list[RobPoint]:
    """ROB chaîné forward, ancré sur le 1er ROB de référence (PortCall).

    ``ROB(evt) = ROB(précédent) − conso_totale(intervalle) + soutages(intervalle)``.
    Le ROB déclaré des PortCall intermédiaires/terminaux est conservé à part
    (``rob_declared_t``) pour le cross-check R14 (lot 8) — la chaîne ne se
    ré-ancre pas dessus. Une conso indéterminée (anomalie) casse la chaîne aval.
    """
    points: list[RobPoint] = []
    running: Decimal | None = None
    for i, ev in enumerate(events):
        declared = _declared_rob(ev)
        if i == 0:
            running = declared
        else:
            interval = intervals[i - 1]
            if running is not None and interval.total_conso_t is not None:
                running = running - interval.total_conso_t + interval.bunkered_t
            elif running is None:
                # Pas encore ancré : on ancre sur le 1er PortCall rencontré.
                running = declared
            else:
                # Conso inconnue (anomalie compteur) → chaîne cassée en aval.
                running = None
        points.append(
            RobPoint(
                event_id=ev.id,
                datetime_utc=ev.datetime_utc,
                event_type=ev.event_type,
                rob_calculated_t=running,
                rob_declared_t=declared,
            )
        )
    return points


# ════════════════════════════════════════════════════════════ Mouillages


def anchoring_duration_h(
    begin: BeginAnchoringEvent, end: EndAnchoringEvent
) -> Decimal | None:
    """Durée d'un mouillage = End.datetime_utc − Begin.datetime_utc (heures)."""
    begin_dt, end_dt = _utc(begin.datetime_utc), _utc(end.datetime_utc)
    if begin_dt is None or end_dt is None:
        return None
    seconds = (end_dt - begin_dt).total_seconds()
    return Decimal(str(seconds)) / Decimal("3600")


def pair_anchorings(events: list[NavEvent]) -> list[AnchoringPair]:
    """Apparie Begin/End (``paired_event_id`` explicite, sinon ``sequence_no``).

    Renvoie une paire par End avec sa durée calculée. Un End sans Begin
    correspondant a ``begin_event_id`` = 0 et ``duration_h`` None.
    """
    begins = [e for e in events if isinstance(e, BeginAnchoringEvent)]
    begins_by_id = {b.id: b for b in begins}
    begins_by_seq: dict[int | None, BeginAnchoringEvent] = {}
    for b in begins:
        begins_by_seq.setdefault(b.sequence_no, b)

    pairs: list[AnchoringPair] = []
    for end in events:
        if not isinstance(end, EndAnchoringEvent):
            continue
        begin = None
        if end.paired_event_id is not None:
            begin = begins_by_id.get(end.paired_event_id)
        if begin is None:
            begin = begins_by_seq.get(end.sequence_no)
        pairs.append(
            AnchoringPair(
                begin_event_id=(begin.id if begin is not None else 0),
                end_event_id=end.id,
                sequence_no=end.sequence_no,
                duration_h=(anchoring_duration_h(begin, end) if begin is not None else None),
            )
        )
    return pairs


# ════════════════════════════════════════════════════════════ Cargo MRV


def _interpolate_displacement(
    hydrostatics: list[VesselHydrostatics], draft_m: Decimal
) -> Decimal | None:
    """Déplacement (m³) interpolé linéairement pour un tirant d'eau moyen.

    Points hors bornes : bornés (clamp) au point extrême le plus proche.
    """
    if not hydrostatics:
        return None
    pts = sorted(hydrostatics, key=lambda h: h.draft_m)
    if draft_m <= pts[0].draft_m:
        return Decimal(pts[0].displacement_m3)
    if draft_m >= pts[-1].draft_m:
        return Decimal(pts[-1].displacement_m3)
    for lo, hi in zip(pts, pts[1:]):
        if lo.draft_m <= draft_m <= hi.draft_m:
            d_lo, d_hi = Decimal(lo.draft_m), Decimal(hi.draft_m)
            v_lo, v_hi = Decimal(lo.displacement_m3), Decimal(hi.displacement_m3)
            if d_hi == d_lo:
                return v_lo
            ratio = (draft_m - d_lo) / (d_hi - d_lo)
            return v_lo + ratio * (v_hi - v_lo)
    return None


def compute_cargo_mrv(
    event: NavEvent,
    vessel: Vessel | None,
    hydrostatics: list[VesselHydrostatics],
    *,
    consumables_t: Decimal = Decimal("0"),
) -> CargoMrvResult:
    """Cargo MRV (EU 2016/1928) : hydrostatiques si dispo, sinon repli saisie.

    - PortCall ballast ⇒ 0 ;
    - PortCall laden + drafts + hydrostatiques + lightweight ⇒
      ``déplacement(m³) × densité_eau − lightweight − consommables`` ;
    - sinon ⇒ repli sur ``cargo_mrv_t`` saisi (Q11 : hydrostatiques absentes).
    """
    if isinstance(event, PortCallEvent):
        if event.vessel_condition == "ballast":
            return CargoMrvResult(event.id, Decimal("0"), "ballast_zero")

        if (
            event.draft_fwd_m is not None
            and event.draft_aft_m is not None
            and hydrostatics
            and vessel is not None
            and vessel.lightweight_t is not None
        ):
            mean_draft = (Decimal(event.draft_fwd_m) + Decimal(event.draft_aft_m)) / Decimal("2")
            displacement_m3 = _interpolate_displacement(hydrostatics, mean_draft)
            if displacement_m3 is not None:
                water_density = (
                    Decimal(vessel.water_density_default_t_m3)
                    if vessel.water_density_default_t_m3 is not None
                    else DEFAULT_WATER_DENSITY_T_M3
                )
                displacement_t = displacement_m3 * water_density
                cargo = displacement_t - Decimal(vessel.lightweight_t) - consumables_t
                return CargoMrvResult(
                    event.id, cargo, "hydrostatics",
                    mean_draft_m=mean_draft, displacement_m3=displacement_m3,
                )

    # Repli : valeur saisie (ou None si absente).
    declared = event.cargo_mrv_t
    if declared is None:
        return CargoMrvResult(event.id, None, "none")
    return CargoMrvResult(event.id, Decimal(declared), "declared_fallback")


# ════════════════════════════════════════════════════════════ Orchestration


async def compute_leg(
    db: AsyncSession,
    leg: Leg,
    *,
    bunkered_t_lookup: BunkerLookup = _zero_bunker,
    consumables_t: Decimal = Decimal("0"),
) -> LegComputation:
    """Calcule toute la chaîne dérivée d'un leg (événements finalisés/validés)."""
    events = await finalized_events_for_leg(db, leg.id)
    engines = await _load_engines(db, leg.vessel_id)
    density = await resolve_density(db, leg.vessel_id)
    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id is not None else None
    hydro_rows = await db.execute(
        select(VesselHydrostatics).where(VesselHydrostatics.vessel_id == leg.vessel_id)
    )
    hydrostatics = list(hydro_rows.scalars().all())

    intervals: list[IntervalResult] = []
    for prev, cur in zip(events, events[1:]):
        intervals.append(
            compute_interval(prev, cur, engines, density, bunkered_t_lookup=bunkered_t_lookup)
        )

    rob_chain = compute_rob_chain(events, intervals)
    cargo_mrv = {
        ev.id: compute_cargo_mrv(ev, vessel, hydrostatics, consumables_t=consumables_t)
        for ev in events
    }
    totals = _leg_totals(intervals)

    return LegComputation(
        leg_id=leg.id,
        events=events,
        intervals=intervals,
        rob_chain=rob_chain,
        cargo_mrv=cargo_mrv,
        totals=totals,
    )


def _leg_totals(intervals: list[IntervalResult]) -> LegTotals:
    me = [i.group_conso_t.get("ME") for i in intervals if i.group_conso_t.get("ME") is not None]
    ae = [i.group_conso_t.get("AE") for i in intervals if i.group_conso_t.get("AE") is not None]
    total = [i.total_conso_t for i in intervals if i.total_conso_t is not None]
    dist = [i.distance_nm for i in intervals if i.distance_nm is not None]
    dur = [i.duration_h for i in intervals if i.duration_h is not None]
    return LegTotals(
        conso_me_t=(sum(me, Decimal("0")) if me else None),
        conso_ae_t=(sum(ae, Decimal("0")) if ae else None),
        conso_total_t=(sum(total, Decimal("0")) if total else None),
        distance_nm=(sum(dist, Decimal("0")) if dist else None),
        duration_h=(sum(dur, Decimal("0")) if dur else None),
    )
