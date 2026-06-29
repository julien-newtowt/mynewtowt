"""Veille — synthèse quotidienne (digest) générée par IA (EVO-04).

Une ligne par jour et par langue : ``body`` est un markdown court résumant les
actualités saillantes du jour. Produit/mis à jour au cron (upsert idempotent)
quand ``ANTHROPIC_API_KEY`` est configurée ; absent sinon (l'UI n'affiche alors
aucun encart).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NewsDigest(Base):
    __tablename__ = "news_digests"
    __table_args__ = (UniqueConstraint("day", "lang", name="uq_news_digests_day_lang"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    lang: Mapped[str] = mapped_column(String(12), nullable=False, default="fr")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<NewsDigest {self.day} {self.lang}>"
