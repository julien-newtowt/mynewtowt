"""Crew members, assignments, certifications, leaves."""
from __future__ import annotations

from datetime import date as _date, datetime

from sqlalchemy import (
    Boolean, CHAR, Date, DateTime, ForeignKey, Integer, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CrewMember(Base):
    __tablename__ = "crew_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(60), nullable=False)  # captain, chief_mate, ab, cook, ...
    nationality: Mapped[str | None] = mapped_column(CHAR(2))
    date_of_birth: Mapped[_date | None] = mapped_column(Date)
    passport_number: Mapped[str | None] = mapped_column(String(60))
    passport_expires_at: Mapped[_date | None] = mapped_column(Date)
    schengen_status: Mapped[str] = mapped_column(String(20), default="compliant", nullable=False)
    # 'compliant' | 'warning' (>80 days in 180) | 'non_compliant' (>90 in 180)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CrewAssignment(Base):
    __tablename__ = "crew_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    crew_member_id: Mapped[int] = mapped_column(ForeignKey("crew_members.id"), nullable=False, index=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, index=True)
    role_on_board: Mapped[str | None] = mapped_column(String(60))
    embark_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disembark_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embark_port_id: Mapped[int | None] = mapped_column(ForeignKey("ports.id"))
    disembark_port_id: Mapped[int | None] = mapped_column(ForeignKey("ports.id"))
    notes: Mapped[str | None] = mapped_column(Text)


class CrewCertification(Base):
    __tablename__ = "crew_certifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    crew_member_id: Mapped[int] = mapped_column(ForeignKey("crew_members.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    # 'stcw_basic', 'stcw_advanced', 'medical', 'gmdss', 'visa_us', 'visa_br', ...
    reference: Mapped[str | None] = mapped_column(String(100))
    issued_at: Mapped[_date | None] = mapped_column(Date)
    expires_at: Mapped[_date | None] = mapped_column(Date)
    document_url: Mapped[str | None] = mapped_column(String(500))


class CrewLeave(Base):
    """Leave / time off (CP, RTT, maladie, etc.)."""

    __tablename__ = "crew_leaves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    crew_member_id: Mapped[int] = mapped_column(ForeignKey("crew_members.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    # 'cp' | 'rtt' | 'maladie' | 'maternite' | 'paternite' | 'sans_solde'
    start_date: Mapped[_date] = mapped_column(Date, nullable=False)
    end_date: Mapped[_date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="requested", nullable=False)
    # 'requested' | 'approved' | 'rejected' | 'cancelled'
    reason: Mapped[str | None] = mapped_column(Text)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
