"""Facteurs d'émission multi-GES, versionnés (MRV lot 1).

Référentiel **par carburant** (contrairement à ``co2_variables`` qui est un
registre scalaire nom→valeur, cf. choix argumenté §2.5 du plan) : une ligne
porte les 4 grandeurs nécessaires au grand livre d'émissions cible — CO₂/CH₄/
N₂O TtW (tank-to-wake, MEPC.391(81)) + WtT FuelEU (well-to-tank, affiché
séparément, jamais sommé au TtW) — avec une **fenêtre de validité**
(``valid_from``/``valid_to``) adaptée au multi-carburant futur (FuelEU
biocarburants, LNG…), en plus du flag ``is_current`` pour la valeur en
vigueur sans date précisée.

Append-only, comme ``app.models.co2_variable.Co2Variable`` : modifier un
facteur INSÈRE une nouvelle ligne et bascule l'ancienne à
``is_current=False``. L'historique n'est jamais supprimé.

Consommé par ``services.referential_env.resolve_emission_factor`` (cache 60 s
+ fail-closed sur les constantes codées) et, par délégation, par
``services.co2.get_do_co2_factor``.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EmissionFactor(Base):
    __tablename__ = "emission_factors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fuel_type: Mapped[str] = mapped_column(
        String(20), default="MDO", server_default="MDO", nullable=False, index=True
    )
    # TtW (tank-to-wake) — combustion du carburant. MEPC.391(81) pour le MDO.
    ef_co2_kg_per_kg: Mapped[Decimal] = mapped_column(Numeric(15, 9), nullable=False)
    ef_ch4_kg_per_kg: Mapped[Decimal] = mapped_column(Numeric(15, 9), nullable=False)
    ef_n2o_kg_per_kg: Mapped[Decimal] = mapped_column(Numeric(15, 9), nullable=False)
    # WtT (well-to-tank, FuelEU) — grammage CO2eq par MJ, grandeur distincte
    # jamais additionnée directement au TtW ci-dessus (unités différentes).
    wtt_gco2eq_per_mj: Mapped[Decimal] = mapped_column(Numeric(15, 9), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(200))
    valid_from: Mapped[_date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[_date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EmissionFactor {self.fuel_type} co2={self.ef_co2_kg_per_kg} "
            f"current={self.is_current}>"
        )
