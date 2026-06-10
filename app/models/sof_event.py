"""On board — SofEvent, ETAShift, OnboardMessage, OnboardMention.

Le Statement of Facts est la chronologie réglementaire des événements
portuaires/maritimes d'un voyage. 24 types codifiés (EOSP, SOSP, NOR,
NOR_RT, PILOT_ON, PILOT_OFF, TUG_ON, TUG_OFF, FREE_PRATIQUE…).

Une partie est mappée automatiquement vers les MRVEvent (carburant)
quand applicable via `SOF_TO_MRV_MAP` (cf. services.mrv_export).
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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

SOF_EVENT_TYPES = (
    "EOSP", "SOSP",           # End / Start Of Sea Passage
    "NOR", "NOR_RT",          # Notice of Readiness (Re-Tendered)
    "FREE_PRATIQUE",
    "PILOT_ON", "PILOT_OFF",
    "TUG_ON", "TUG_OFF",
    "FIRST_LINE", "ALL_FAST",
    "GANGWAY_UP", "GANGWAY_DOWN",
    "ARRIVE_PILOT_STATION", "DEPART_PILOT_STATION",
    "ANCHORED", "WEIGH_ANCHOR",
    "BERTHED", "UNBERTHED",
    "BUNKER_START", "BUNKER_END",
    "LOADING_START", "LOADING_END",
    "DISCHARGING_START", "DISCHARGING_END",
    "DRAFT_SURVEY",
    "OTHER",
)

ETA_SHIFT_REASONS = (
    "weather",
    "mechanical",
    "port_congestion",
    "customs_delay",
    "cargo_readiness",
    "crew_change",
    "bunker_delay",
    "anchorage_wait",
    "other",
)


class SofEvent(Base):
    """Statement Of Facts — événement chronologique d'un leg.

    Document réglementaire IMO (Statement of Facts). Une fois signé par le
    commandant, l'événement devient **immuable** :
    - ``signed_at`` / ``signed_by_id`` / ``signed_by_name`` : qui & quand.
    - ``signature_hash`` : SHA-256 du tuple (event_type, occurred_at, label,
      lat, lon, notes, signed_by_id, signed_at) — détecte toute altération
      post-signature.
    - ``is_locked = True`` après signature → backend rejette tout UPDATE/
      DELETE (cf. captain_router.sign_sof_event / require_unlocked).
    """

    __tablename__ = "sof_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(
        ForeignKey("legs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str | None] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    port_id: Mapped[int | None] = mapped_column(ForeignKey("ports.id"))
    latitude: Mapped[float | None] = mapped_column()
    longitude: Mapped[float | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    recorded_by_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Signature commandant (réglementaire IMO)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    signed_by_name: Mapped[str | None] = mapped_column(String(200))
    signature_hash: Mapped[str | None] = mapped_column(String(64))
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class EtaShift(Base):
    """Décalage d'ETA — motif obligatoire pour traçabilité."""

    __tablename__ = "eta_shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(
        ForeignKey("legs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    previous_eta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    new_eta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    declared_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    declared_by_name: Mapped[str | None] = mapped_column(String(200))
    declared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OnboardMessage(Base):
    """Message équipage / armateur — supporte @mentions et bot MYTOWT_BOT."""

    __tablename__ = "onboard_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"), index=True)
    vessel_id: Mapped[int | None] = mapped_column(ForeignKey("vessels.id"), index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    author_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    mentions: Mapped[list[OnboardMessageMention]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class OnboardMessageMention(Base):
    __tablename__ = "onboard_message_mentions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("onboard_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mentioned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    mentioned_text: Mapped[str] = mapped_column(String(80), nullable=False)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    message: Mapped[OnboardMessage] = relationship(back_populates="mentions")


class CargoDocument(Base):
    """Documents cargo générés à bord (NOR/NOR_RT/LOP/Mate's Receipt…)."""

    __tablename__ = "cargo_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(
        ForeignKey("legs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # 'NOR', 'NOR_RT', 'LOP_GENERAL', 'LOP_DRAFT', 'MATES_RECEIPT', 'OTHER'
    reference: Mapped[str | None] = mapped_column(String(100))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    party_name: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
