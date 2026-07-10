"""Feature flag evaluation.

Resolution rules (in order):
1. If the flag is missing in DB → enabled=False (deny by default).
2. If `enabled=False` → False.
3. If `audience.roles` non-empty and user role is in it → True.
4. If `audience.client_segments` non-empty and client segment matches → True.
5. If `rollout_pct > 0` → hash(user_id, flag_key) % 100 < rollout_pct.
6. Otherwise → True (flag is enabled globally).
"""

from __future__ import annotations

import hashlib
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feature_flag import FeatureFlag

# ─────────────────── Toggle global « Newtowt Agent » (chatbot Kairos AI) ──────
# Activable / désactivable depuis /admin (Configuration). Stocké en FeatureFlag
# (clé ``newtowt_agent``). Défaut = activé (le chatbot fonctionnait avant le
# toggle). Lecture mise en cache (TTL court) pour éviter une requête DB sur
# chaque page staff (le topbar l'affiche partout).
NEWTOWT_AGENT_KEY = "newtowt_agent"
_AGENT_CACHE_TTL_S = 30.0
_agent_cache: dict[str, float | bool] = {"value": True, "exp": 0.0}


async def newtowt_agent_enabled(db: AsyncSession, *, use_cache: bool = True) -> bool:
    """État du « Newtowt Agent » (chatbot). Défaut activé si le flag est absent."""
    now = time.monotonic()
    if use_cache and now < float(_agent_cache["exp"]):
        return bool(_agent_cache["value"])
    flag = (
        await db.execute(select(FeatureFlag).where(FeatureFlag.key == NEWTOWT_AGENT_KEY))
    ).scalar_one_or_none()
    value = True if flag is None else bool(flag.enabled)
    _agent_cache["value"] = value
    _agent_cache["exp"] = now + _AGENT_CACHE_TTL_S
    return value


async def set_newtowt_agent(db: AsyncSession, enabled: bool, *, user_id: int | None = None) -> None:
    """Active / désactive le Newtowt Agent et invalide le cache."""
    flag = (
        await db.execute(select(FeatureFlag).where(FeatureFlag.key == NEWTOWT_AGENT_KEY))
    ).scalar_one_or_none()
    if flag is None:
        flag = FeatureFlag(
            key=NEWTOWT_AGENT_KEY,
            enabled=enabled,
            description="Chatbot Kairos AI (Newtowt Agent) — widget topbar + page /chat.",
            updated_by_id=user_id,
        )
        db.add(flag)
    else:
        flag.enabled = enabled
        flag.updated_by_id = user_id
    await db.flush()
    _agent_cache["value"] = enabled
    _agent_cache["exp"] = time.monotonic() + _AGENT_CACHE_TTL_S


# ─────────────────── Bascule capture événementielle (LOT 14) ──────────────────
# ``mrv_v2_capture`` : la capture d'événements v2 (``/onboard/events``) remplace
# la saisie noon legacy. **DÉFAUT ON GLOBAL** (flag absent ⇒ actif). Désactivable
# **PAR NAVIRE** via ``audience.vessels_off`` (liste de codes navire et/ou d'ids)
# pour le double-run inversé du pilote : un navire en opt-out garde l'ancien
# formulaire noon actif. Lecture en cache court (le bord interroge la garde à
# chaque saisie). **FAIL-OPEN vers ON** : une panne DB ne doit jamais rouvrir le
# legacy en douce (ON = legacy bloqué = capture v2 imposée).
MRV_V2_CAPTURE_KEY = "mrv_v2_capture"
_CAPTURE_CACHE_TTL_S = 20.0
_capture_cache: dict[str, tuple[float, bool]] = {}


def _capture_vessel_key(vessel) -> str:
    if vessel is None:
        return "__none__"
    return f"id:{getattr(vessel, 'id', None)}|code:{getattr(vessel, 'code', None)}"


def reset_capture_v2_cache() -> None:
    """Vide le cache de la garde de bascule (tests / après édition du flag)."""
    _capture_cache.clear()


async def capture_v2_enabled(db: AsyncSession, vessel, *, use_cache: bool = True) -> bool:
    """La capture d'événements v2 est-elle active pour ce navire ?

    - flag absent → ``True`` (défaut ON global) ;
    - ``enabled=False`` → ``False`` (coupé globalement, tous navires en legacy) ;
    - ``enabled=True`` + navire dans ``audience.vessels_off`` → ``False`` (opt-out
      double-run) ; sinon ``True``.
    Fail-open vers ``True`` si le flag est illisible (jamais rouvrir le legacy).
    """
    key = _capture_vessel_key(vessel)
    now = time.monotonic()
    if use_cache:
        cached = _capture_cache.get(key)
        if cached is not None and now < cached[0]:
            return cached[1]
    try:
        flag = (
            await db.execute(select(FeatureFlag).where(FeatureFlag.key == MRV_V2_CAPTURE_KEY))
        ).scalar_one_or_none()
        if flag is None:
            value = True  # défaut ON global
        elif not flag.enabled:
            value = False  # coupé globalement
        else:
            audience = flag.audience or {}
            off = audience.get("vessels_off") or []
            codes_off = {str(x).strip().upper() for x in off}
            ids_off = {str(x).strip() for x in off}
            code = (getattr(vessel, "code", None) or "").upper() if vessel is not None else ""
            vid = str(getattr(vessel, "id", "")) if vessel is not None else ""
            opted_out = bool(code and code in codes_off) or bool(vid and vid in ids_off)
            value = not opted_out
    except Exception:  # fail-open : ne jamais rouvrir le legacy en douce sur erreur DB
        value = True
    if use_cache:
        _capture_cache[key] = (now + _CAPTURE_CACHE_TTL_S, value)
    return value


def _bucket(identifier: str, flag_key: str) -> int:
    h = hashlib.sha256(f"{flag_key}:{identifier}".encode()).hexdigest()
    return int(h[:8], 16) % 100


async def is_enabled(
    db: AsyncSession,
    key: str,
    *,
    user_role: str | None = None,
    user_id: int | None = None,
    client_segment: str | None = None,
    client_id: int | None = None,
) -> bool:
    flag = (
        await db.execute(select(FeatureFlag).where(FeatureFlag.key == key))
    ).scalar_one_or_none()
    if not flag or not flag.enabled:
        return False

    audience = flag.audience or {}
    roles = set(audience.get("roles", []))
    segments = set(audience.get("client_segments", []))

    if roles and user_role and user_role in roles:
        return True
    if segments and client_segment and client_segment in segments:
        return True
    if roles or segments:
        # Audience explicitly set but user matches none of them.
        # Fall through to rollout_pct gate.
        pass

    if flag.rollout_pct == 0:
        return not (roles or segments)  # global ON only if no audience set

    identifier = str(user_id or client_id or "anonymous")
    return _bucket(identifier, key) < flag.rollout_pct
