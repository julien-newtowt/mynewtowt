"""Escale operations + docker shifts (Import / Export direction)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

DIRECTIONS = ("IMPORT", "EXPORT", "BOTH")
OPERATION_TYPES = ("technique", "armement", "relations_externes", "documentaire", "commercial")
OPERATION_ACTIONS = (
    "nor",
    "eosp",
    "sosp",
    "pilot_on",
    "pilot_off",
    "gangway_up",
    "gangway_down",
    "embarquement",
    "debarquement",
    "soutage",
    "avitaillement",
    "relation_presse",
    "inspection",
    "autre",
)


class EscaleOperation(Base):
    __tablename__ = "escale_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, index=True)
    direction: Mapped[str | None] = mapped_column(String(10))  # IMPORT/EXPORT/BOTH
    operation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="planned", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DockerShift(Base):
    __tablename__ = "docker_shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, index=True)
    direction: Mapped[str | None] = mapped_column(String(10))  # IMPORT/EXPORT
    company: Mapped[str | None] = mapped_column(String(200))
    nb_dockers: Mapped[int] = mapped_column(Integer, default=0)
    palettes_target: Mapped[int | None] = mapped_column(Integer)
    palettes_done: Mapped[int] = mapped_column(Integer, default=0)
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cost_eur: Mapped[float | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
