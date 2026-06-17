"""Synchronisation Marad → crew (LECTURE SEULE côté Marad).

Lit les données crew de Marad (``GET /api/Crewing``) et les réconcilie dans la
table ``crew_members`` de mynewtowt. **Read-only au sens Marad** : on ne modifie
JAMAIS Marad ; on n'écrit que dans notre propre base.

Principes de l'upsert (cf. docs/integrations/marad-crew-readonly.md) :
- **clé de réconciliation** : ``crew_members.marad_id`` = GUID Marad ;
- **idempotent** : un même GUID met à jour l'enregistrement existant ;
- **additif / non destructeur** : un champ n'est écrasé que si Marad fournit une
  valeur exploitable (jamais de NULL/placeholder qui effacerait une saisie ERP) ;
- **champs ERP préservés** : statut Schengen, visas, livret marin, passeport,
  ``is_active``, ``notes`` ne sont pas gérés par Marad → jamais touchés ici ;
- **champs sensibles ignorés volontairement** : ``bankAccount``, ``idNumber``,
  adresses postales, tailles de vêtements — non importés.

Schéma Marad ``/api/Crewing`` (confirmé) — champs utilisés :
``id`` (GUID), ``firstName``, ``lastName``, ``callName``, ``ranks`` (liste),
``nationality``, ``birthDate`` (ISO datetime), ``email``, ``mobilePhone``,
``phone``.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crew import CrewMember, MaradCrewSchedule
from app.models.leg import Leg
from app.utils import marad

logger = logging.getLogger("marad")


def is_configured() -> bool:
    return marad.enabled()


def _records(payload: Any) -> list[dict]:
    """Normalise une réponse Marad en liste de dicts."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "value", "records"):
            v = payload.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        return [payload]
    return []


def _clean(val: Any) -> str | None:
    """Chaîne nettoyée, ou None si vide / placeholder Swagger ("string")."""
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s or s.lower() == "string":
        return None
    return s


def _full_name(rec: dict) -> str | None:
    first = _clean(rec.get("firstName"))
    last = _clean(rec.get("lastName"))
    name = " ".join(p for p in (first, last) if p)
    return name or _clean(rec.get("callName"))


def _first_rank(rec: dict) -> str | None:
    ranks = rec.get("ranks")
    if isinstance(ranks, list):
        for r in ranks:
            c = _clean(r)
            if c:
                return c[:60]
    return None


def _nationality(rec: dict) -> str | None:
    """``nationality`` n'est conservé que si c'est un code ISO-2 (colonne CHAR(2))."""
    n = _clean(rec.get("nationality"))
    if n and len(n) == 2 and n.isalpha():
        return n.upper()
    return None


def _phone(rec: dict) -> str | None:
    p = _clean(rec.get("mobilePhone")) or _clean(rec.get("phone"))
    return p[:50] if p else None


def _email(rec: dict) -> str | None:
    e = _clean(rec.get("email"))
    return e[:255] if e and "@" in e else None


def _birth_date(rec: dict) -> date | None:
    raw = _clean(rec.get("birthDate"))
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _apply(member: CrewMember, rec: dict, *, creating: bool) -> None:
    """Écrit les champs issus de Marad sur ``member`` (additif, non destructeur)."""
    name = _full_name(rec)
    if name:
        member.full_name = name[:200]
    elif creating:
        member.full_name = "(sans nom)"

    rank = _first_rank(rec)
    if rank:
        member.role = rank
    elif creating:
        member.role = "marin"

    nat = _nationality(rec)
    if nat:
        member.nationality = nat

    dob = _birth_date(rec)
    if dob:
        member.date_of_birth = dob

    email = _email(rec)
    if email:
        member.email = email

    phone = _phone(rec)
    if phone:
        member.phone = phone


async def sync_crew(db: AsyncSession) -> dict:
    """Upsert idempotent du crew Marad dans ``crew_members`` (clé ``marad_id``).

    No-op propre si Marad n'est pas configuré. Renvoie un résumé
    ``{configured, fetched, created, updated, skipped, errors, note}``.
    """
    if not marad.enabled():
        return {
            "configured": False,
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "note": "MARAD_API_TOKEN non configuré — intégration inactive.",
        }

    payload = await marad.list_crew()
    records = _records(payload)

    created = updated = skipped = errors = 0
    for rec in records:
        marad_id = _clean(rec.get("id"))
        if not marad_id:
            skipped += 1  # enregistrement sans GUID → non réconciliable
            continue
        try:
            member = (
                await db.execute(select(CrewMember).where(CrewMember.marad_id == marad_id))
            ).scalar_one_or_none()
            if member is None:
                member = CrewMember(marad_id=marad_id, full_name="(sans nom)", role="marin")
                _apply(member, rec, creating=True)
                db.add(member)
                created += 1
            else:
                _apply(member, rec, creating=False)
                updated += 1
        except Exception:  # un enregistrement fautif ne stoppe pas le batch
            logger.exception("Marad sync: échec sur l'enregistrement %s", marad_id)
            errors += 1

    await db.flush()  # commit géré par la dependency get_db
    result = {
        "configured": True,
        "fetched": len(records),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "note": "Sync read-only Marad → crew_members (clé marad_id, non destructeur).",
    }
    logger.info("Marad sync: %s", result)
    return result


# ─────────────────── Plannings d'embarquement (CrewingSchedule) ───────────────────
# Schéma /api/CrewingSchedule NON ENCORE confirmé → extraction défensive sur des
# clés candidates (même approche que le crew avant confirmation). Le mapping
# définitif sera figé dès qu'un échantillon réel sera fourni. Chez Marad, un
# « voyage » = notre `leg` (réconcilié via leg_code).

_SCHED_ID_KEYS = ("id", "scheduleId", "crewingScheduleId")
_SCHED_CREW_KEYS = ("crewMemberId", "crewId", "personId", "seafarerId", "crewMemberGuid")
_SCHED_VESSEL_ID_KEYS = ("vesselId", "shipId", "vesselGuid")
_SCHED_VESSEL_NAME_KEYS = ("vesselName", "shipName")
_SCHED_VOYAGE_KEYS = (
    "voyage",
    "voyageNo",
    "voyageNumber",
    "voyageRef",
    "voyageName",
    "voyageCode",
    "legCode",
    "tripCode",
)
_SCHED_RANK_KEYS = ("rankName", "rank", "position", "rankLabel", "function")
_SCHED_START_KEYS = ("startDate", "signOnDate", "embarkDate", "fromDate", "dateFrom", "plannedSignOn")
_SCHED_END_KEYS = ("endDate", "signOffDate", "disembarkDate", "toDate", "dateTo", "plannedSignOff")
_SCHED_STATUS_KEYS = ("status", "state", "scheduleStatus")


def _pick(rec: dict, keys: tuple[str, ...]) -> str | None:
    """1re valeur exploitable parmi plusieurs clés candidates (placeholders ignorés)."""
    for k in keys:
        c = _clean(rec.get(k))
        if c:
            return c
    return None


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _norm_code(code: str | None) -> str:
    """Normalise un code de leg / référence voyage pour la réconciliation."""
    return (code or "").strip().upper().replace(" ", "")


async def sync_schedules(db: AsyncSession) -> dict:
    """Upsert idempotent des plannings Marad dans ``marad_crew_schedules``.

    Lecture seule côté Marad. Réconcilie :
    - le marin via ``CrewMember.marad_id`` ;
    - le navire via ``MARAD_VESSEL_MAP`` ;
    - le **leg** via le « voyage » Marad ↔ ``leg_code``.
    No-op propre si Marad n'est pas configuré.
    """
    if not marad.enabled():
        return {
            "configured": False,
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "note": "MARAD_API_TOKEN non configuré — intégration inactive.",
        }

    payload = await marad.list_schedules()
    records = _records(payload)

    # Index de réconciliation (une seule requête chacun).
    vmap = marad.vessel_map()  # {marad_vessel_id: vessel_id (str)}
    crew_rows = (
        await db.execute(
            select(CrewMember.id, CrewMember.marad_id).where(CrewMember.marad_id.is_not(None))
        )
    ).all()
    crew_by_marad = {marad_id: cid for cid, marad_id in crew_rows}
    leg_rows = (await db.execute(select(Leg.id, Leg.leg_code))).all()
    leg_by_code = {_norm_code(code): lid for lid, code in leg_rows if code}

    created = updated = skipped = errors = 0
    for rec in records:
        sched_id = _pick(rec, _SCHED_ID_KEYS)
        if not sched_id:
            skipped += 1
            continue
        try:
            crew_guid = _pick(rec, _SCHED_CREW_KEYS)
            voyage_ref = _pick(rec, _SCHED_VOYAGE_KEYS)
            marad_vessel_id = _pick(rec, _SCHED_VESSEL_ID_KEYS)
            vessel_id = None
            if marad_vessel_id and marad_vessel_id in vmap:
                try:
                    vessel_id = int(vmap[marad_vessel_id])
                except ValueError:
                    vessel_id = None
            leg_id = leg_by_code.get(_norm_code(voyage_ref)) if voyage_ref else None
            crew_member_id = crew_by_marad.get(crew_guid) if crew_guid else None

            row = (
                await db.execute(
                    select(MaradCrewSchedule).where(
                        MaradCrewSchedule.marad_schedule_id == sched_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = MaradCrewSchedule(marad_schedule_id=sched_id)
                db.add(row)
                created += 1
            else:
                updated += 1
            row.crew_member_id = crew_member_id
            row.marad_crew_id = crew_guid
            row.vessel_id = vessel_id
            row.marad_vessel_name = _pick(rec, _SCHED_VESSEL_NAME_KEYS)
            row.marad_voyage_ref = voyage_ref
            row.leg_id = leg_id
            row.rank_label = (_pick(rec, _SCHED_RANK_KEYS) or None)
            if row.rank_label:
                row.rank_label = row.rank_label[:80]
            row.start_date = _parse_date(_pick(rec, _SCHED_START_KEYS))
            row.end_date = _parse_date(_pick(rec, _SCHED_END_KEYS))
            row.status = _pick(rec, _SCHED_STATUS_KEYS)
        except Exception:  # un schedule fautif ne stoppe pas le batch
            logger.exception("Marad schedules: échec sur le schedule %s", sched_id)
            errors += 1

    await db.flush()
    result = {
        "configured": True,
        "fetched": len(records),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "note": "Sync read-only Marad → marad_crew_schedules (voyage↔leg, navire, marin).",
    }
    logger.info("Marad schedules sync: %s", result)
    return result


async def sync_all(db: AsyncSession) -> dict:
    """Synchronise crew + plannings (CrewingSchedule) en un appel.

    Utilisé par le bouton « Synchroniser Marad » (/crew) et le cron
    ``POST /api/marad/refresh``. Renvoie un résumé à plat pour l'UI + le détail.
    """
    crew = await sync_crew(db)
    sched = await sync_schedules(db)
    return {
        "configured": crew["configured"],
        "crew_created": crew.get("created", 0),
        "crew_updated": crew.get("updated", 0),
        "crew_fetched": crew.get("fetched", 0),
        "sched_created": sched.get("created", 0),
        "sched_updated": sched.get("updated", 0),
        "sched_fetched": sched.get("fetched", 0),
        "errors": crew.get("errors", 0) + sched.get("errors", 0),
        "crew": crew,
        "schedules": sched,
    }
