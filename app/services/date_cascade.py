"""Cascade des dates planifiées suite à un décalage d'ETD/ETA (UC-03).

Quand l'ETD/ETA d'un leg bouge (édition planning ou déclaration ETA-shift
côté capitaine), TOUTES les dates dépendantes prévisionnelles doivent être
re-challengées, pas seulement les legs aval du même navire. Ce service
applique le décalage ``delta`` à l'ensemble des artefacts planifiés rattachés
au(x) leg(s) impacté(s) :

  1. Legs aval du même navire (ETD > ancien ETD, ATD encore NULL) — etd/eta
     décalés de ``delta`` (la clôture booking suit).
  2. Opérations escale (``EscaleOperation``) du/des leg(s) — planned_start /
     planned_end décalés tant que actual_* est NULL.
  3. Shifts dockers (``DockerShift``) du/des leg(s) — idem.
  4. Packing lists : pas de date de chargement rattachée au leg dans le
     modèle V3 (``PackingList`` FK ``commercial_orders``, aucun champ
     ``loading_date``) → rien à décaler, noté dans le rapport (skipped).
  5. Bookings : la date d'arrivée client est lue via ``booking.leg.eta`` —
     aucune colonne à recalculer sur la réservation elle-même. En revanche
     chaque client actif est notifié (best-effort) du décalage.

RÈGLE D'OR : on ne touche JAMAIS un fait réalisé. Seules les lignes dont les
timestamps ACTUELS (atd / ata / actual_start / actual_end) sont encore NULL
sont décalées. Chaque bloc est isolé en try/except + logging : un bloc qui
échoue n'empêche pas les autres ni la mutation principale du leg.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import Leg

logger = logging.getLogger("date_cascade")

# Motif transmis à ``notify_clients_eta_shift``. ``schedule_cascade`` n'est pas
# un motif reconnu côté client (il s'afficherait en clair) — on retombe sur
# ``other`` qui se traduit proprement par « ajustement de planning ».
_CASCADE_NOTIFY_REASON = "other"


async def cascade_from_leg(
    db: AsyncSession,
    leg: Leg,
    *,
    delta: timedelta,
) -> dict:
    """Re-challenge toutes les dates dépendantes d'un ``leg`` décalé de ``delta``.

    ``leg`` est le leg dont l'ETD/ETA vient de bouger (ses dates portent DÉJÀ
    les nouvelles valeurs ; ``delta`` = nouvelle − ancienne). Best-effort :
    chaque bloc est isolé, les compteurs reflètent ce qui a réellement été
    décalé. Ne lève pas. ``await db.flush()`` est laissé à l'appelant (route),
    mais on flush ici aussi pour matérialiser avant les requêtes suivantes.

    Renvoie un dict de synthèse ::

        {
          "delta_hours": float,
          "downstream_legs": int,        # legs aval décalés
          "escale_operations": int,      # planned_* décalés
          "docker_shifts": int,          # planned_* décalés
          "packing_lists": int,          # 0 — pas de champ (cf. skipped)
          "clients_notified": int,       # notifications client émises
          "impacted_leg_ids": list[int], # leg source + legs aval décalés
          "skipped": list[str],          # blocs ignorés (ex. packing_lists)
        }
    """
    summary: dict = {
        "delta_hours": delta.total_seconds() / 3600.0,
        "downstream_legs": 0,
        "escale_operations": 0,
        "docker_shifts": 0,
        "packing_lists": 0,
        "clients_notified": 0,
        "impacted_leg_ids": [leg.id],
        "skipped": [],
    }

    # delta nul → rien à propager (mais on renvoie un rapport cohérent).
    if delta == timedelta(0):
        return summary

    # ── 1. Legs aval du même navire ───────────────────────────────────────
    # Frontière = ETD courant du leg − delta = ancien ETD (avant édition).
    # On reconstitue l'ancien ETD pour ne sélectionner que la VRAIE
    # descendance (un leg déjà décalé en amont ne doit pas re-capturer le
    # leg source). Les legs déjà partis (atd non NULL) sont préservés.
    impacted_legs: list[Leg] = [leg]
    try:
        old_etd = leg.etd - delta
        stmt = (
            select(Leg)
            .where(Leg.vessel_id == leg.vessel_id)
            .where(Leg.id != leg.id)
            .where(Leg.etd > old_etd)
            .where(Leg.atd.is_(None))
            .order_by(Leg.etd.asc())
        )
        downstream = list((await db.execute(stmt)).scalars().all())
        for dn in downstream:
            dn.etd = dn.etd + delta
            dn.eta = dn.eta + delta
            if dn.booking_close_at:
                dn.booking_close_at = dn.booking_close_at + delta
            impacted_legs.append(dn)
            summary["impacted_leg_ids"].append(dn.id)
        summary["downstream_legs"] = len(downstream)
        await db.flush()
    except Exception:
        # Best-effort : on log et on continue, sans bloquer la mutation principale.
        logger.exception("cascade: downstream legs shift failed (leg %s)", leg.id)

    impacted_ids = [li.id for li in impacted_legs]

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
            moved = False
            if op.planned_start is not None:
                op.planned_start = op.planned_start + delta
                moved = True
            if op.planned_end is not None:
                op.planned_end = op.planned_end + delta
                moved = True
            if moved:
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
            moved = False
            if sh.planned_start is not None:
                sh.planned_start = sh.planned_start + delta
                moved = True
            if sh.planned_end is not None:
                sh.planned_end = sh.planned_end + delta
                moved = True
            if moved:
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
    # chaque leg impacté on prévient les clients actifs du décalage d'ETA.
    try:
        from app.services import notifications

        notified = 0
        for li in impacted_legs:
            previous_eta = li.eta - delta  # ETA avant décalage
            try:
                notified += await notifications.notify_clients_eta_shift(
                    db,
                    leg_id=li.id,
                    leg_code=li.leg_code,
                    previous_eta=previous_eta,
                    new_eta=li.eta,
                    reason=_CASCADE_NOTIFY_REASON,
                )
            except Exception:
                # Échec par-leg : on log et on passe au leg suivant.
                logger.exception("cascade: client notify failed (leg %s)", li.id)
        summary["clients_notified"] = notified
    except Exception:
        logger.exception("cascade: client notifications block failed (legs %s)", impacted_ids)

    return summary
