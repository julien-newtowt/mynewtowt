"""Public planning share — token-based access to a filtered planning view."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PlanningShare(Base):
    __tablename__ = "planning_shares"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    # Filters applied to the shared view (JSON stringified for V3.0).
    label: Mapped[str | None] = mapped_column(String(200))
    vessel_id: Mapped[int | None] = mapped_column(ForeignKey("vessels.id"))
    # Filtres géographiques optionnels : ne montrer que les traversées
    # partant de ce POL et/ou arrivant à ce POD.
    pol_port_id: Mapped[int | None] = mapped_column(ForeignKey("ports.id"))
    pod_port_id: Mapped[int | None] = mapped_column(ForeignKey("ports.id"))
    only_bookable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # Période de filtrage optionnelle (sur l'ETD). NULL ⇒ fenêtre par défaut
    # (7 j passés → 90 j à venir) appliquée à l'affichage public.
    date_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_access_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PlanningShare {self.label or self.id} token={self.token[:8]}>"
