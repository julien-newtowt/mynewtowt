"""Watch log — officer-on-watch journal entries (4h periods)."""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

WATCH_PERIODS: tuple[str, ...] = ("00-04", "04-08", "08-12", "12-16", "16-20", "20-24")


class WatchLog(Base):
    __tablename__ = "watch_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, index=True)
    watch_date: Mapped[_date] = mapped_column(Date, nullable=False)
    watch_period: Mapped[str] = mapped_column(String(5), nullable=False)

    officer_on_watch: Mapped[str | None] = mapped_column(String(200))
    officer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    entry: Mapped[str] = mapped_column(Text, nullable=False)
    weather_summary: Mapped[str | None] = mapped_column(String(300))

    # Signature de l'officier de quart (verrouille le log à la signature)
    signed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    signed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    signed_by_name: Mapped[str | None] = mapped_column(String(200))
    signature_hash: Mapped[str | None] = mapped_column(String(64))
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class OnboardChecklist(Base):
    __tablename__ = "onboard_checklists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # 'fire_drill', 'abandon_drill', 'isps_audit', 'fsc_inspection', 'man_overboard'
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    items_json: Mapped[str | None] = mapped_column(Text)  # JSON-serialized items
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    signed_by_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class VisitorLog(Base):
    """ISPS visitor logbook — required on most port calls."""

    __tablename__ = "visitor_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str | None] = mapped_column(String(200))
    purpose: Mapped[str | None] = mapped_column(String(200))
    id_document: Mapped[str | None] = mapped_column(String(80))
    time_in: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escorted_by: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
