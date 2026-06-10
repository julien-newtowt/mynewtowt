"""Staff user (collaborateurs NEWTOWT)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40), nullable=False, default="operation")
    language: Mapped[str] = mapped_column(String(5), default="fr")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_secret: Mapped[str | None] = mapped_column(String(64))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Pour les rôles 'marins' / 'manager_maritime' : navire de rattachement.
    # Utilisé par captain_router pour filtrer les legs (RBAC row-level)
    # et par /captain/next-port pour défaut-sélectionner le prochain leg.
    assigned_vessel_id: Mapped[int | None] = mapped_column(
        ForeignKey("vessels.id"), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_users_role", "role"),
        Index("ix_users_active", "is_active"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username} role={self.role}>"
