"""Escale ticketing — incident management for port calls.

Workflow : open → in_progress → pending_external | resolved → closed
                                                  → cancelled (any state)

SLA target_at is computed at creation from the priority and never changes
after that. sla_breached is recomputed on every read (no cron needed for V3.0).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)

    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"), index=True)
    port_id: Mapped[int | None] = mapped_column(ForeignKey("ports.id"))

    category: Mapped[str] = mapped_column(String(40), nullable=False)
    priority: Mapped[str] = mapped_column(String(4), nullable=False)  # 'P1','P2','P3'
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    external_contact: Mapped[str | None] = mapped_column(String(200))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_reason: Mapped[str | None] = mapped_column(Text)

    sla_target_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_breached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    comments: Mapped[list["TicketComment"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan",
        order_by="TicketComment.created_at",
    )

    __table_args__ = (
        Index("ix_tickets_status", "status"),
        Index("ix_tickets_priority_status", "priority", "status"),
        Index("ix_tickets_assigned", "assigned_to_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Ticket {self.reference} {self.priority}/{self.status}>"


class TicketComment(Base):
    __tablename__ = "ticket_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    author_name: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ticket: Mapped[Ticket] = relationship(back_populates="comments")
