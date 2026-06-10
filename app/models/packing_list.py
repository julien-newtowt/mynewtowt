"""Packing lists — internal & client portal (token-based access).

Pour chaque commande, l'expéditeur remplit en ligne sa packing list à
travers un portail public protégé par token (validité 90 jours). En
interne, l'armateur consulte, audite, verrouille et génère le Bill of
Lading + Arrival Notice.

Workflow status :
  draft → submitted → locked

Lien public : `/p/{token}` — UUID hex tronqué à 24 caractères.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


TOKEN_VALIDITY_DAYS = 90


def generate_token() -> str:
    return uuid.uuid4().hex[:24]


def default_token_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=TOKEN_VALIDITY_DAYS)


class PackingList(Base):
    __tablename__ = "packing_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("commercial_orders.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    token: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, default=generate_token, index=True
    )
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=default_token_expiry
    )
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    batches: Mapped[list["PackingListBatch"]] = relationship(
        back_populates="packing_list", cascade="all, delete-orphan",
        order_by="PackingListBatch.id",
    )

    @property
    def is_locked(self) -> bool:
        return self.status == "locked"

    @property
    def batch_count(self) -> int:
        return len(self.batches) if self.batches else 0

    @property
    def completion_pct(self) -> int:
        if not self.batches:
            return 0
        filled = sum(1 for b in self.batches if b.weight_kg is not None and b.weight_kg > 0)
        return round(100 * filled / len(self.batches)) if self.batches else 0


class PackingListBatch(Base):
    __tablename__ = "packing_list_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    packing_list_id: Mapped[int] = mapped_column(
        ForeignKey("packing_lists.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    batch_number: Mapped[int | None] = mapped_column(Integer)
    pallet_format: Mapped[str] = mapped_column(String(20), default="EPAL", nullable=False)
    pallet_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    hs_code: Mapped[str | None] = mapped_column(String(20))
    weight_kg: Mapped[float | None] = mapped_column(Float)
    cubage_m3: Mapped[float | None] = mapped_column(Float)
    length_cm: Mapped[float | None] = mapped_column(Float)
    width_cm: Mapped[float | None] = mapped_column(Float)
    height_cm: Mapped[float | None] = mapped_column(Float)
    hazardous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    imdg_class: Mapped[str | None] = mapped_column(String(20))
    un_number: Mapped[str | None] = mapped_column(String(10))
    stackable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    marks_and_numbers: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    packing_list: Mapped[PackingList] = relationship(back_populates="batches")


class PackingListAudit(Base):
    """Trace field-by-field des modifications sur les batches/PL."""

    __tablename__ = "packing_list_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    packing_list_id: Mapped[int] = mapped_column(
        ForeignKey("packing_lists.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    batch_id: Mapped[int | None] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(40), nullable=False)  # 'client' | 'staff'
    actor_name: Mapped[str | None] = mapped_column(String(200))
    field: Mapped[str] = mapped_column(String(60), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class PackingListDocument(Base):
    """Document attaché à une packing list (BL, Arrival Notice, autres pièces)."""

    __tablename__ = "packing_list_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Un document peut être rattaché à une packing list (portail expéditeur)
    # OU directement à un booking (upload client depuis l'espace /me).
    packing_list_id: Mapped[int | None] = mapped_column(
        ForeignKey("packing_lists.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # 'bl' | 'arrival_notice' | 'invoice' | 'customs' | 'msds' | 'other'
    label: Mapped[str | None] = mapped_column(String(200))
    file_path: Mapped[str | None] = mapped_column(String(500))
    file_mime: Mapped[str | None] = mapped_column(String(80))
    uploaded_by: Mapped[str | None] = mapped_column(String(200))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PortalAccessLog(Base):
    """Audit des accès au portail public (token tronqué, jamais en clair)."""

    __tablename__ = "portal_access_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portal_type: Mapped[str] = mapped_column(String(40), default="cargo", nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    packing_list_id: Mapped[int | None] = mapped_column(Integer)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    path: Mapped[str | None] = mapped_column(String(200))
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class PortalMessage(Base):
    """Messagerie bidirectionnelle entre l'armateur et le client cargo."""

    __tablename__ = "portal_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    packing_list_id: Mapped[int] = mapped_column(
        ForeignKey("packing_lists.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    sender: Mapped[str] = mapped_column(String(20), nullable=False)  # 'client' | 'staff'
    sender_name: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
