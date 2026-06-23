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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.booking import Booking

TOKEN_VALIDITY_DAYS = 90

# CARGO-02 — champs requis pour une packing list « complète » (mentions
# obligatoires du connaissement). Sert au calcul de ``completion_pct``.
_BATCH_REQUIRED_FIELDS: tuple[str, ...] = (
    "shipper_name",
    "shipper_address",
    "shipper_city",
    "shipper_country",
    "consignee_name",
    "consignee_address",
    "consignee_city",
    "consignee_country",
    "type_of_goods",
    "pallet_count",
    "weight_kg",
)


def generate_token() -> str:
    return uuid.uuid4().hex[:24]


def default_token_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=TOKEN_VALIDITY_DAYS)


class PackingList(Base):
    __tablename__ = "packing_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Une packing list provient soit d'une commande (rail A, remplissage
    # opérateur), soit d'un booking client (rail B, remplissage via portail).
    # Exactement l'une des deux FK est renseignée (cf. CheckConstraint XOR).
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("commercial_orders.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    token: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, default=generate_token, index=True
    )
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=default_token_expiry
    )
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # Date de chargement prévue (= ETD du leg). Alimente la cascade de dates
    # (cf. services/date_cascade) quand l'ETD du leg est décalé.
    loading_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    batches: Mapped[list[PackingListBatch]] = relationship(
        back_populates="packing_list",
        cascade="all, delete-orphan",
        order_by="PackingListBatch.id",
    )
    # Rail B : booking source de la packing list. Relation unidirectionnelle
    # (le modèle Booking n'a pas de back-reference) ; lazy par défaut, à
    # eager-loader explicitement (selectinload) dans un contexte async.
    booking: Mapped[Booking | None] = relationship("Booking")

    __table_args__ = (
        # XOR : exactement une des deux origines est renseignée. Garantit
        # l'invariant « une PL appartient à une commande OU à un booking ».
        CheckConstraint(
            "(order_id IS NULL) <> (booking_id IS NULL)",
            name="ck_packing_lists_order_xor_booking",
        ),
    )

    @property
    def is_locked(self) -> bool:
        return self.status == "locked"

    @property
    def batch_count(self) -> int:
        return len(self.batches) if self.batches else 0

    @property
    def completion_pct(self) -> int:
        # CARGO-02 — complétude documentaire douanière : moyenne du taux de
        # remplissage des champs requis du connaissement sur tous les batches.
        if not self.batches:
            return 0
        total = len(self.batches) * len(_BATCH_REQUIRED_FIELDS)
        filled = sum(
            1
            for b in self.batches
            for f in _BATCH_REQUIRED_FIELDS
            if (v := getattr(b, f, None)) is not None and str(v).strip()
        )
        return round(100 * filled / total) if total else 0


class PackingListBatch(Base):
    __tablename__ = "packing_list_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    packing_list_id: Mapped[int] = mapped_column(
        ForeignKey("packing_lists.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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

    # CARGO-02 — parties du connaissement (mentions obligatoires du BL).
    shipper_name: Mapped[str | None] = mapped_column(String(200))
    shipper_address: Mapped[str | None] = mapped_column(Text)
    shipper_postal: Mapped[str | None] = mapped_column(String(20))
    shipper_city: Mapped[str | None] = mapped_column(String(100))
    shipper_country: Mapped[str | None] = mapped_column(String(100))
    notify_name: Mapped[str | None] = mapped_column(String(200))
    notify_address: Mapped[str | None] = mapped_column(Text)
    notify_postal: Mapped[str | None] = mapped_column(String(20))
    notify_city: Mapped[str | None] = mapped_column(String(100))
    notify_country: Mapped[str | None] = mapped_column(String(100))
    consignee_name: Mapped[str | None] = mapped_column(String(200))
    consignee_address: Mapped[str | None] = mapped_column(Text)
    consignee_postal: Mapped[str | None] = mapped_column(String(20))
    consignee_city: Mapped[str | None] = mapped_column(String(100))
    consignee_country: Mapped[str | None] = mapped_column(String(100))

    # CARGO-02 — marchandise (BL / douane).
    type_of_goods: Mapped[str | None] = mapped_column(String(200))
    description_of_goods: Mapped[str | None] = mapped_column(Text)

    # CARGO-01 — numérotation Bill of Lading persistante (ex. TUAW_1CFRBR6_001).
    # Unique : interdit deux BL au même numéro (anti-doublon au niveau base).
    bl_number: Mapped[str | None] = mapped_column(String(50), unique=True, index=True)
    bl_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
        nullable=False,
        index=True,
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
        nullable=True,
        index=True,
    )
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
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
        nullable=False,
        index=True,
    )
    sender: Mapped[str] = mapped_column(String(20), nullable=False)  # 'client' | 'staff'
    sender_name: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
