"""Cargo sailing vessel  referenced by Leg, Booking and capacity rules.

Pour le Carnet de Bord ANEMOS, ce modle contient les spcifications techniques
fixes du navire (rfrentiel - REF).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Statuts de flotte (P4). « operational » = en service ; « under_construction »
# = commandé/en chantier (livraison à venir).
VESSEL_BUILD_STATUSES: tuple[str, ...] = ("operational", "under_construction")


class Vessel(Base):
    __tablename__ = "vessels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(4), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Classe de navire  pilote le rfrentiel d'arrimage (capacits &
    # rsistances par zone). Tous les sister-ships partagent la mme classe
    # (ex. Anemos / Artemis / Atlantis = "phoenix").
    vessel_class: Mapped[str] = mapped_column(
        String(40), default="phoenix", nullable=False, server_default="phoenix"
    )
    imo_number: Mapped[str | None] = mapped_column(String(20))
    flag: Mapped[str | None] = mapped_column(String(2))
    dwt: Mapped[float | None] = mapped_column(Float)
    # Capacité commerciale unique (palettes EPAL réservables) = capacité
    # physique de cale du référentiel stowage Phoenix (P4, arbitrage direction).
    capacity_palettes: Mapped[int] = mapped_column(Integer, default=978, nullable=False)
    default_speed_kn: Mapped[float] = mapped_column(Float, default=8.0, nullable=False)
    default_elongation: Mapped[float] = mapped_column(Float, default=1.15, nullable=False)
    opex_daily_sea_eur: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Statut de flotte (P4) : navire en service ou en construction. Source de
    # vérité du récit « 2 en opération, 4 en construction » (cf.
    # VESSEL_BUILD_STATUSES) pour l'ERP et, à terme, la vitrine.
    build_status: Mapped[str] = mapped_column(
        String(20), default="operational", server_default="operational", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # =========================================================================
    # Champs pour le Carnet de Bord ANEMOS (REF - rfrentiel navire)
    # =========================================================================

    # Dimensions
    loa_m: Mapped[float | None] = mapped_column(
        Float, comment="Longueur hors-tout (Length Overall) en mtres"
    )
    beam_m: Mapped[float | None] = mapped_column(Float, comment="Largeur (Beam) en mtres")
    height_m: Mapped[float | None] = mapped_column(Float, comment="Hauteur totale en mtres")
    mast_height_m: Mapped[float | None] = mapped_column(Float, comment="Hauteur de mt en mtres")
    draft_max_m: Mapped[float | None] = mapped_column(
        Float, comment="Tirant d'eau maximal en mtres"
    )

    # Voilure
    sail_area_sqm: Mapped[float | None] = mapped_column(
        Float, comment="Surface totale de voilure en m2"
    )

    # Capacits
    capacity_barriques: Mapped[int | None] = mapped_column(Integer, comment="Capacit en barriques")
    capacity_pax: Mapped[int | None] = mapped_column(Integer, comment="Capacit en passagers")

    # Identification et administration
    home_port: Mapped[str | None] = mapped_column(String(100), comment="Port d'attache")
    port_of_registry: Mapped[str | None] = mapped_column(
        String(100), comment="Port d'immatriculation"
    )

    # Dates de construction
    build_start_date: Mapped[date | None] = mapped_column(
        Date, comment="Date de dbut de construction"
    )
    build_end_date: Mapped[date | None] = mapped_column(
        Date, comment="Date de fin de construction / mise en service"
    )

    # Description pour le Carnet de Bord
    description: Mapped[str | None] = mapped_column(
        Text, comment="Description du navire pour le Carnet de Bord"
    )
    crew_description: Mapped[str | None] = mapped_column(
        Text, comment="Description de l'quipage type pour ce navire"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Vessel {self.code} {self.name}>"
