"""Known device fingerprint — détection login depuis nouvel appareil.

Pour chaque user (polymorphe owner_type/id), on stocke une empreinte
SHA-256 du (User-Agent + IP préfixée). Quand un login arrive avec une
empreinte jamais vue, on déclenche ``security_alerts.notify_new_device_login``.

L'empreinte est volontairement grossière :
- UA normalisé (tronqué à 200 chars, lowercase) — un changement de
  version de navigateur ne crée pas un faux positif (mais un changement
  majeur OS ou navigateur, oui).
- IP : on hash le /24 (IPv4) ou /48 (IPv6) → tolérant aux NAT mobile
  qui changent l'IP toutes les heures, mais sensible aux pays / VPN.

On NE stocke ni l'IP ni l'UA en clair (RGPD light — seul le hash sert
au lookup). L'audit log peut toujours afficher l'IP (cf. activity_logs)
mais cette table-ci est purement fingerprint.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class KnownDevice(Base):
    __tablename__ = "known_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "client" | "staff"
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120))  # ex. "Chrome / macOS"
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_known_device_lookup", "owner_type", "owner_id", "fingerprint_hash"),
    )
