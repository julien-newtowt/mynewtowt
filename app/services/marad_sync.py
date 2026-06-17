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

from app.models.crew import CrewMember
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
