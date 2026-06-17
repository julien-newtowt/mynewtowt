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
