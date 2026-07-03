"""Événements de voyage — le réel du bord pilote les statuts (FLX-02).

Quand le commandant consigne un SOF de départ (SOSP) ou d'arrivée (EOSP),
le système matérialise ATD/ATA sur le leg et fait avancer les bookings
embarqués dans leur cycle de vie via ``services.booking.advance`` — qui
valide chaque transition et déclenche déjà les effets de bord
(notifications client, emails, certificat Anemos) via ``booking_lifecycle``.

Les deux hooks sont **idempotents** : rappeler ``on_vessel_departed`` sur
un leg déjà parti ne fait rien (ATD posé, bookings déjà en mer). Les
erreurs par booking sont isolées (try/except + log) : un booking en état
incohérent ne doit jamais bloquer l'action SOF du commandant.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.leg import Leg
from app.services import booking as booking_service
from app.services.activity import record as activity_record
from app.services.mrv_export import SOF_TO_MRV_MAP

logger = logging.getLogger("voyage_events")

# Types SOF déclencheurs — dérivés du mapping MRV (source de vérité unique).
DEPARTURE_SOF_TYPES: frozenset[str] = frozenset(
    sof_type for sof_type, kind in SOF_TO_MRV_MAP.items() if kind == "departure"
)
ARRIVAL_SOF_TYPES: frozenset[str] = frozenset(
    sof_type for sof_type, kind in SOF_TO_MRV_MAP.items() if kind == "arrival"
)

# Chaîne post-confirmation — ``advance()`` valide chaque transition unitaire.
_STATUS_CHAIN = ("confirmed", "loaded", "at_sea", "discharged", "delivered")


async def _advance_stepwise(db: AsyncSession, booking: Booking, target: str) -> bool:
    """Avance un booking maillon par maillon jusqu'à ``target``.

    Retourne True si au moins une transition a été appliquée.
    """
    if booking.status not in _STATUS_CHAIN or target not in _STATUS_CHAIN:
        return False
    start = _STATUS_CHAIN.index(booking.status)
    end = _STATUS_CHAIN.index(target)
    if start >= end:
        return False
    for step in _STATUS_CHAIN[start + 1 : end + 1]:
        await booking_service.advance(db, booking, step)
    return True


async def _advance_leg_bookings(
    db: AsyncSession, leg: Leg, *, from_statuses: tuple[str, ...], target: str
) -> int:
    """Avance tous les bookings du leg dans ``from_statuses`` vers ``target``.

    Chaque booking est traité indépendamment : un échec est loggé et
    n'empêche pas les suivants d'avancer.
    """
    bookings = list(
        (
            await db.execute(
                select(Booking)
                .where(Booking.leg_id == leg.id)
                .where(Booking.status.in_(from_statuses))
            )
        )
        .scalars()
        .all()
    )
    advanced = 0
    for booking in bookings:
        try:
            if await _advance_stepwise(db, booking, target):
                advanced += 1
        except Exception:
            logger.exception(
                "booking %s advance to %s failed (leg %s)",
                booking.reference,
                target,
                leg.leg_code,
            )
    return advanced


async def on_vessel_departed(db: AsyncSession, leg: Leg) -> None:
    """SOF de départ (SOSP) → ATD réel + bookings confirmés/chargés en mer."""
    from app.services.planning import refresh_leg_status

    atd_set = False
    if leg.atd is None:
        leg.atd = datetime.now(UTC)
        atd_set = True
    refresh_leg_status(leg)
    advanced = await _advance_leg_bookings(
        db, leg, from_statuses=("confirmed", "loaded"), target="at_sea"
    )
    if not atd_set and advanced == 0:
        return  # déjà appliqué — idempotent, pas de log en double
    await db.flush()
    await activity_record(
        db,
        action="vessel_departed",
        user_name="system",
        module="captain",
        entity_type="leg",
        entity_id=leg.id,
        entity_label=leg.leg_code,
        detail=f"atd={leg.atd.isoformat() if leg.atd else None} bookings_advanced={advanced}",
    )


async def on_vessel_arrived(db: AsyncSession, leg: Leg) -> None:
    """SOF d'arrivée (EOSP) → ATA réel + bookings débarqués (certificat Anemos)."""
    from app.services.planning import refresh_leg_status

    ata_set = False
    if leg.ata is None:
        leg.ata = datetime.now(UTC)
        ata_set = True
    refresh_leg_status(leg)
    advanced = await _advance_leg_bookings(
        db, leg, from_statuses=("loaded", "at_sea"), target="discharged"
    )
    if not ata_set and advanced == 0:
        return  # déjà appliqué — idempotent, pas de log en double
    await db.flush()
    await activity_record(
        db,
        action="vessel_arrived",
        user_name="system",
        module="captain",
        entity_type="leg",
        entity_id=leg.id,
        entity_label=leg.leg_code,
        detail=f"ata={leg.ata.isoformat() if leg.ata else None} bookings_advanced={advanced}",
    )
