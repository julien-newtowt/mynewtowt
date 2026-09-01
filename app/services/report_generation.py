"""Génération des rapports MRV & workflow de validation (LOT 5).

Les rapports sont **dérivés** du magasin d'événements (``nav_events``) via
``services.inter_event_compute`` — jamais ressaisis. Chaque rapport fige un
``payload`` JSON (snapshot) : le PDF est rendu depuis ce snapshot
(reproductibilité d'audit), jamais recalculé au rendu.

Cycle de vie (CDC §9) :

- ``generate_*`` crée/regénère un rapport au statut ``brouillon`` ; tant que le
  statut est régénérable (brouillon / attente_validation_master), une
  regénération **remplace** le payload ; dès ``valide_master`` le rapport est
  **immuable** (regénération refusée) ;
- corrections post-validation **uniquement** via ``apply_field_modification``
  (trace ``EnvFieldModification`` + met à jour le payload + audit) —
  justification vide refusée (R18) ; le pire statut qualité des modifications
  est porté par ``EnvReport.quality_status`` (dérivé) et bloque la
  consolidation au lot 10 ;
- ``validate_master`` (Master, bord — tout type) puis ``validate_siege``
  (siège — Carbon **uniquement**).

⚠ Les formules d'émission ont été **déplacées dans ``services/emission_ledger``
au lot 9** (grand livre unique, règle d'or) : ce module appelle désormais
``emission_ledger.emissions_breakdown`` (CFOTE_09 multi-GES) — ne jamais
réintroduire ici une multiplication conso × facteur. Ce module ne touche pas
``services/carbon.py`` (adaptateur legacy).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bunker import BunkerOperation
from app.models.env_report import (
    MUTABLE_STATUSES,
    QUALITY_STATUSES,
    EnvFieldModification,
    EnvReport,
    EnvReportEventLink,
    worst_quality_status,
)
from app.models.leg import Leg
from app.models.nav_event import DepartureEvent, NavEvent, PortCallEvent
from app.models.vessel import Vessel
from app.services import emission_ledger, referential_env
from app.services import inter_event_compute as iec
from app.services.activity import record as activity_record
from app.services.referential_env import ResolvedEmissionFactor
from app.services.validation_engine import get_threshold

_MILLION = Decimal("1000000")


# ════════════════════════════════════════════════════════════ Exceptions


class ReportGenerationError(Exception):
    """Erreur métier de génération/validation — traduite en HTTP par le routeur."""


class ReportImmutableError(ReportGenerationError):
    """Regénération refusée : le rapport est validé (immuable)."""


class JustificationRequiredError(ReportGenerationError):
    """R18 — toute correction post-validation exige une justification non vide."""


class SiegeValidationNotAllowedError(ReportGenerationError):
    """Validation siège demandée sur un type autre que ``carbon``."""


class ReportWorkflowError(ReportGenerationError):
    """Transition de statut invalide (déjà validé, ordre non respecté…)."""


class StopoverError(ReportGenerationError):
    """Couple Arrival/Departure incohérent pour un rapport d'escale."""


# ════════════════════════════════════════════════════════════ Sérialisation


def _num(value: Decimal | int | float | None) -> str | None:
    """Decimal → str (précision préservée, JSON-safe)."""
    return None if value is None else str(value)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _naive_utc(dt: datetime | None) -> datetime | None:
    """Ramène un datetime en naïf-UTC (compare uniformément PG aware / SQLite naïf)."""
    if dt is None:
        return None
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo is not None else dt


# ════════════════════════════════════════════════════════════ Émissions (CFOTE_09)
# La multiplication conso × facteur vit désormais UNIQUEMENT dans
# ``emission_ledger.emissions_breakdown`` (règle d'or, lot 9). Ce module l'appelle.


def _factor_meta(factor: ResolvedEmissionFactor) -> dict[str, Any]:
    return {
        "fuel_type": factor.fuel_type,
        "source_reference": factor.source_reference,
        "valid_from": factor.valid_from.isoformat() if factor.valid_from else None,
        "valid_to": factor.valid_to.isoformat() if factor.valid_to else None,
        "is_fallback": factor.is_fallback,
    }


def _intensity(value: Decimal | None, denom: Decimal | None, scale: Decimal) -> str | None:
    if value is None or denom in (None, Decimal("0")):
        return None
    return _num(value * scale / denom)


# ════════════════════════════════════════════════════════ Contexte de calcul


async def _build_bunker_lookup(db: AsyncSession, leg_id: int) -> iec.BunkerLookup:
    """Ferme une fonction ``(from, to] → tonnes soutées`` sur les soutages
    **validés Master** du voyage (ROB chaîné, lot 6).

    Logique déplacée dans ``emission_ledger.build_bunker_lookup`` (lot 9) —
    délégation conservée ici pour les appelants existants (``mrv_router``)."""
    return await emission_ledger.build_bunker_lookup(db, leg_id)


async def _engines_map(db: AsyncSession, vessel_id: int | None) -> dict[int, Any]:
    if vessel_id is None:
        return {}
    engines = await referential_env.get_vessel_engines(db, vessel_id)
    return {e.id: e for e in engines}


async def _leg_context(
    db: AsyncSession, leg: Leg
) -> tuple[iec.LegComputation, ResolvedEmissionFactor]:
    """Chaîne dérivée du voyage + facteur d'émission applicable (daté sur ETD)."""
    bunker_lookup = await _build_bunker_lookup(db, leg.id)
    comp = await iec.compute_leg(db, leg, bunkered_t_lookup=bunker_lookup)
    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id is not None else None
    fuel_type = getattr(vessel, "default_fuel_type", None) or "MDO"
    at_date = leg.etd.date() if leg.etd is not None else None
    factor = await referential_env.resolve_emission_factor(db, fuel_type, at_date)
    return comp, factor


# ════════════════════════════════════════════════ Mouillages / assiette MRV


def _anchoring_windows(events: list[NavEvent]) -> list[tuple[datetime, datetime]]:
    """Fenêtres [Begin, End] appariées (naïf-UTC)."""
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
    """Vrai si l'intervalle est **entièrement contenu** dans une fenêtre de mouillage."""
    f, t = _naive_utc(interval.from_dt), _naive_utc(interval.to_dt)
    if f is None or t is None:
        return False
    return any(wf <= f and t <= wt for (wf, wt) in windows)


# ════════════════════════════════════════════════════════ Persistance rapport


async def _find_existing(
    db: AsyncSession,
    report_type: str,
    leg_id: int,
    anchor_event_id: int | None,
    *,
    period_seq: int | None = None,
) -> EnvReport | None:
    """Rapport existant pour la clé d'upsert : Carbon = 1/leg (ou 1/période,
    G1 — ``period_seq`` distingue les 2 Carbon Reports d'un voyage scindé au
    Cut-off) ; Noon/Stopover = ancré sur un événement (lien)."""
    if report_type == "carbon":
        period_clause = (
            EnvReport.period_seq.is_(None)
            if period_seq is None
            else EnvReport.period_seq == period_seq
        )
        stmt = select(EnvReport).where(
            EnvReport.leg_id == leg_id,
            EnvReport.report_type == report_type,
            period_clause,
        )
        return (await db.execute(stmt)).scalars().first()
    if anchor_event_id is None:
        return None
    stmt = (
        select(EnvReport)
        .join(EnvReportEventLink, EnvReportEventLink.report_id == EnvReport.id)
        .where(
            EnvReport.report_type == report_type,
            EnvReportEventLink.event_id == anchor_event_id,
        )
    )
    return (await db.execute(stmt)).scalars().first()


def _assert_regenerable(existing: EnvReport | None) -> None:
    if existing is not None and existing.status not in MUTABLE_STATUSES:
        raise ReportImmutableError(
            f"Rapport {existing.report_type} #{existing.id} déjà validé "
            f"({existing.status}) — regénération refusée (immuable)."
        )


async def _store_report(
    db: AsyncSession,
    existing: EnvReport | None,
    *,
    leg_id: int,
    report_type: str,
    payload: dict[str, Any],
    link_event_ids: list[int],
    author_user_id: int | None,
    period_seq: int | None = None,
) -> EnvReport:
    """Crée ou remplace (regénération) un rapport au statut ``brouillon``.

    Les collections (``event_links``/``modifications``) ne sont touchées que
    lorsqu'elles sont *chargées* : assignées à la construction pour un rapport
    neuf (objet transitoire, aucune I/O), diff-mutées pour un rapport existant
    (chargé en ``selectin``) — jamais d'accès paresseux (MissingGreenlet async).

    ``period_seq`` (G1) : None pour un rapport non scindé (tous types, y
    compris Carbon historique) ; 1/2 pour un Carbon Report scindé au Cut-off.
    """
    now = datetime.now(UTC)
    unique_ids = list(dict.fromkeys(e for e in link_event_ids if e is not None))
    if existing is not None:
        existing.payload = payload
        existing.generated_at = now
        existing.last_saved_at = now
        existing.status = "brouillon"
        if author_user_id is not None:
            existing.author_user_id = author_user_id
        wanted = set(unique_ids)
        for link in list(existing.event_links):
            if link.event_id not in wanted:
                existing.event_links.remove(link)
        have = {link.event_id for link in existing.event_links}
        for eid in unique_ids:
            if eid not in have:
                existing.event_links.append(EnvReportEventLink(event_id=eid))
        await db.flush()
        return existing

    report = EnvReport(
        leg_id=leg_id,
        report_type=report_type,
        status="brouillon",
        payload=payload,
        generated_at=now,
        last_saved_at=now,
        author_user_id=author_user_id,
        period_seq=period_seq,
        event_links=[EnvReportEventLink(event_id=eid) for eid in unique_ids],
        modifications=[],
    )
    db.add(report)
    await db.flush()
    return report


def _vessel_meta(vessel: Vessel | None) -> dict[str, Any]:
    if vessel is None:
        return {"name": None, "code": None, "imo": None}
    return {
        "name": vessel.name,
        "code": vessel.code,
        "imo": getattr(vessel, "imo_number", None),
    }


# ════════════════════════════════════════════════════════════ Noon report


async def generate_noon_report(
    db: AsyncSession, leg: Leg, noon_event: NavEvent, *, author_user_id: int | None = None
) -> EnvReport:
    """Rapport Noon (CFOTE_05) : champs de l'événement + dérivés de l'intervalle
    depuis l'événement précédent (distance, vitesse, conso ME/AE par deltas)."""
    existing = await _find_existing(db, "noon", leg.id, noon_event.id)
    _assert_regenerable(existing)

    comp, factor = await _leg_context(db, leg)
    idx = next((i for i, e in enumerate(comp.events) if e.id == noon_event.id), None)
    interval = comp.intervals[idx - 1] if (idx is not None and idx > 0) else None
    prev_event = comp.events[idx - 1] if (idx is not None and idx > 0) else None
    rob_point = comp.rob_chain[idx] if idx is not None else None
    cargo = comp.cargo_mrv.get(noon_event.id)

    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id is not None else None
    payload: dict[str, Any] = {
        "report_type": "noon",
        "scope": {
            "leg_code": leg.leg_code,
            "vessel": _vessel_meta(vessel),
            "event_type": noon_event.event_type,
            "datetime_utc": _iso(noon_event.datetime_utc),
            "datetime_local": _iso(noon_event.datetime_local),
            "timezone": noon_event.timezone,
            "lat_decimal": _num(noon_event.lat_decimal),
            "lon_decimal": _num(noon_event.lon_decimal),
            "position_source": noon_event.position_source,
        },
        "previous_event_id": (prev_event.id if prev_event is not None else None),
        "interval": _interval_payload(interval),
        "rob": {
            "calculated_t": _num(rob_point.rob_calculated_t) if rob_point else None,
            "declared_t": _num(rob_point.rob_declared_t) if rob_point else None,
        },
        "cargo_mrv": {
            "cargo_mrv_t": _num(cargo.cargo_mrv_t) if cargo else None,
            "method": cargo.method if cargo else None,
        },
        "noon": {
            "distance_to_go_nm": _num(getattr(noon_event, "distance_to_go_nm", None)),
            "time_from_sosp_h": _num(getattr(noon_event, "time_from_sosp_h", None)),
            "distance_from_sosp_nm": _num(getattr(noon_event, "distance_from_sosp_nm", None)),
            "announced_eta": _iso(getattr(noon_event, "announced_eta", None)),
            "etb": _iso(getattr(noon_event, "etb", None)),
            "comments": getattr(noon_event, "comments", None),
        },
        "factor": _factor_meta(factor),
    }
    link_ids = [noon_event.id] + ([prev_event.id] if prev_event is not None else [])
    return await _store_report(
        db,
        existing,
        leg_id=leg.id,
        report_type="noon",
        payload=payload,
        link_event_ids=link_ids,
        author_user_id=author_user_id,
    )


def _interval_payload(interval: iec.IntervalResult | None) -> dict[str, Any]:
    if interval is None:
        return {
            "distance_nm": None,
            "duration_h": None,
            "speed_kn": None,
            "conso_me_t": None,
            "conso_ae_t": None,
            "conso_total_t": None,
            "bunkered_t": None,
            "counter_anomaly": False,
            "engines": [],
        }
    return {
        "from_event_id": interval.from_event_id,
        "to_event_id": interval.to_event_id,
        "distance_nm": _num(interval.distance_nm),
        "duration_h": _num(interval.duration_h),
        "speed_kn": _num(interval.speed_kn),
        "conso_me_t": _num(interval.group_conso_t.get("ME")),
        "conso_ae_t": _num(interval.group_conso_t.get("AE")),
        "conso_total_t": _num(interval.total_conso_t),
        "bunkered_t": _num(interval.bunkered_t),
        "counter_anomaly": interval.counter_anomaly,
        "engines": [
            {
                "engine_role": ec.engine_role,
                "engine_group": ec.engine_group,
                "conso_t": _num(ec.conso_t),
                "running_hours_h": _num(ec.running_hours_h),
                "counter_anomaly": ec.counter_anomaly,
                "reset_applied": ec.reset_applied,
            }
            for ec in interval.engines.values()
        ],
    }


# ════════════════════════════════════════════════════════════ Carbon report


def _carbon_payload(
    leg: Leg,
    comp: iec.LegComputation,
    factor: ResolvedEmissionFactor,
    vessel: Vessel | None,
    dep: DepartureEvent | None,
) -> dict[str, Any]:
    """Construit le payload Carbon (CFOTE_09) depuis une ``LegComputation`` —
    totale (voyage entier) ou une moitié issue de ``iec.split_at_event`` (G1).

    ``dep`` (pour le cargo B/L + MRV) est toujours résolu sur le Departure du
    voyage ENTIER, jamais sur la moitié en cours : le cargo physique ne
    change pas à un Cut-off (contrairement à Departure/Arrival), donc les
    deux Carbon Reports scindés d'un même voyage portent le même cargo."""
    events = comp.events
    totals = comp.totals

    windows = _anchoring_windows(events)
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
    distance = totals.distance_nm if totals is not None else None

    cargo_bl = getattr(dep, "cargo_bl_t", None) if dep is not None else None
    dep_cargo = (
        comp.cargo_mrv.get(dep.id) if (dep is not None and dep.id in comp.cargo_mrv) else None
    )
    cargo_mrv = dep_cargo.cargo_mrv_t if dep_cargo is not None else None

    # Règle d'or : la seule multiplication conso × facteur vit dans
    # ``emission_ledger.emissions_breakdown`` ; les intensités relisent son résultat.
    emissions = emission_ledger.emissions_breakdown(conso_hors, factor)
    co2_t = Decimal(emissions["co2_t"]) if emissions["co2_t"] is not None else None

    return {
        "report_type": "carbon",
        "scope": {
            "leg_code": leg.leg_code,
            "vessel": _vessel_meta(vessel),
            "etd": _iso(leg.etd),
            "eta": _iso(leg.eta),
            "event_count": len(events),
        },
        "totals": {
            "conso_me_t": _num(totals.conso_me_t) if totals else None,
            "conso_ae_t": _num(totals.conso_ae_t) if totals else None,
            "conso_total_t": _num(conso_total),
            "conso_mouillage_t": _num(conso_mouillage),
            "conso_hors_mouillage_t": _num(conso_hors),
            "distance_nm": _num(distance),
            "duration_h": _num(totals.duration_h) if totals else None,
        },
        "cargo": {
            "cargo_bl_t": _num(cargo_bl),
            "cargo_mrv_t": _num(cargo_mrv),
            "cargo_mrv_method": dep_cargo.method if dep_cargo is not None else None,
        },
        "emissions": emissions,
        "intensities": {
            # kg CO₂ / nm, kg CO₂ / t B/L, g CO₂ / (t MRV · nm) — CFOTE_09.
            "co2_per_nm_kg": _intensity(co2_t, distance, Decimal("1000")),
            "co2_per_t_bl_kg": _intensity(co2_t, cargo_bl, Decimal("1000")),
            "co2_eu_mrv_g_per_tnm": _intensity(
                co2_t,
                (
                    (distance * cargo_mrv)
                    if (distance is not None and cargo_mrv is not None)
                    else None
                ),
                _MILLION,
            ),
        },
        "factor": _factor_meta(factor),
        "events": _carbon_event_rows(comp, windows),
        "anchorings": _anchoring_rows(comp, events),
    }


async def generate_carbon_report(
    db: AsyncSession, leg: Leg, *, author_user_id: int | None = None
) -> EnvReport:
    """Rapport Carbon (CFOTE_09) depuis TOUS les événements finalisés du voyage :
    totaux conso ME/AE/total, **assiette hors mouillage** (exclut les intervalles
    Begin→End), conso mouillage à part, distance, cargo (B/L + MRV), émissions
    multi-GES.

    Comportement historique inchangé (voyage entier, 1 seul rapport) — pour
    un voyage avec un événement Cut-off finalisé (G1), préférer
    ``carbon_reports_for_leg`` qui scinde automatiquement en 2 rapports."""
    existing = await _find_existing(db, "carbon", leg.id, None)
    _assert_regenerable(existing)

    comp, factor = await _leg_context(db, leg)
    dep = next((e for e in comp.events if isinstance(e, DepartureEvent)), None)
    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id is not None else None
    payload = _carbon_payload(leg, comp, factor, vessel, dep)
    return await _store_report(
        db,
        existing,
        leg_id=leg.id,
        report_type="carbon",
        payload=payload,
        link_event_ids=[e.id for e in comp.events],
        author_user_id=author_user_id,
    )


async def _cutoff_event_for_leg(db: AsyncSession, leg: Leg) -> NavEvent | None:
    """Événement Cut-off finalisé du voyage, s'il existe (au plus un en
    pratique — cf. hypothèse simplificatrice de R27)."""
    events = await iec.finalized_events_for_leg(db, leg.id)
    return next((e for e in events if e.event_type == "cutoff"), None)


async def generate_carbon_reports_split(
    db: AsyncSession, leg: Leg, cutoff_event: NavEvent, *, author_user_id: int | None = None
) -> tuple[EnvReport, EnvReport]:
    """G1 — scinde le Carbon Report en 2 rapports indépendants de part et
    d'autre du Cut-off (CDC v0.7 §9.2 : déclaration bornée par exercice
    civil). ``period_seq=1`` (jusqu'au Cut-off inclus) et ``period_seq=2``
    (à partir du Cut-off) sont deux ``EnvReport`` distincts pour le même
    ``leg_id`` — statuts/validations indépendants, comme deux rapports
    normaux. Le contrat gelé du Dashboard (``emission_ledger``) n'est PAS
    affecté : cette scission ne touche que la couche rapport réglementaire,
    pas le total unique réconciliant vu par le Dashboard."""
    comp, factor = await _leg_context(db, leg)
    period1, period2 = iec.split_at_event(comp, cutoff_event.id)
    dep = next((e for e in comp.events if isinstance(e, DepartureEvent)), None)
    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id is not None else None

    reports: list[EnvReport] = []
    for seq, period in ((1, period1), (2, period2)):
        existing = await _find_existing(db, "carbon", leg.id, None, period_seq=seq)
        _assert_regenerable(existing)
        payload = _carbon_payload(leg, period, factor, vessel, dep)
        reports.append(
            await _store_report(
                db,
                existing,
                leg_id=leg.id,
                report_type="carbon",
                payload=payload,
                link_event_ids=[e.id for e in period.events],
                author_user_id=author_user_id,
                period_seq=seq,
            )
        )
    return reports[0], reports[1]


async def carbon_reports_for_leg(
    db: AsyncSession, leg: Leg, *, author_user_id: int | None = None
) -> list[EnvReport]:
    """Point d'entrée recommandé (G1) : génère 1 Carbon Report (comportement
    historique) si le voyage n'a pas de Cut-off, ou 2 (scindés) sinon.
    ``generate_carbon_report`` reste directement appelable pour forcer le
    rapport non scindé (tests existants, usages internes)."""
    cutoff = await _cutoff_event_for_leg(db, leg)
    if cutoff is None:
        return [await generate_carbon_report(db, leg, author_user_id=author_user_id)]
    r1, r2 = await generate_carbon_reports_split(db, leg, cutoff, author_user_id=author_user_id)
    return [r1, r2]


def _carbon_event_rows(comp: iec.LegComputation, windows) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, ev in enumerate(comp.events):
        interval = comp.intervals[i - 1] if i > 0 else None
        rob = comp.rob_chain[i] if i < len(comp.rob_chain) else None
        rows.append(
            {
                "event_id": ev.id,
                "event_type": ev.event_type,
                "datetime_utc": _iso(ev.datetime_utc),
                "distance_nm": _num(interval.distance_nm) if interval else None,
                "conso_total_t": _num(interval.total_conso_t) if interval else None,
                "rob_calculated_t": _num(rob.rob_calculated_t) if rob else None,
                "in_anchoring": bool(interval and _interval_in_anchoring(interval, windows)),
            }
        )
    return rows


def _anchoring_rows(comp: iec.LegComputation, events: list[NavEvent]) -> list[dict[str, Any]]:
    by_id = {e.id: e for e in events}
    interval_by_from = {iv.from_event_id: iv for iv in comp.intervals}
    rows: list[dict[str, Any]] = []
    for pair in iec.pair_anchorings(events):
        interval = interval_by_from.get(pair.begin_event_id)
        conso = (
            interval.total_conso_t
            if (interval and interval.to_event_id == pair.end_event_id)
            else None
        )
        begin = by_id.get(pair.begin_event_id)
        end = by_id.get(pair.end_event_id)
        rows.append(
            {
                "sequence_no": pair.sequence_no,
                "begin_datetime_utc": _iso(begin.datetime_utc) if begin else None,
                "end_datetime_utc": _iso(end.datetime_utc) if end else None,
                "duration_h": _num(pair.duration_h),
                "conso_t": _num(conso),
            }
        )
    return rows


# ════════════════════════════════════════════════════════════ Stopover report


async def generate_stopover_report(
    db: AsyncSession,
    arrival_event: NavEvent,
    departure_event: NavEvent,
    *,
    author_user_id: int | None = None,
) -> EnvReport:
    """Rapport d'escale (NOUVEAU) : entre l'Arrival du leg N et le Departure du
    leg N+1 du même navire — les 8 blocs proposés au CDC §12.4 : identité
    escale, ROB déclaré arrivée/départ, conso escale par deltas de compteurs
    ET par ROB, **écart classé** via les bornes R14 (conforme/mineur/majeur/
    critique), soutages de la fenêtre, cargaison chargée/déchargée (delta
    B/L déclaré, G8) et ancrage/dérive rattaché (Begin/End anchoring de la
    fenêtre, hors temps MRV, G8)."""
    if arrival_event.vessel_id != departure_event.vessel_id:
        raise StopoverError("Arrival et Departure doivent concerner le même navire.")
    if not isinstance(arrival_event, PortCallEvent) or not isinstance(
        departure_event, PortCallEvent
    ):
        raise StopoverError("Le rapport d'escale relie une Arrivée et un Départ (PortCall).")

    leg_id = arrival_event.leg_id  # l'escale conclut le voyage d'arrivée.
    existing = await _find_existing(db, "stopover", leg_id, arrival_event.id)
    _assert_regenerable(existing)

    vessel_id = arrival_event.vessel_id
    engines = await _engines_map(db, vessel_id)
    density = await iec.resolve_density(db, vessel_id)
    interval = iec.compute_interval(arrival_event, departure_event, engines, density)

    conso_escale = interval.total_conso_t
    rob_arrival = getattr(arrival_event, "rob_t", None)
    rob_departure = getattr(departure_event, "rob_t", None)

    bunkers = await _bunkers_in_window(
        db, vessel_id, arrival_event.datetime_utc, departure_event.datetime_utc
    )
    bunkered_t = sum((b.mass_t for b in bunkers if b.mass_t is not None), Decimal("0"))

    # Cargaison chargée/déchargée pendant l'escale (G8, CDC §12.4 bloc 7) : delta
    # du B/L déclaré entre l'Arrival et le Departure suivant (positif = chargée,
    # négatif = déchargée) — même convention que ROB/conso (valeurs déclarées
    # aux deux bornes de l'escale, jamais une nouvelle saisie/import).
    cargo_bl_arrival = getattr(arrival_event, "cargo_bl_t", None)
    cargo_bl_departure = getattr(departure_event, "cargo_bl_t", None)
    cargo_delta_t: Decimal | None = None
    if cargo_bl_arrival is not None and cargo_bl_departure is not None:
        cargo_delta_t = Decimal(cargo_bl_departure) - Decimal(cargo_bl_arrival)

    # Ancrage/dérive rattaché pendant l'escale (G8, CDC §12.4 bloc 8).
    anchorings = await _anchorings_in_window(
        db, vessel_id, arrival_event.datetime_utc, departure_event.datetime_utc
    )

    rob_theo: Decimal | None = None
    ecart: Decimal | None = None
    if rob_arrival is not None and conso_escale is not None:
        rob_theo = Decimal(rob_arrival) - conso_escale + bunkered_t
        if rob_departure is not None:
            ecart = abs(Decimal(rob_departure) - rob_theo)
    classification = await _classify_rob_ecart(db, ecart, vessel_id)

    arr_leg = await db.get(Leg, arrival_event.leg_id) if arrival_event.leg_id else None
    dep_leg = await db.get(Leg, departure_event.leg_id) if departure_event.leg_id else None
    vessel = await db.get(Vessel, vessel_id) if vessel_id is not None else None

    payload: dict[str, Any] = {
        "report_type": "stopover",
        "scope": {
            "vessel": _vessel_meta(vessel),
            "arrival_leg_code": arr_leg.leg_code if arr_leg else None,
            "departure_leg_code": dep_leg.leg_code if dep_leg else None,
        },
        "arrival": {
            "event_id": arrival_event.id,
            "datetime_utc": _iso(arrival_event.datetime_utc),
            "rob_declared_t": _num(rob_arrival),
        },
        "departure": {
            "event_id": departure_event.id,
            "datetime_utc": _iso(departure_event.datetime_utc),
            "rob_declared_t": _num(rob_departure),
        },
        "duration_h": _num(interval.duration_h),
        "consumption": {
            "conso_escale_t": _num(conso_escale),
            "conso_me_t": _num(interval.group_conso_t.get("ME")),
            "conso_ae_t": _num(interval.group_conso_t.get("AE")),
            "counter_anomaly": interval.counter_anomaly,
            "engines": _interval_payload(interval)["engines"],
        },
        "rob_check": {
            "declared_arrival_t": _num(rob_arrival),
            "declared_departure_t": _num(rob_departure),
            "theoretical_departure_t": _num(rob_theo),
            "bunkered_t": _num(bunkered_t),
            "ecart_t": _num(ecart),
            "classification": classification["status"],
            "thresholds": classification["thresholds"],
        },
        "bunkers": [
            {
                "bdn_number": b.bdn_number,
                "port_locode": b.port_locode,
                "delivery_datetime_utc": _iso(b.delivery_datetime_utc),
                "mass_t": _num(b.mass_t),
                "fuel_type": b.fuel_type,
            }
            for b in bunkers
        ],
        "cargo": {
            "declared_arrival_t": _num(cargo_bl_arrival),
            "declared_departure_t": _num(cargo_bl_departure),
            "delta_t": _num(cargo_delta_t),
        },
        "anchorings": anchorings,
    }
    return await _store_report(
        db,
        existing,
        leg_id=leg_id,
        report_type="stopover",
        payload=payload,
        link_event_ids=[arrival_event.id, departure_event.id],
        author_user_id=author_user_id,
    )


async def _bunkers_in_window(
    db: AsyncSession, vessel_id: int, frm: datetime | None, to: datetime | None
) -> list[BunkerOperation]:
    """Soutages validés Master du navire livrés dans la fenêtre (arrivée, départ]."""
    rows = (
        (
            await db.execute(
                select(BunkerOperation)
                .where(BunkerOperation.vessel_id == vessel_id)
                .where(BunkerOperation.status == "valide_master")
                .order_by(BunkerOperation.delivery_datetime_utc)
            )
        )
        .scalars()
        .all()
    )
    f, t = _naive_utc(frm), _naive_utc(to)
    out: list[BunkerOperation] = []
    for b in rows:
        d = _naive_utc(b.delivery_datetime_utc)
        if d is None:
            continue
        if (f is None or f < d) and (t is None or d <= t):
            out.append(b)
    return out


async def _anchorings_in_window(
    db: AsyncSession, vessel_id: int, frm: datetime | None, to: datetime | None
) -> list[dict[str, Any]]:
    """Mouillages (Begin/End appariés) du navire survenus dans la fenêtre
    (arrivée, départ] de l'escale — bloc « Ancrage/dérive rattaché » (CDC
    §12.4 bloc 8, G8) : ces périodes sont hors temps MRV (cf. Carbon Report),
    référencées ici pour information, pas recalculées."""
    rows = (
        (
            await db.execute(
                select(NavEvent)
                .where(
                    NavEvent.vessel_id == vessel_id,
                    NavEvent.event_type.in_(("anchoring_begin", "anchoring_end")),
                    NavEvent.status.in_(iec.FINALIZED_STATUSES),
                )
                .order_by(NavEvent.datetime_utc.asc())
            )
        )
        .scalars()
        .all()
    )
    f, t = _naive_utc(frm), _naive_utc(to)
    windowed = []
    for e in rows:
        d = _naive_utc(e.datetime_utc)
        if d is None:
            continue
        if (f is None or f < d) and (t is None or d <= t):
            windowed.append(e)
    by_id = {e.id: e for e in windowed}
    out: list[dict[str, Any]] = []
    for pair in iec.pair_anchorings(windowed):
        begin = by_id.get(pair.begin_event_id)
        end = by_id.get(pair.end_event_id)
        out.append(
            {
                "sequence_no": pair.sequence_no,
                "begin_datetime_utc": _iso(begin.datetime_utc) if begin else None,
                "end_datetime_utc": _iso(end.datetime_utc) if end else None,
                "duration_h": _num(pair.duration_h),
            }
        )
    return out


async def _classify_rob_ecart(
    db: AsyncSession, ecart: Decimal | None, vessel_id: int | None
) -> dict[str, Any]:
    """Classe un écart ROB via les 3 bornes R14 (mineur/majeur/critique)."""
    mineur = await get_threshold(db, "R14", "seuil_rob_ecart_mineur_t", vessel_id)
    majeur = await get_threshold(db, "R14", "seuil_rob_ecart_majeur_t", vessel_id)
    critique = await get_threshold(db, "R14", "seuil_rob_ecart_critique_t", vessel_id)
    thresholds = {
        "seuil_rob_ecart_mineur_t": _num(mineur.value) if mineur else None,
        "seuil_rob_ecart_majeur_t": _num(majeur.value) if majeur else None,
        "seuil_rob_ecart_critique_t": _num(critique.value) if critique else None,
    }
    if ecart is None or mineur is None or majeur is None or critique is None:
        status = "indetermine"
    elif ecart <= mineur.value:
        status = "conforme"
    elif ecart <= majeur.value:
        status = "mineur"
    elif ecart <= critique.value:
        status = "majeur"
    else:
        status = "critique"
    return {"status": status, "thresholds": thresholds}


# ════════════════════════════════════════════════════════ Corrections / workflow


async def apply_field_modification(
    db: AsyncSession,
    report: EnvReport,
    field_name: str,
    corrected_value: Any,
    justification: str,
    author: Any,
    resulting_quality_status: str,
) -> EnvFieldModification:
    """Correction tracée d'un champ (R18) — **justification obligatoire**.

    Écrit un ``EnvFieldModification``, met à jour le payload (snapshot) et
    enregistre l'action dans l'``activity trail``. Le pire statut qualité des
    modifications est ensuite porté par ``EnvReport.quality_status`` (dérivé) —
    consommé par le lot 10 (``under_conformity`` bloque la consolidation).
    """
    just = (justification or "").strip()
    if not just:
        raise JustificationRequiredError("R18 — justification obligatoire pour toute correction.")
    if resulting_quality_status not in QUALITY_STATUSES:
        raise ReportGenerationError(
            f"Statut qualité inconnu : {resulting_quality_status!r} (attendu {QUALITY_STATUSES})."
        )

    payload = dict(report.payload or {})
    initial_value = payload.get(field_name)

    mod = EnvFieldModification(
        event_id=None,
        field_name=field_name,
        initial_value=None if initial_value is None else str(initial_value),
        corrected_value=None if corrected_value is None else str(corrected_value),
        justification_text=just,
        author_user_id=getattr(author, "id", None),
        timestamp_utc=datetime.now(UTC),
        resulting_quality_status=resulting_quality_status,
    )
    # Via la relation → report_id posé automatiquement + collection en mémoire
    # à jour (le ``champ dérivé`` ``EnvReport.quality_status`` la relit).
    report.modifications.append(mod)

    payload[field_name] = corrected_value
    report.payload = payload  # réassignation → attribut marqué « dirty »
    report.last_saved_at = datetime.now(UTC)
    await db.flush()

    await activity_record(
        db,
        action="mrv_report_field_modify",
        user_id=getattr(author, "id", None),
        user_name=getattr(author, "full_name", None) or getattr(author, "username", None),
        user_role=getattr(author, "role", None),
        module="mrv",
        entity_type="env_report",
        entity_id=report.id,
        entity_label=f"{report.report_type} #{report.id} · {field_name}",
        detail=f"{initial_value!r} → {corrected_value!r} [{resulting_quality_status}]",
    )
    return mod


async def validate_master(db: AsyncSession, report: EnvReport, user: Any) -> EnvReport:
    """Validation Master (bord) — tout type. Verrouille la regénération."""
    if report.status in ("valide_master", "valide_siege"):
        raise ReportWorkflowError("Rapport déjà validé Master.")
    report.status = "valide_master"
    report.validated_master_at = datetime.now(UTC)
    report.validated_master_by = getattr(user, "id", None)
    await db.flush()
    return report


async def validate_siege(db: AsyncSession, report: EnvReport, user: Any) -> EnvReport:
    """Validation siège — **réservée au Carbon** (2ᵉ niveau CDC §9)."""
    if report.report_type != "carbon":
        raise SiegeValidationNotAllowedError(
            f"Validation siège réservée au Carbon (type reçu : {report.report_type})."
        )
    if report.status != "valide_master":
        raise ReportWorkflowError("Le rapport doit être validé Master avant la validation siège.")
    report.status = "valide_siege"
    report.validated_siege_at = datetime.now(UTC)
    report.validated_siege_by = getattr(user, "id", None)
    await db.flush()
    return report


async def report_quality_status(db: AsyncSession, report_id: int) -> str | None:
    """Pire statut qualité d'un rapport (requête explicite — sûr hors selectin).

    Point de consommation documenté pour le lot 10 (porte de consolidation :
    ``under_conformity`` exclut le rapport du dataset réglementaire)."""
    rows = (
        (
            await db.execute(
                select(EnvFieldModification.resulting_quality_status).where(
                    EnvFieldModification.report_id == report_id
                )
            )
        )
        .scalars()
        .all()
    )
    return worst_quality_status(rows)
