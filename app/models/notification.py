"""Dashboard notifications — petits messages alimentant le notif-center.

Types codifiés :
    new_order, new_cargo_message, eosp, sosp,
    new_claim, eta_shift, packing_to_review, leg_locked
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


NOTIFICATION_TYPES = (
    "new_order", "new_cargo_message", "eosp", "sosp",
    "new_claim", "eta_shift", "packing_to_review", "leg_locked",
    "info",
    # Cycle de vie booking — côté client
    "booking_submitted", "booking_confirmed", "booking_loaded",
    "booking_at_sea", "booking_discharged", "booking_delivered",
    "booking_cancelled", "invoice_issued", "anemos_issued",
    "new_booking_message",
)

# Icônes par type (emoji)
NOTIFICATION_ICONS: dict[str, str] = {
    "new_order":         "📦",
    "new_cargo_message": "💬",
    "eosp":              "⚓",
    "sosp":              "⛵",
    "new_claim":         "⚠️",
    "eta_shift":         "🕐",
    "packing_to_review": "📋",
    "leg_locked":        "🔒",
    "info":              "ℹ️",
    "booking_submitted": "📝",
    "booking_confirmed": "✅",
    "booking_loaded":    "📦",
    "booking_at_sea":    "⛵",
    "booking_discharged": "⚓",
    "booking_delivered": "🎉",
    "booking_cancelled": "❌",
    "invoice_issued":    "🧾",
    "anemos_issued":     "🌿",
    "new_booking_message": "💬",
}


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    target_role: Mapped[str | None] = mapped_column(String(40))  # broadcast par rôle
    target_client_id: Mapped[int | None] = mapped_column(
        ForeignKey("client_accounts.id"), index=True
    )  # notification destinée à un compte client (espace /me)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(500))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    @property
    def icon(self) -> str:
        return NOTIFICATION_ICONS.get(self.type, "ℹ️")

    @property
    def type_label(self) -> str:
        return self.type.replace("_", " ").title()
