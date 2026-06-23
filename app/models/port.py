"""Port  UN LOCODE referenced by Legs and Bookings.

Pour le Carnet de Bord ANEMOS, ce modle contient les descriptions
ditoriales des ports (MAN - curation humaine).
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Port(Base):
    __tablename__ = "ports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    locode: Mapped[str] = mapped_column(String(5), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    timezone: Mapped[str | None] = mapped_column(String(50))
    # Provenance: 'manual' (seeded), 'datagouv' (FR open data),
    # 'unlocode' (UN/LOCODE dataset), 'user' (operator-added).
    source: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)
    function_code: Mapped[str | None] = mapped_column(String(8))
    subdivision: Mapped[str | None] = mapped_column(String(8))
    # Admins can hide a port without deleting it (preserves FK history).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # =========================================================================
    # Champs pour le Carnet de Bord ANEMOS (MAN - curation humaine)
    # =========================================================================

    # Description ditoriale pour le Carnet de Bord
    description: Mapped[str | None] = mapped_column(
        Text, comment="Description du port pour le Carnet de Bord ANEMOS"
    )

    # Catgorie de port pour le Carnet de Bord
    anemos_category: Mapped[str | None] = mapped_column(
        String(50),
        comment="Catgorie pour le Carnet de Bord (ex: 'departure', 'arrival', 'escale')",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Port {self.locode} {self.name}>"
