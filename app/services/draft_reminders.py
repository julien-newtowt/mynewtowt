"""Alerte R19 — brouillons d'événements MRV dormants (LOT 4).

Un brouillon d'événement (``nav_events.status == "brouillon"``) non finalisé
au-delà d'un délai doit alerter :

- ``delai_rappel_brouillon_h`` (R19, 1er seuil, défaut 24 h) → **rappel** au
  Master auteur du brouillon (notification nominative) ;
- ``delai_alerte_siege_brouillon_h`` (R19, 2e seuil, défaut 48 h) → **alerte**
  aux rôles siège (Environmental Manager / DPA / QHSE), en plus du rappel.

Les deux seuils sont résolus via ``validation_engine.get_threshold`` (override
navire possible, fail-closed sur le défaut codé). L'âge d'un brouillon est
mesuré depuis ``last_saved_at`` (repli ``created_at`` si jamais sauvegardé) —
**pas** ``created_at`` seul : un Master qui reprend un brouillon sur plusieurs
sessions (l'usage même que l'autosave est censé couvrir) ne doit pas
déclencher de fausse alerte à chaque passage du cron simplement parce que la
création initiale remonte à plus de ``delai_rappel_brouillon_h`` (G3).

**Idempotence** — un même brouillon ne redéclenche pas la même alerte à chaque
passage du cron : avant de créer une notification, on vérifie l'absence d'une
notification active (non archivée) portant le même ``link`` et la même cible
(``target_user_id`` pour le Master, ``target_role`` pour un rôle siège). Le
lien pointant vers la reprise du brouillon (``/onboard/events/{id}/edit``) fait
donc office de marqueur de déduplication.

Appelé par le cron ``POST /api/mrv/draft-reminders`` (patron des crons Power
Automate) et testable directement (unitaire).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.nav_event import NavEvent
from app.models.notification import Notification
from app.services import notifications
from app.services.validation_engine import get_threshold

# Rôles « siège » alertés au 2e seuil (Environmental Manager / DPA + QHSE/admin
# — cf. plan §2.6 : module ``mrv``). Une notification par rôle (patron des
# helpers ``notify_*`` existants qui ciblent un rôle unique).
SIEGE_MRV_ROLES: tuple[str, ...] = ("manager_maritime", "administrateur")

# Défauts codés (repli si get_threshold renvoie None — ne devrait pas arriver,
# les deux paramètres sont dans THRESHOLD_SEED).
_DEFAULT_RAPPEL_H = Decimal("24")
_DEFAULT_SIEGE_H = Decimal("48")

_RULE = "R19"


def _edit_link(event_id: int) -> str:
    return f"/onboard/events/{event_id}/edit"


def _age_hours(since: datetime | None, now: datetime) -> float:
    """Âge en heures, robuste au mélange naïf (SQLite) / aware (Postgres)."""
    if since is None:
        return 0.0
    a = since if since.tzinfo is None else since.astimezone(UTC).replace(tzinfo=None)
    b = now if now.tzinfo is None else now.astimezone(UTC).replace(tzinfo=None)
    return (b - a).total_seconds() / 3600.0


def _last_saved(event: NavEvent) -> datetime | None:
    """Référence d'âge du brouillon : ``last_saved_at`` (mis à jour à chaque
    autosave), repli ``created_at`` si jamais explicitement sauvegardé (ne
    devrait pas arriver — ``event_capture.create_draft`` l'initialise à la
    création — mais reste défensif)."""
    return event.last_saved_at or event.created_at


@dataclass(frozen=True)
class DormantDraft:
    """Brouillon dormant + son âge et les seuils franchis."""

    event: NavEvent
    age_hours: float
    over_master: bool
    over_siege: bool


async def _draft_events(db: AsyncSession) -> list[NavEvent]:
    return list(
        (
            await db.execute(
                select(NavEvent)
                .where(NavEvent.status == "brouillon")
                .order_by(func.coalesce(NavEvent.last_saved_at, NavEvent.created_at).asc())
            )
        )
        .scalars()
        .all()
    )


async def select_dormant_drafts(
    db: AsyncSession, now: datetime | None = None
) -> list[DormantDraft]:
    """Sélectionne les brouillons ayant franchi au moins le 1er seuil R19.

    Résout les deux seuils par navire (override) ; l'âge est comparé à chacun.
    Renvoie uniquement les brouillons au-delà du 1er seuil (``over_master``),
    en marquant ceux qui franchissent aussi le 2e (``over_siege``).
    """
    now = now or datetime.now(UTC)
    out: list[DormantDraft] = []
    for ev in await _draft_events(db):
        master_tv = await get_threshold(db, _RULE, "delai_rappel_brouillon_h", ev.vessel_id)
        siege_tv = await get_threshold(db, _RULE, "delai_alerte_siege_brouillon_h", ev.vessel_id)
        master_h = float(master_tv.value) if master_tv else float(_DEFAULT_RAPPEL_H)
        siege_h = float(siege_tv.value) if siege_tv else float(_DEFAULT_SIEGE_H)
        age = _age_hours(_last_saved(ev), now)
        if age < master_h:
            continue
        out.append(
            DormantDraft(
                event=ev,
                age_hours=age,
                over_master=age >= master_h,
                over_siege=age >= siege_h,
            )
        )
    return out


async def _notification_exists(
    db: AsyncSession,
    *,
    link: str,
    target_user_id: int | None = None,
    target_role: str | None = None,
) -> bool:
    """Vrai s'il existe déjà une notification active (non archivée) pour ce
    couple (lien de reprise, cible) — garde-fou d'idempotence du cron."""
    stmt = select(Notification.id).where(
        Notification.link == link,
        Notification.is_archived.is_(False),
    )
    if target_user_id is not None:
        stmt = stmt.where(Notification.target_user_id == target_user_id)
    else:
        stmt = stmt.where(Notification.target_user_id.is_(None))
    if target_role is not None:
        stmt = stmt.where(Notification.target_role == target_role)
    else:
        stmt = stmt.where(Notification.target_role.is_(None))
    return (await db.execute(stmt.limit(1))).first() is not None


def _draft_label(event: NavEvent) -> str:
    return f"{event.event_type} · brouillon #{event.id}"


async def run_draft_reminders(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Envoie les rappels/alertes R19 (idempotent). Renvoie le décompte créé.

    ``{"scanned", "master", "siege"}`` :
    - ``scanned`` = brouillons au-delà du 1er seuil ;
    - ``master`` = rappels Master **créés** (hors doublons) ;
    - ``siege`` = alertes siège **créées** (hors doublons).
    """
    now = now or datetime.now(UTC)
    dormant = await select_dormant_drafts(db, now)
    created_master = 0
    created_siege = 0

    for d in dormant:
        ev = d.event
        link = _edit_link(ev.id)
        age_h = int(d.age_hours)

        # 1er seuil — rappel Master (auteur nominatif).
        if (
            d.over_master
            and ev.author_user_id is not None
            and not await _notification_exists(db, link=link, target_user_id=ev.author_user_id)
        ):
            await notifications.create(
                db,
                type="info",
                title=f"Brouillon d'événement à finaliser ({_draft_label(ev)})",
                detail=(
                    f"Ce brouillon n'est pas finalisé depuis ~{age_h} h "
                    "(rappel R19). Reprenez-le pour le finaliser."
                ),
                link=link,
                target_user_id=ev.author_user_id,
            )
            created_master += 1

        # 2e seuil — alerte siège (par rôle).
        if d.over_siege:
            for role in SIEGE_MRV_ROLES:
                if await _notification_exists(db, link=link, target_role=role):
                    continue
                await notifications.create(
                    db,
                    type="info",
                    title=f"Brouillon MRV dormant côté bord ({_draft_label(ev)})",
                    detail=(
                        f"Brouillon non finalisé depuis ~{age_h} h " "(alerte siège R19, 2e seuil)."
                    ),
                    link=link,
                    target_role=role,
                )
                created_siege += 1

    return {"scanned": len(dormant), "master": created_master, "siege": created_siege}
