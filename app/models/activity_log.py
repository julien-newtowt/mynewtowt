"""Append-only audit log.

In production, the table should be configured with:
- INSERT-only privileges for the app role
- A trigger preventing UPDATE/DELETE
- Optional hash-chaining for tamper detection (added in Sec-S6 sprint)
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    user_name: Mapped[str | None] = mapped_column(String(200))
    user_role: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    module: Mapped[str | None] = mapped_column(String(40))
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    entity_label: Mapped[str | None] = mapped_column(String(200))
    detail: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
