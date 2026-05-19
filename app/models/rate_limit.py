"""Persistent rate-limit attempts."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RateLimitAttempt(Base):
    __tablename__ = "rate_limit_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_rate_limit_scope_id_at", "scope", "identifier", "attempted_at"),
    )
