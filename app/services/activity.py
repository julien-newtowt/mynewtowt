"""Activity log helper — append-only audit recorder.

PII scrubbing : si ``entity_label`` ressemble à un email, on le réduit en
``a***@domain.tld`` (préserve la lisibilité pour audit interne tout en
limitant la fuite RGPD). Les hashes pour rate-limit sont gérés ailleurs
(``services.rate_limit``).
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity_log import ActivityLog


_EMAIL_RE = re.compile(r"^([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})$")


def _scrub(value: str | None) -> str | None:
    """Masque les emails dans une chaîne de label/detail.

    ``john.doe@example.com`` → ``j***@example.com``. Reste un identifiant
    cohérent pour les rapprochements audit, mais sans exposer l'adresse
    complète.
    """
    if not value:
        return value
    m = _EMAIL_RE.match(value.strip())
    if m:
        return f"{m.group(1)}***{m.group(2)}"
    return value


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
        user_name=_scrub(user_name),
        user_role=user_role,
        module=module,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_label=_scrub(entity_label),
        detail=_scrub(str(detail) if detail is not None else None),
        ip_address=ip_address,
    )
    db.add(entry)
    await db.flush()
    return entry
