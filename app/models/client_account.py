"""Client account — authenticated customers of NEWTOWT.

Distinct from staff users (different table, different cookie, different
permission space). A ClientAccount belongs to a company; future
`client_users` table will support multiple users per company (V3.1).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ClientAccount(Base):
    __tablename__ = "client_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    vat_number: Mapped[str | None] = mapped_column(String(50))
    country: Mapped[str | None] = mapped_column(String(2))
    billing_address: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(5), default="fr")
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_secret: Mapped[str | None] = mapped_column(String(64))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Segment helps pricing & service rules: occasional / recurring / key_account
    segment: Mapped[str] = mapped_column(String(20), default="occasional", nullable=False)

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
        Index("ix_client_accounts_segment", "segment"),
        Index("ix_client_accounts_country", "country"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ClientAccount {self.email} ({self.company_name})>"
