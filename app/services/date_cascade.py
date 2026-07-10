"""Cascade des dates planifiées suite à un décalage d'ETD/ETA (UC-03).

Quand l'ETD/ETA d'un leg bouge (édition planning, drag-drop Gantt ou
déclaration ETA-shift côté capitaine), TOUTES les dates dépendantes
prévisionnelles sont re-challengées. Deux passes (mêmes règles que le
moteur scénario, cf. ``services.planning.plan_downstream_shifts``) :

  1. **Décalage rigide** : les legs aval du même navire non appareillés
     (ATD null, hors annulés) sont translatés du delta d'ETD — la
     planification relative est préservée.
  2. **Résolution des chevauchements** : tout leg qui démarrerait avant la
     fin (ETA) du précédent est repoussé, durée conservée. Couvre le pur
     allongement d'ETA (retard d'arrivée sans décalage de départ).

Sont ensuite recalés, PAR LEG et de son propre delta :
  - opérations escale (``EscaleOperation``) — planned_* tant que actual_* NULL ;
  - shifts dockers (``DockerShift``) — idem ;
  - ``booking_close_at`` (suit le delta d'ETD du leg) ;
  - notifications clients (bookings actifs) avec l'ETA réellement changée ;
  - une ligne ``schedule_revisions`` par leg décalé (source="cascade",
    ``trigger_leg_id`` = leg source, ``batch_id`` partagé) ;
  - renumérotation des leg_codes si un leg change d'année (le rang lettre
    est chronologique — cf. ``renumber_vessel_year``).

RÈGLE D'OR : on ne touche JAMAIS un fait réalisé. Seules les lignes dont
les timestamps ACTUELS (atd / ata / actual_start / actual_end) sont encore
NULL sont décalées. Chaque bloc est isolé en try/except + logging : un bloc
qui échoue n'empêche pas les autres ni la mutation principale du leg.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import Leg
from app.services.planning import (
    LegOverlap,
    _lane_after,
    ensure_utc,
    plan_downstream_shifts,
    renumber_vessel_year,
)

logger = logging.getLogger("date_cascade")

# Motif transmis à ``notify_clients_eta_shift``. ``schedule_cascade`` n'est pas
# un motif reconnu côté client (il s'afficherait en clair) — on retombe sur
# ``other`` qui se traduit proprement par « ajustement de planning ».
_CASCADE_NOTIFY_REASON = "other"


async def cascade_from_leg(
    db: AsyncSession,
    leg: Leg,
    *,
    old_etd: datetime,
    old_eta: datetime,
    old_ready_at: datetime | None = None,
    source_ready_at: datetime | None = None,
    source: str = "cascade",
    batch_id: str | None = None,
    actor_id: int | None = None,
    actor_name: str | None = None,
) -> dict:
    """Re-challenge toutes les dates dépendantes d'un ``leg`` dont les dates
    ont bougé. ``leg`` porte DÉJÀ les nouvelles valeurs ; ``old_etd`` /
    ``old_eta`` sont les valeurs AVANT modification (elles définissent la
    frontière aval et les deltas).

    Best-effort : chaque bloc est isolé, les compteurs reflètent ce qui a
    réellement été décalé. Ne lève pas. ``await db.flush()`` est laissé à
    l'appelant (route), mais on flush ici aussi pour matérialiser avant les
    requêtes suivantes.

    Renvoie un dict de synthèse ::

        {
          "delta_hours": float,          # delta d'ETD du leg source
          "downstream_legs": int,        # legs aval décalés
          "escale_operations": int,      # planned_* décalés
          "docker_shifts": int,          # planned_* décalés
          "packing_lists": int,          # 0 — pas de champ (cf. skipped)
          "clients_notified": int,       # notifications client émises
          "impacted_leg_ids": list[int], # leg source + legs aval décalés
          "renumbered": list[tuple],     # (leg_id, ancien_code, nouveau_code)
          "skipped": list[str],          # blocs ignorés
        }
    """
    old_etd = ensure_utc(old_etd)
    old_eta = ensure_utc(old_eta)
    new_etd = ensure_utc(leg.etd)
    new_eta = ensure_utc(leg.eta)
    old_ready = ensure_utc(old_ready_at) or old_eta
    source_ready = ensure_utc(source_ready_at) or new_eta
    delta = new_etd - old_etd
    batch_id = batch_id or uuid.uuid4().hex[:12]

    summary: dict = {
        "delta_hours": delta.total_seconds() / 3600.0,
        "downstream_legs": 0,
        "escale_operations": 0,
        "docker_shifts": 0,
        "packing_lists": 0,
        "clients_notified": 0,
        "impacted_leg_ids": [leg.id],
        "renumbered": [],
        "skipped": [],
    }

    # Rien n'a bougé → rien à propager (rapport cohérent quand même).
    if delta == timedelta(0) and new_eta == old_eta and source_ready == old_ready:
        return summary

    # ── 1. Legs aval du même navire (2 passes : rigide + anti-chevauchement)
    # eta_deltas mémorise, PAR leg, l'écart d'ETA appliqué — il pilote le
    # recalage des opérations escale / dockers et la notification client.
    # etd_deltas pilote booking_close_at.
    eta_deltas: dict[int, timedelta] = {leg.id: new_eta - old_eta}
    etd_deltas: dict[int, timedelta] = {leg.id: delta}
    impacted_legs: list[Leg] = [leg]
    try:
        lane = await _lane_after(
            db, vessel_id=leg.vessel_id, after_etd=old_etd, exclude_leg_id=leg.id
        )
        try:
            planned = plan_downstream_shifts(lane, delta=delta, source_eta=source_ready)
        except LegOverlap as e:
            # Un leg déjà appareillé bloque la résolution : on ne touche à
            # rien en aval, on le signale (l'opérateur arbitre manuellement).
            summary["skipped"].append(f"downstream_legs:{e}")
            planned = {}
        moved = 0
        for dn in lane:
            if dn.id not in planned:
                continue
            p_etd, p_eta = planned[dn.id]
            cur_etd, cur_eta = ensure_utc(dn.etd), ensure_utc(dn.eta)
            if (p_etd, p_eta) == (cur_etd, cur_eta):
                continue
            etd_deltas[dn.id] = p_etd - cur_etd
            eta_deltas[dn.id] = p_eta - cur_eta
            dn.etd = p_etd
            dn.eta = p_eta
            if dn.booking_close_at:
                dn.booking_close_at = ensure_utc(dn.booking_close_at) + etd_deltas[dn.id]
            impacted_legs.append(dn)
            summary["impacted_leg_ids"].append(dn.id)
            moved += 1
        summary["downstream_legs"] = moved
        await db.flush()
    except Exception:
        # Best-effort : on log et on continue, sans bloquer la mutation principale.
        logger.exception("cascade: downstream legs shift failed (leg %s)", leg.id)

    impacted_ids = [li.id for li in impacted_legs]

    # ── 1bis. Historisation (une révision par leg aval décalé) ────────────
    try:
        from app.services import schedule_history

        for li in impacted_legs:
            if li.id == leg.id:
                continue  # la révision du leg source est écrite par l'appelant
            await schedule_history.record(
                db,
                leg=li,
                old_etd=ensure_utc(li.etd) - etd_deltas[li.id],
                new_etd=ensure_utc(li.etd),
                old_eta=ensure_utc(li.eta) - eta_deltas[li.id],
                new_eta=ensure_utc(li.eta),
                source="cascade",
                batch_id=batch_id,
                trigger_leg_id=leg.id,
                user_id=actor_id,
                user_name=actor_name,
            )
    except Exception:
        logger.exception("cascade: schedule history failed (legs %s)", impacted_ids)

    # ── 1ter. Renumérotation si un leg décalé change d'année ──────────────
    try:
        years: set[int] = set()
        for li in impacted_legs:
            years.add((ensure_utc(li.etd) - etd_deltas[li.id]).year)
            years.add(ensure_utc(li.etd).year)
        renumbered: list[tuple[int, str, str]] = []
        for yr in sorted(years):
            renumbered += await renumber_vessel_year(db, leg.vessel_id, yr)
        summary["renumbered"] = renumbered
    except Exception:
        logger.exception("cascade: leg_code renumber failed (legs %s)", impacted_ids)

    # ── 2. Opérations escale (planned_*, actual_* NULL) ───────────────────
    try:
        from app.models.escale import EscaleOperation

        ops = list(
            (
                await db.execute(
                    select(EscaleOperation)
                    .where(EscaleOperation.leg_id.in_(impacted_ids))
                    .where(EscaleOperation.actual_start.is_(None))
                    .where(EscaleOperation.actual_end.is_(None))
                )
            )
            .scalars()
            .all()
        )
        shifted = 0
        for op in ops:
            d = eta_deltas.get(op.leg_id) or timedelta(0)
            if not d:
                continue
            moved_op = False
            if op.planned_start is not None:
                op.planned_start = ensure_utc(op.planned_start) + d
                moved_op = True
            if op.planned_end is not None:
                op.planned_end = ensure_utc(op.planned_end) + d
                moved_op = True
            if moved_op:
                shifted += 1
        summary["escale_operations"] = shifted
        await db.flush()
    except Exception:
        logger.exception("cascade: escale operations shift failed (legs %s)", impacted_ids)

    # ── 3. Shifts dockers (planned_*, actual_* NULL) ──────────────────────
    try:
        from app.models.escale import DockerShift

        shifts = list(
            (
                await db.execute(
                    select(DockerShift)
                    .where(DockerShift.leg_id.in_(impacted_ids))
                    .where(DockerShift.actual_start.is_(None))
                    .where(DockerShift.actual_end.is_(None))
                )
            )
            .scalars()
            .all()
        )
        shifted = 0
        for sh in shifts:
            d = eta_deltas.get(sh.leg_id) or timedelta(0)
            if not d:
                continue
            moved_sh = False
            if sh.planned_start is not None:
                sh.planned_start = ensure_utc(sh.planned_start) + d
                moved_sh = True
            if sh.planned_end is not None:
                sh.planned_end = ensure_utc(sh.planned_end) + d
                moved_sh = True
            if moved_sh:
                shifted += 1
        summary["docker_shifts"] = shifted
        await db.flush()
    except Exception:
        logger.exception("cascade: docker shifts shift failed (legs %s)", impacted_ids)

    # ── 4. Packing lists — aucune date de chargement liée au leg ──────────
    # Le modèle PackingList (app/models/packing_list.py) n'a ni leg_id ni
    # champ loading_date : il est rattaché à une commande (commercial_orders).
    # Rien à décaler côté PL — on le note explicitement.
    summary["skipped"].append("packing_lists:no loading_date field on PackingList")

    # ── 5. Bookings — rien à recalculer, mais on notifie les clients ──────
    # La réservation lit son ETA via leg.eta (pas de colonne propre). Pour
    # chaque leg dont l'ETA a réellement bougé, on prévient les clients
    # actifs du décalage.
    try:
        from app.services import notifications

        notified = 0
        for li in impacted_legs:
            d = eta_deltas.get(li.id) or timedelta(0)
            if not d:
                continue
            previous_eta = ensure_utc(li.eta) - d
            try:
                notified += await notifications.notify_clients_eta_shift(
                    db,
                    leg_id=li.id,
                    leg_code=li.leg_code,
                    previous_eta=previous_eta,
                    new_eta=ensure_utc(li.eta),
                    reason=_CASCADE_NOTIFY_REASON,
                )
            except Exception:
                # Échec par-leg : on log et on passe au leg suivant.
                logger.exception("cascade: client notify failed (leg %s)", li.id)
        summary["clients_notified"] = notified
    except Exception:
        logger.exception("cascade: client notifications block failed (legs %s)", impacted_ids)

    return summary
