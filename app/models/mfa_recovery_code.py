"""MFA recovery codes — single-use, hashed at rest.

Permet à un user qui a perdu son téléphone (auth app supprimée) de
récupérer l'accès. À l'activation MFA, 10 codes uniques sont générés,
affichés UNE seule fois en clair, et stockés hashés en DB. Chaque code
est consommable une fois (``used_at`` posé après usage).

Modèle polymorphe : ``owner_type`` ∈ {"client", "staff"} + ``owner_id``
pointe vers la table correspondante (``client_accounts`` ou ``users``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MfaRecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "client" | "staff"
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # SHA-256(code_clear) hex, 64 chars. Le code en clair n'est jamais stocké.
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_mfa_recovery_owner", "owner_type", "owner_id"),)
