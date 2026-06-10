"""Veille — articles ingérés depuis l'agrégateur NewsData.io.

Chaque ``NewsItem`` est un article normalisé rattaché à une ``NewsSource``.
La déduplication s'appuie sur ``external_id`` (``article_id`` NewsData, ou à
défaut un SHA-256 du lien) avec contrainte d'unicité.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NewsItem(Base):
    __tablename__ = "news_items"
    __table_args__ = (
        UniqueConstraint("external_id", name="uq_news_items_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("news_sources.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Clé de déduplication (NewsData article_id ou SHA-256 du lien).
    external_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    link: Mapped[str] = mapped_column(String(1000), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Éditeur d'origine (ex. "gcaptain", "Splash 247").
    publisher: Mapped[str | None] = mapped_column(String(200))
    image_url: Mapped[str | None] = mapped_column(String(1000))
    language: Mapped[str | None] = mapped_column(String(12))
    country: Mapped[str | None] = mapped_column(String(60))
    category: Mapped[str | None] = mapped_column(String(60))

    pub_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<NewsItem {self.id} {self.title[:40]!r}>"
