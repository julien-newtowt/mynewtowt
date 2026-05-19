"""Activity log helper — append-only audit recorder."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity_log import ActivityLog


async def record(
    db: AsyncSession,
    *,
    action: str,
    user_id: int | None = None,
    user_name: str | None = None,
    user_role: str | None = None,
    module: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    entity_label: str | None = None,
    detail: str | Any | None = None,
    ip_address: str | None = None,
) -> ActivityLog:
    entry = ActivityLog(
        action=action,
        user_id=user_id,
        user_name=user_name,
        user_role=user_role,
        module=module,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_label=entity_label,
        detail=str(detail) if detail is not None else None,
        ip_address=ip_address,
    )
    db.add(entry)
    await db.flush()
    return entry
