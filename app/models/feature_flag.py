"""Feature flag — database-backed gating for progressive rollouts."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rollout_pct: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    audience: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FeatureFlag {self.key} on={self.enabled} pct={self.rollout_pct}>"
