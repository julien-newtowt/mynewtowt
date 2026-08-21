"""Rappel R27 — approche de la bascule d'année civile (Year-End Cut-off, LOT G1).

CDC v0.7 §9.2 : « rappel système au Master à l'approche de l'échéance ».
Complète R27 (moteur de règles, ``validation_rules_catalog._r27_year_end_cutoff``)
qui détecte l'ABSENCE de Cut-off une fois la bascule franchie — ce service
détecte l'APPROCHE de la bascule, avant qu'elle ne soit franchie, pour donner
au Master le temps d'agir.

Un voyage actif (``atd`` posé, ``ata`` absent) dont la bascule d'année
(1ᵉʳ janvier de l'exercice suivant depuis ``atd``) tombe dans les
``rappel_cutoff_avant_j`` jours à venir, et qui n'a pas encore d'événement
Cut-off finalisé à cette date, déclenche un rappel nominatif à chaque
utilisateur ``assigné`` au navire (``User.assigned_vessel_id`` — même champ
qui scope déjà "mes voyages actifs" côté captain/onboard).

**Idempotence** — même patron que ``draft_reminders`` : avant de créer une
notification, on vérifie l'absence d'une notification active portant le même
lien et la même cible.

Appelé par le cron ``POST /api/mrv/cutoff-reminders`` (patron des crons Power
Automate existants) et testable directement (unitaire).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import Leg
from app.models.notification import Notification
from app.models.user import User
from app.services import inter_event_compute as iec
from app.services import notifications
from app.services.validation_engine import get_threshold

_RULE = "R27"
_DEFAULT_RAPPEL_J = Decimal("7")


def _norm_dt(dt: datetime | None) -> datetime | None:
    """Normalise en naïf UTC — même convention que ``validation_engine._norm_dt``."""
    if dt is None:
        return None
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo is not None else dt


def _new_cutoff_link(leg_id: int) -> str:
    return f"/onboard/events/new/cutoff?leg_id={leg_id}"


@dataclass(frozen=True)
class UpcomingCutoff:
    """Voyage actif approchant une bascule d'année sans Cut-off finalisé."""

    leg: Leg
    boundary: datetime
    days_remaining: float


async def _active_legs_without_ata(db: AsyncSession) -> list[Leg]:
    return list(
        (
            await db.execute(
                select(Leg).where(Leg.atd.isnot(None), Leg.ata.is_(None)).order_by(Leg.atd.asc())
            )
        )
        .scalars()
        .all()
    )


async def select_upcoming_cutoffs(
    db: AsyncSession, now: datetime | None = None
) -> list[UpcomingCutoff]:
    """Voyages actifs dont la bascule d'année approche (fenêtre
    ``rappel_cutoff_avant_j``) sans Cut-off finalisé à cette date.

    Hypothèse simplificatrice actée (cf. R27) : au plus une bascule par
    voyage — seule la PROCHAINE bascule depuis ``atd`` est considérée."""
    now = now or datetime.now(UTC)
    now_naive = _norm_dt(now)
    out: list[UpcomingCutoff] = []
    for leg in await _active_legs_without_ata(db):
        start = _norm_dt(leg.atd)
        if start is None:
            continue
        boundary = datetime(start.year + 1, 1, 1)
        if boundary <= now_naive:
            continue  # déjà franchie — c'est le ressort de R27, pas du rappel
        tv = await get_threshold(db, _RULE, "rappel_cutoff_avant_j", leg.vessel_id)
        window_j = float(tv.value) if tv else float(_DEFAULT_RAPPEL_J)
        days_remaining = (boundary - now_naive).total_seconds() / 86400
        if days_remaining > window_j:
            continue
        events = await iec.finalized_events_for_leg(db, leg.id)
        has_cutoff = any(
            e.event_type == "cutoff" and _norm_dt(e.datetime_utc) == boundary for e in events
        )
        if has_cutoff:
            continue
        out.append(UpcomingCutoff(leg=leg, boundary=boundary, days_remaining=days_remaining))
    return out


async def _notification_exists(db: AsyncSession, *, link: str, target_user_id: int) -> bool:
    stmt = select(Notification.id).where(
        Notification.link == link,
        Notification.is_archived.is_(False),
        Notification.target_user_id == target_user_id,
    )
    return (await db.execute(stmt.limit(1))).first() is not None


async def run_cutoff_reminders(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Envoie les rappels R27 d'approche de bascule d'année (idempotent).

    ``{"scanned", "notified"}`` : ``scanned`` = voyages concernés,
    ``notified`` = rappels **créés** (hors doublons)."""
    now = now or datetime.now(UTC)
    upcoming = await select_upcoming_cutoffs(db, now)
    created = 0

    for u in upcoming:
        leg = u.leg
        link = _new_cutoff_link(leg.id)
        masters = list(
            (await db.execute(select(User).where(User.assigned_vessel_id == leg.vessel_id)))
            .scalars()
            .all()
        )
        for master in masters:
            if await _notification_exists(db, link=link, target_user_id=master.id):
                continue
            await notifications.create(
                db,
                type="info",
                title=f"Bascule d'année à venir — voyage {leg.leg_code}",
                detail=(
                    f"Bascule d'année civile dans ~{int(u.days_remaining)} j "
                    f"({u.boundary.date()}) — déclarez l'événement Cut-off avant "
                    "cette échéance (R27)."
                ),
                link=link,
                target_user_id=master.id,
            )
            created += 1

    return {"scanned": len(upcoming), "notified": created}
