"""Overrides de la matrice RBAC rôles × modules (ARC-04).

Chaque ligne est un écart par rapport à la matrice codée en dur
``app.permissions._MATRIX`` (qui reste la valeur par défaut). Une cellule
égale au défaut n'est PAS stockée ici — l'admin supprime la ligne quand
elle redevient identique au défaut.

``level`` : "" (aucun accès) | "C" | "CM" | "CMS".
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role", "module", name="uq_role_permissions_role_module"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    module: Mapped[str] = mapped_column(String(40), nullable=False)
    level: Mapped[str] = mapped_column(String(3), default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by: Mapped[str | None] = mapped_column(String(100))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RolePermission {self.role}/{self.module}={self.level!r}>"
