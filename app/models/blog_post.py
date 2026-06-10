"""Carnet de construction & actualités — billets éditoriaux de la vitrine.

Un ``BlogPost`` est un billet multilingue (FR canonique) rattaché à une
``category`` (``carnet`` = carnet de construction, ``actualite`` = actualités).
Le carnet suit l'avancée des quatre navires en construction — jalons positifs
uniquement (aucune critique du constructeur, aucun défaut, cf. garde-fous §2).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BlogPost(Base):
    __tablename__ = "blog_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="carnet")
    lang: Mapped[str] = mapped_column(String(12), nullable=False, default="fr")

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    lead: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, nullable=False)  # HTML de confiance (seed/admin)
    author: Mapped[str | None] = mapped_column(String(120))

    is_published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BlogPost {self.id} {self.category}:{self.slug!r}>"
