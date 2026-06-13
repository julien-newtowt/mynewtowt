"""Variables CO₂ paramétrables et versionnées (ENV-02).

Modèle append-only : modifier une variable INSÈRE une nouvelle ligne
(``is_current=True`` + ``effective_date``) et bascule l'ancienne ligne
courante à ``is_current=False``. L'historique n'est jamais supprimé —
chaque valeur reste traçable (source, auteur, date d'effet).

Noms de variables consommés par ``app.services.co2`` :
- ``towt_co2_ef``         — facteur d'émission TOWT (gCO₂/t.km)
- ``conventional_co2_ef`` — facteur d'émission conventionnel (gCO₂/t.km)
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Co2Variable(Base):
    __tablename__ = "co2_variables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20))
    # Référence de la valeur, ex. "Audit vérificateur 2026" / "IMO 4th GHG Study".
    source: Mapped[str | None] = mapped_column(String(200))
    effective_date: Mapped[_date] = mapped_column(Date, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Co2Variable {self.name}={self.value} current={self.is_current}>"
