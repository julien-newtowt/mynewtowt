"""Helpers de signature/verrouillage des documents commandant.

Trois types de documents sont concernés (SofEvent, NoonReport, WatchLog) :
- ``compute_hash(record)`` calcule un SHA-256 sur les champs immuables,
- ``sign(record, user)`` appose ``signed_at``, ``signed_by_*``,
  ``signature_hash`` et passe ``is_locked = True``,
- ``ensure_unlocked(record)`` lève 409 si on tente de modifier un doc
  signé (à appeler en début de toute route mutate).

Le hash sert de tamper-evidence : si quelqu'un (ou une migration de
données) modifie un champ après signature, ``verify_hash()`` ne
correspondra plus → audit visible.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException


def _norm(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.astimezone(UTC).isoformat()
    return str(v)


def compute_sof_hash(e) -> str:
    parts = [
        _norm(e.event_type), _norm(e.occurred_at), _norm(e.label),
        _norm(e.latitude), _norm(e.longitude), _norm(e.notes),
        _norm(e.signed_by_id), _norm(e.signed_at),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def compute_noon_hash(n) -> str:
    parts = [
        _norm(n.leg_id), _norm(n.recorded_at),
        _norm(n.latitude), _norm(n.longitude),
        _norm(n.sog_avg), _norm(n.cog_avg),
        _norm(n.wind_speed_kn), _norm(n.wind_direction_deg),
        _norm(n.distance_24h_nm), _norm(n.rob_fuel_l),
        _norm(n.fuel_consumed_24h_l), _norm(n.remarks),
        _norm(n.signed_by_id), _norm(n.signed_at),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def compute_watch_hash(w) -> str:
    parts = [
        _norm(w.leg_id), _norm(w.watch_date), _norm(w.watch_period),
        _norm(w.officer_on_watch), _norm(w.entry),
        _norm(w.weather_summary),
        _norm(w.signed_by_id), _norm(w.signed_at),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def sign_record(record, user, *, hash_fn) -> None:
    """Signe un record : pose signed_at / signed_by_* + signature_hash + lock."""
    if getattr(record, "is_locked", False):
        raise HTTPException(status_code=409, detail="Document déjà signé et verrouillé")
    now = datetime.now(UTC)
    record.signed_at = now
    record.signed_by_id = user.id
    record.signed_by_name = (
        getattr(user, "full_name", None) or getattr(user, "username", "") or "—"
    )
    record.signature_hash = hash_fn(record)
    record.is_locked = True


def ensure_unlocked(record) -> None:
    """Garde-fou avant tout UPDATE/DELETE — 409 si déjà verrouillé."""
    if getattr(record, "is_locked", False):
        raise HTTPException(
            status_code=409,
            detail="Document signé/verrouillé — modification interdite",
        )


def verify_hash(record, *, hash_fn) -> bool:
    """Renvoie True si le hash stocké correspond au contenu courant."""
    if not getattr(record, "signature_hash", None):
        return False
    return record.signature_hash == hash_fn(record)
