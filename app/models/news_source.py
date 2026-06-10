"""Veille — sources de veille d'actualité (requêtes agrégateur NewsData.io).

Une *source* n'est pas un flux RSS mais une **requête sauvegardée** envoyée
à l'agrégateur tiers (NewsData.io) : un thème de veille (transport maritime,
voile / wind propulsion, Brésil, réglementation UE…) avec ses mots-clés,
pays, langues et catégorie. Chaque source peut être ciblée sur certains
rôles staff (sinon visible par tout le staff).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NewsSource(Base):
    __tablename__ = "news_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Libellé éditorial du thème de veille (ex. "Voile & wind propulsion").
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Mots-clés envoyés à NewsData (`q`) — supporte OR/AND et guillemets.
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    # Filtres NewsData optionnels (codes ISO séparés par virgule).
    countries: Mapped[str | None] = mapped_column(String(120))   # ex. "br,fr"
    languages: Mapped[str | None] = mapped_column(String(120))   # ex. "fr,en,pt"
    category: Mapped[str | None] = mapped_column(String(60))     # ex. "business"
    # Ciblage RBAC : rôles séparés par virgule. NULL/"" = tout le staff.
    target_roles: Mapped[str | None] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @property
    def role_list(self) -> list[str]:
        if not self.target_roles:
            return []
        return [r.strip() for r in self.target_roles.split(",") if r.strip()]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<NewsSource {self.id} {self.name!r} enabled={self.enabled}>"
