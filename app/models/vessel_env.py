"""Référentiel navire — cuves, moteurs, hydrostatiques (MRV lot 1).

Socle paramétrable de la refonte du reporting environnemental (H2/H3) :
chaque navire porte son propre référentiel de cuves de soutage, de moteurs
(avec groupe d'agrégation ME/AE) et — à terme — sa courbe hydrostatique
(tirant d'eau ↔ déplacement, formule Cargo MRV EU 2016/1928).

Ces tables sont volontairement **vessel-agnostic dans leur schéma** mais
**seedées par navire** via ``services.referential_env.ensure_vessel_env_defaults``
(pas dans la migration : les ids navires varient selon l'environnement).

Règle d'agrégation ME/AE (confirmée dictionnaire de données §2.1) :
``PME``/``SME`` → groupe ``ME`` ; ``FWD_GEN``/``AFT_GEN`` → groupe ``AE`` ;
les groupes électrogènes de ligne d'arbre (``PORT_SHAFT_GEN`` /
``STBD_SHAFT_GEN``) n'appartiennent à aucun groupe (``NULL``) et sont donc
exclus des totaux MRV.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Codes de cuve — numérotation machine du bord (14/15/16/17) + fourre-tout
# "other" pour toute cuve non couverte par cette numérotation. 5 valeurs =
# les 5 cuves seedées par défaut (cf. referential_env.DEFAULT_TANK_CODES).
TANK_CODES: tuple[str, ...] = ("14", "15", "16", "17", "other")

# Rôles moteur — 6 valeurs = les 6 moteurs seedés par défaut par navire.
ENGINE_ROLES: tuple[str, ...] = (
    "PME",
    "SME",
    "FWD_GEN",
    "AFT_GEN",
    "PORT_SHAFT_GEN",
    "STBD_SHAFT_GEN",
)

# Groupes d'agrégation MRV. ``None`` = exclu des totaux (ligne d'arbre).
ENGINE_GROUPS: tuple[str, ...] = ("ME", "AE")


class VesselTank(Base):
    """Une cuve de soutage d'un navire (référentiel — pas un relevé)."""

    __tablename__ = "vessel_tanks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vessel_id: Mapped[int] = mapped_column(
        ForeignKey("vessels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tank_code: Mapped[str] = mapped_column(String(10), nullable=False)
    # Capacité nominale (m³) — plan officiel du navire à obtenir (Q11) ; le
    # proxy "max observé FLGO" documenté dans le dossier source est jugé
    # impropre (R23-v2 reste en sévérité Info tant que cette donnée manque).
    capacity_m3: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VesselTank vessel={self.vessel_id} code={self.tank_code}>"


class VesselEngine(Base):
    """Un moteur/groupe électrogène d'un navire (référentiel)."""

    __tablename__ = "vessel_engines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vessel_id: Mapped[int] = mapped_column(
        ForeignKey("vessels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    engine_role: Mapped[str] = mapped_column(String(30), nullable=False)
    # ME (PME+SME) / AE (FWD_GEN+AFT_GEN) / NULL (lignes d'arbre, hors total).
    engine_group: Mapped[str | None] = mapped_column(String(10))
    display_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VesselEngine vessel={self.vessel_id} role={self.engine_role} group={self.engine_group}>"


class VesselHydrostatics(Base):
    """Point de courbe hydrostatique (tirant d'eau ↔ déplacement).

    Table vide pour l'instant (données navire officielles à fournir, Q11) ;
    alimentera l'interpolation linéaire de la formule Cargo MRV (EU 2016/1928)
    dans un lot ultérieur (calculs inter-événements).
    """

    __tablename__ = "vessel_hydrostatics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vessel_id: Mapped[int] = mapped_column(
        ForeignKey("vessels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    draft_m: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    displacement_m3: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VesselHydrostatics vessel={self.vessel_id} draft={self.draft_m}>"
