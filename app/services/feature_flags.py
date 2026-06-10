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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feature_flag import FeatureFlag


def _bucket(identifier: str, flag_key: str) -> int:
    h = hashlib.sha256(f"{flag_key}:{identifier}".encode("utf-8")).hexdigest()
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
