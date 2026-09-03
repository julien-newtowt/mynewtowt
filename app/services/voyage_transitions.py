"""Séquence déclarative départ / arrivée d'un leg (PLN-SEQ).

``declare_departure`` et ``declare_arrival`` sont les points d'entrée UNIQUES
de la pose du réel (ATD/ATA), quel que soit le canal : cockpit escale
(boutons « Déclarer le départ du port de … » / « Déclarer l'arrivée au port
de … ») ou SOF du bord (SOSP/EOSP, ``captain_router``).

Séquence garantie (jamais de chevauchement, jamais d'arrivée sans départ,
**un seul leg actif par navire**) :

    planifié ──déclaration départ (ATD, POL)──▶ en mer
    en mer  ──déclaration arrivée (ATA, POD)──▶ à quai
    à quai  ──départ déclaré du leg SUIVANT────▶ terminé (voyage_completed_at)

Le départ d'un leg exige que le leg précédent du navire soit arrivé (ATA) ;
il le termine opérationnellement dans le même geste : à tout instant un
navire n'a qu'un leg « en mer » ou « à quai ». La clôture administrative
(``closure_*``, workflow captain) reste indépendante.

Chaque déclaration enchaîne, dans l'ordre :

1. **SOF** — inscription de l'événement au registre (SOSP au départ, EOSP à
   l'arrivée) si absent (``create_sof=False`` quand le SOF est lui-même le
   déclencheur, canal bord) ;
2. **Réel** — pose (ou correction) d'ATD/ATA + ``refresh_leg_status``
   (machine à états unique — le statut stocké reste ``in_progress``, la
   phase affichée « en mer »/« à quai » est dérivée, cf. ``Leg.phase``) ;
3. **Bookings** — avance du cycle de vie via ``voyage_events`` (idempotent,
   best-effort, notifications + emails clients par ``booking_lifecycle``) ;
4. **Recalculs** :
   - départ : l'ETA du leg est re-ancrée sur l'ATD (la durée de transit
     prévue est conservée), puis TOUS les legs suivants sont re-challengés
     (``date_cascade`` : legs aval, opérations d'escale, dockers, clôtures
     booking, notifications clients) ;
   - arrivée : les legs suivants sont re-ancrés sur ATA + durée d'escale
     planifiée (le leg suivant est « activé » — notification Opérations) ;
5. **Historisation** — chaque mouvement de date (réel ET prévisionnel
   recalculé) écrit une ``schedule_revision`` (sources
   ``departure_declared`` / ``arrival_declared``, aval en ``cascade``) ;
6. **Finance** — rollup OPEX réel (``finance_rollup``) ;
7. **Notifications** — SOSP/EOSP au rôle ``operation`` (premier passage
   uniquement) ; toute cascade bloquée par un leg déjà appareillé remonte en
   incident visible (``notify_cascade_blocked``, via ``date_cascade``).

La « partie navigation » du leg n'a pas de flag propre : elle est OUVERTE par
l'ATD et FERMÉE par l'ATA (fenêtre ``voyage_track.leg_window`` — tracking GPS,
statut de navigation, météo historisée en dérivent tous).

Idempotence : re-déclarer avec le même horodatage ne réémet ni révision ni
notification. Re-déclarer avec un horodatage différent est une **correction**
tracée (ancienne → nouvelle valeur) qui re-déclenche les recalculs.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import LEG_ORIGIN_TOWT, Leg
from app.models.sof_event import SofEvent
from app.services.planning import (
    DEFAULT_PORT_STAY_HOURS,
    PlanningError,
    assert_leg_mutable,
    ensure_utc,
    refresh_leg_status,
)

logger = logging.getLogger("voyage_transitions")


class VoyageSequenceError(PlanningError):
    """Violation de la séquence départ → arrivée (400 côté routes)."""


async def _ensure_sof(
    db: AsyncSession,
    leg: Leg,
    *,
    event_type: str,
    occurred_at: datetime,
    port_id: int | None,
    actor_id: int | None,
    actor_name: str | None,
) -> bool:
    """Inscrit l'événement au SOF s'il n'existe pas déjà pour ce leg.

    Un seul SOSP / EOSP par leg : si le bord (ou une synchro escale) l'a déjà
    consigné, on ne crée pas de doublon — le registre SOF appartient au bord,
    la déclaration ne fait que combler son absence (sinon la checklist de
    clôture resterait incomplète pour un leg piloté depuis la terre).
    """
    existing = await db.scalar(
        select(SofEvent.id)
        .where(SofEvent.leg_id == leg.id, SofEvent.event_type == event_type)
        .limit(1)
    )
    if existing is not None:
        return False
    db.add(
        SofEvent(
            leg_id=leg.id,
            event_type=event_type,
            occurred_at=occurred_at,
            port_id=port_id,
            notes="Auto — déclaration départ/arrivée (escale)",
            recorded_by_id=actor_id,
            recorded_by_name=actor_name,
        )
    )
    await db.flush()
    return True


async def _next_leg(db: AsyncSession, leg: Leg) -> Leg | None:
    """Prochain leg (non annulé) du même navire — celui que l'arrivée active."""
    return (
        await db.execute(
            select(Leg)
            .where(Leg.vessel_id == leg.vessel_id)
            .where(Leg.id != leg.id)
            .where(Leg.status != "cancelled")
            .where(Leg.origin != LEG_ORIGIN_TOWT)
            .where(Leg.etd > leg.etd)
            .order_by(Leg.etd.asc(), Leg.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _stay_delta(leg: Leg) -> timedelta:
    return timedelta(hours=leg.port_stay_planned_hours or DEFAULT_PORT_STAY_HOURS)


async def _previous_legs(db: AsyncSession, leg: Leg) -> list[Leg]:
    """Legs (non annulés) du même navire qui précèdent ``leg`` — par ETD."""
    return list(
        (
            await db.execute(
                select(Leg)
                .where(Leg.vessel_id == leg.vessel_id)
                .where(Leg.id != leg.id)
                .where(Leg.status != "cancelled")
                .where(Leg.origin != LEG_ORIGIN_TOWT)
                .where(Leg.etd < leg.etd)
                .order_by(Leg.etd.asc(), Leg.id.asc())
            )
        )
        .scalars()
        .all()
    )


def _complete_previous_legs(previous: list[Leg], *, at: datetime) -> list[int]:
    """Termine opérationnellement les legs précédents arrivés (ATA posée).

    Un navire qui appareille pour un nouveau leg a quitté le quai : le voyage
    précédent est terminé — ``voyage_completed_at`` posé une seule fois (jamais
    réécrit), statut recalculé par la machine à états unique.
    """
    done: list[int] = []
    for prev in previous:
        if prev.ata is not None and prev.voyage_completed_at is None:
            prev.voyage_completed_at = at
            refresh_leg_status(prev)
            done.append(prev.id)
    return done


async def declare_departure(
    db: AsyncSession,
    leg: Leg,
    *,
    at: datetime | None = None,
    actor_id: int | None = None,
    actor_name: str | None = None,
    create_sof: bool = True,
    quiet: bool = False,
    reanchor_eta: bool = True,
) -> dict:
    """Déclare le départ du port de chargement (POL) — ouvre la navigation.

    Séquence inter-legs : le leg précédent du navire doit être arrivé (ATA) ;
    il est terminé opérationnellement par ce départ (``voyage_completed_at``).
    ``quiet`` coupe les notifications (reprise d'historique en masse).
    ``reanchor_eta=False`` conserve l'ETA prévisionnelle telle quelle (pas de
    re-ancrage sur l'ATD ni de cascade) : c'est le bon réglage pour une
    reprise d'historique dont l'arrivée réelle est déjà connue — re-ancrer une
    prévision aussitôt supplantée par l'ATA fausserait le « prévu » affiché
    (ex. leg planifié au 1er août, parti le 6 juin : ETA tirée de 56 j).

    Renvoie un dict de synthèse : ``first`` (première déclaration), ``changed``
    (ATD posé ou corrigé), ``sof_created``, ``eta_shift_hours`` (re-ancrage
    d'ETA appliqué), ``cascade`` (synthèse ``date_cascade``),
    ``completed_leg_ids`` (legs précédents terminés par ce départ).
    """
    assert_leg_mutable(leg)  # archive TOWT : le réel est un fait, il ne se redéclare pas
    from app.services.finance_rollup import rollup_for_leg
    from app.services.notifications import notify_sosp
    from app.services.voyage_events import on_vessel_departed

    if leg.status == "cancelled":
        raise VoyageSequenceError("Ce leg est annulé — aucun départ à déclarer.")
    t = ensure_utc(at) or datetime.now(UTC)
    old_atd = ensure_utc(leg.atd)
    ata = ensure_utc(leg.ata)
    if ata is not None and t > ata:
        raise VoyageSequenceError(
            "Le départ ne peut pas être postérieur à l'arrivée déjà déclarée."
        )
    first = old_atd is None
    changed = first or t != old_atd
    summary: dict = {
        "first": first,
        "changed": changed,
        "sof_created": False,
        "eta_shift_hours": 0.0,
        "cascade": {},
        "completed_leg_ids": [],
    }

    # Un seul leg actif par navire : le leg précédent doit être arrivé.
    previous = await _previous_legs(db, leg)
    still_at_sea = [p for p in previous if p.atd is not None and p.ata is None]
    if still_at_sea:
        codes = ", ".join(p.leg_code for p in still_at_sea)
        raise VoyageSequenceError(
            f"Le leg précédent {codes} est encore en mer : déclarez d'abord son "
            "arrivée (ATA) — un navire n'a qu'un seul voyage actif."
        )
    if previous:
        last_prev = previous[-1]
        prev_ata = ensure_utc(last_prev.ata)
        if prev_ata is not None and t < prev_ata:
            raise VoyageSequenceError(
                f"Le départ ne peut pas précéder l'arrivée du leg précédent "
                f"{last_prev.leg_code} ({prev_ata:%Y-%m-%d %H:%M} UTC)."
            )

    if changed:
        leg.atd = t
    refresh_leg_status(leg)
    # Le navire a quitté le quai : le(s) leg(s) précédent(s) arrivé(s) sont
    # terminés opérationnellement (posé une seule fois, jamais réécrit).
    summary["completed_leg_ids"] = _complete_previous_legs(previous, at=t)

    if create_sof:
        summary["sof_created"] = await _ensure_sof(
            db,
            leg,
            event_type="SOSP",
            occurred_at=t,
            port_id=leg.departure_port_id,
            actor_id=actor_id,
            actor_name=actor_name,
        )

    # Bookings confirmés/chargés → en mer (idempotent, best-effort).
    await on_vessel_departed(db, leg)

    if changed:
        batch_id = uuid.uuid4().hex[:12]
        old_eta = ensure_utc(leg.eta)
        new_eta = old_eta
        if ata is None and reanchor_eta:
            # Re-ancrage de l'ETA sur le départ réel : la durée de transit
            # prévue est conservée (ancre = ATD précédent s'il s'agit d'une
            # correction, ETD prévisionnel sinon). L'ETD n'est jamais réécrit :
            # il reste le prévisionnel, la dérive se mesure contre lui.
            anchor = old_atd or ensure_utc(leg.etd)
            new_eta = old_eta + (t - anchor)
            if new_eta != old_eta:
                leg.eta = new_eta
                summary["eta_shift_hours"] = (new_eta - old_eta).total_seconds() / 3600.0
        await db.flush()

        # Historisation du mouvement (réel + ETA re-ancrée).
        from app.services import schedule_history

        await schedule_history.record(
            db,
            leg=leg,
            old_etd=ensure_utc(leg.etd),
            new_etd=ensure_utc(leg.etd),
            old_eta=old_eta,
            new_eta=new_eta,
            old_atd=old_atd,
            new_atd=t,
            source="departure_declared",
            batch_id=batch_id,
            detail="Départ déclaré (POL)" if first else "Correction du départ déclaré",
            user_id=actor_id,
            user_name=actor_name,
        )

        # Recalcul de TOUS les legs suivants (et dates dépendantes) si l'ETA
        # a bougé — la cascade écrit les révisions aval (source="cascade").
        if new_eta != old_eta:
            from app.services import date_cascade

            stay = _stay_delta(leg)
            summary["cascade"] = await date_cascade.cascade_from_leg(
                db,
                leg,
                old_etd=ensure_utc(leg.etd),
                old_eta=old_eta,
                old_ready_at=old_eta + stay,
                source_ready_at=new_eta + stay,
                source="departure_declared",
                batch_id=batch_id,
                actor_id=actor_id,
                actor_name=actor_name,
            )

    # Finance : l'OPEX réel dépend de la fenêtre ATD→ATA.
    try:
        await rollup_for_leg(db, leg)
    except Exception:
        logger.exception("declare_departure: rollup failed (leg %s)", leg.id)

    if first and not quiet:
        try:
            await notify_sosp(db, leg.leg_code, leg.id)
        except Exception:
            logger.exception("declare_departure: notify failed (leg %s)", leg.id)

    await db.flush()
    return summary


async def declare_arrival(
    db: AsyncSession,
    leg: Leg,
    *,
    at: datetime | None = None,
    actor_id: int | None = None,
    actor_name: str | None = None,
    create_sof: bool = True,
    quiet: bool = False,
) -> dict:
    """Déclare l'arrivée au port de destination (POD) — ferme la navigation.

    Exige un départ déclaré (séquence départ → arrivée) et une arrivée
    postérieure au départ. Active le leg suivant (recalage de ses dates sur
    ATA + durée d'escale planifiée, notification aux Opérations). ``quiet``
    coupe les notifications (reprise d'historique en masse).
    """
    assert_leg_mutable(leg)  # archive TOWT : le réel est un fait, il ne se redéclare pas
    from app.services.finance_rollup import rollup_for_leg
    from app.services.notifications import notify_eosp, notify_leg_activated
    from app.services.voyage_events import on_vessel_arrived

    if leg.status == "cancelled":
        raise VoyageSequenceError("Ce leg est annulé — aucune arrivée à déclarer.")
    atd = ensure_utc(leg.atd)
    if atd is None:
        raise VoyageSequenceError(
            "Déclarez d'abord le départ du port de chargement (ATD) — "
            "la séquence est départ → arrivée."
        )
    t = ensure_utc(at) or datetime.now(UTC)
    if t < atd:
        raise VoyageSequenceError("L'arrivée ne peut pas précéder le départ déclaré.")
    old_ata = ensure_utc(leg.ata)
    first = old_ata is None
    changed = first or t != old_ata
    summary: dict = {
        "first": first,
        "changed": changed,
        "sof_created": False,
        "cascade": {},
        "next_leg_id": None,
        "next_leg_code": None,
    }

    if changed:
        leg.ata = t
    refresh_leg_status(leg)

    if create_sof:
        summary["sof_created"] = await _ensure_sof(
            db,
            leg,
            event_type="EOSP",
            occurred_at=t,
            port_id=leg.arrival_port_id,
            actor_id=actor_id,
            actor_name=actor_name,
        )

    # Bookings chargés/en mer → débarqués (certificat Anemos, best-effort).
    await on_vessel_arrived(db, leg)

    if changed:
        batch_id = uuid.uuid4().hex[:12]
        eta = ensure_utc(leg.eta)

        from app.services import schedule_history

        await schedule_history.record(
            db,
            leg=leg,
            old_etd=ensure_utc(leg.etd),
            new_etd=ensure_utc(leg.etd),
            old_eta=eta,
            new_eta=eta,
            old_ata=old_ata,
            new_ata=t,
            source="arrival_declared",
            batch_id=batch_id,
            detail="Arrivée déclarée (POD)" if first else "Correction de l'arrivée déclarée",
            user_id=actor_id,
            user_name=actor_name,
        )

        # Re-ancrage des legs suivants sur le réel : le prochain départ ne
        # peut pas précéder ATA + durée d'escale planifiée. Une arrivée en
        # avance ne « tire » pas les legs aval (décision opérateur) ; une
        # arrivée en retard les repousse.
        from app.services import date_cascade

        stay = _stay_delta(leg)
        old_ready = (old_ata or eta) + stay
        summary["cascade"] = await date_cascade.cascade_from_leg(
            db,
            leg,
            old_etd=ensure_utc(leg.etd),
            old_eta=eta,
            old_ready_at=old_ready,
            source_ready_at=t + stay,
            source="arrival_declared",
            batch_id=batch_id,
            actor_id=actor_id,
            actor_name=actor_name,
        )

    # Activation du leg suivant : c'est désormais le voyage courant du navire
    # (statut portuaire « à quai » sur ce leg, préparation d'escale sur l'autre).
    nxt = await _next_leg(db, leg)
    if nxt is not None:
        summary["next_leg_id"] = nxt.id
        summary["next_leg_code"] = nxt.leg_code
        if first and not quiet:
            try:
                await notify_leg_activated(db, nxt.leg_code, nxt.id)
            except Exception:
                logger.exception("declare_arrival: activation notify failed (leg %s)", nxt.id)

    try:
        await rollup_for_leg(db, leg)
    except Exception:
        logger.exception("declare_arrival: rollup failed (leg %s)", leg.id)

    if first and not quiet:
        try:
            await notify_eosp(db, leg.leg_code, leg.id)
        except Exception:
            logger.exception("declare_arrival: notify failed (leg %s)", leg.id)

    await db.flush()
    return summary


async def repair_vessel_sequence(
    db: AsyncSession, *, vessel_id: int | None = None
) -> list[tuple[Leg, Leg]]:
    """Passe de cohérence « un seul leg actif par navire » sur la donnée existante.

    La règle vit dans ``declare_departure`` (le départ du leg N+1 termine le
    leg N) — mais un ATD posé par un autre chemin (ancien flux escale, import,
    SQL) l'a contournée : deux legs du même navire peuvent alors rester « à
    quai » côte à côte. Cette passe rejoue la règle a posteriori : tout leg
    arrivé (ATA) dont un leg ultérieur du même navire a appareillé (ATD) est
    terminé opérationnellement (``voyage_completed_at`` = cet ATD, statut
    recalculé). Idempotente, jamais de réécriture d'une fin déjà posée.

    Renvoie les couples ``(leg terminé, leg suivant qui l'a terminé)``.
    """
    stmt = (
        select(Leg)
        .where(Leg.status != "cancelled")
        .where(Leg.origin != LEG_ORIGIN_TOWT)  # ADR-014 : hors séquence vivante
        .order_by(Leg.vessel_id.asc(), Leg.etd.asc(), Leg.id.asc())
    )
    if vessel_id is not None:
        stmt = stmt.where(Leg.vessel_id == vessel_id)
    legs = list((await db.execute(stmt)).scalars().all())
    by_vessel: dict[int, list[Leg]] = {}
    for lg in legs:
        by_vessel.setdefault(lg.vessel_id, []).append(lg)

    repaired: list[tuple[Leg, Leg]] = []
    for lane in by_vessel.values():
        for idx, lg in enumerate(lane):
            if lg.ata is None or lg.voyage_completed_at is not None:
                continue
            successor = next((n for n in lane[idx + 1 :] if n.atd is not None), None)
            if successor is None:
                continue
            lg.voyage_completed_at = ensure_utc(successor.atd)
            refresh_leg_status(lg)
            repaired.append((lg, successor))
    if repaired:
        await db.flush()
    return repaired
