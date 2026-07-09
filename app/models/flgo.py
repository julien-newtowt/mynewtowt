"""FLGO (Marad/MaraSoft) — relevés fuel/lube/gas-oil, LECTURE SEULE (MRV LOT 7).

FLGO = jaugeage des cuves de soutage relevé dans Marad (mesures périodiques
"Measurement" + réceptions "Received" lors d'un soutage). mynewtowt **importe**
ces relevés (API Marad en direct + repli import xlsx manuel des exports IHM)
pour un **rapprochement indépendant** avec les données déclarées par le bord
(ROB, conso, soutage) — jamais l'inverse : ces tables ne sont **jamais
modifiées à la main** dans mynewtowt et mynewtowt n'écrit jamais dans Marad
(cf. ``app/utils/marad.py``, ``app/services/flgo_sync.py``).

Trois tables :

- ``FlgoReading`` — un relevé (jaugeage "measurement" ou réception "received")
  pour un navire/produit/horodatage. Clé naturelle anti-doublon
  ``UNIQUE(vessel_id, reading_datetime, action_type, product_name)`` — un
  re-sync (API ou xlsx) ne crée jamais de doublon, seulement des mises à jour
  si les valeurs ont changé côté Marad (cf. ``services.flgo_sync``).
- ``FlgoTankCompartmentVolume`` — détail par compartiment physique d'un
  relevé (ex. "14 - GO DB B" côté Anemos, "GO BD B     Ref:14" côté Artemis —
  même compartiment n°14, libellé différent par navire). ``tank_code`` est
  **dérivé** du numéro de compartiment (préfixe ou suffixe "Ref:NN" selon le
  navire) — correspondance directe avec ``vessel_tanks.tank_code``
  (14/15/16/17/other, lot 1).
- ``FlgoVoyageConsumptionRef`` — contrôle croisé indépendant conso ME/AE par
  voyage (type feuille ``CheckConsumption`` de l'outil de travail Marad),
  alimentera les règles R15/R17 (lot 8). Schéma seul dans ce lot — aucun
  import automatisé n'est câblé pour cette table (la feuille source
  ``CheckConsumption`` observée dans le dossier client est cassée, ``#REF!``
  sur 100% des lignes ; à raccorder lot 13 depuis un dataset propre).

Convention d'unités (plan §2.7, alignée lots 1/6) : volumes en m³, masses en
tonnes.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Type d'action Marad — jaugeage périodique ("measurement") ou réception lors
# d'un soutage ("received"). D'autres valeurs peuvent exister côté Marad
# (ex. "delivered" observé dans le schéma API, jamais rencontré dans les
# exports fournis) — non contraint en base pour rester tolérant, documenté ici.
FLGO_ACTION_TYPES: tuple[str, ...] = ("measurement", "received")

# Provenance d'un relevé — API Marad en direct, ou repli import xlsx manuel
# (export IHM, cf. services.flgo_sync.import_flgo_xlsx).
FLGO_SOURCES: tuple[str, ...] = ("api", "xlsx_import")


class FlgoReading(Base):
    """Un relevé FLGO (jaugeage ou réception) — LECTURE SEULE côté Marad.

    ``reading_datetime`` est l'horodatage de l'opération Marad (tz-aware,
    normalisé UTC à l'ingestion — Marad ne documente pas de fuseau explicite,
    cf. ``services.flgo_sync._ensure_utc``). ``total_rob_m3`` peut être NULL
    (schéma API non garanti sur tous les enregistrements historiques).
    """

    __tablename__ = "flgo_readings"
    __table_args__ = (
        UniqueConstraint(
            "vessel_id",
            "reading_datetime",
            "action_type",
            "product_name",
            name="uq_flgoreading_natural_key",
        ),
        Index("ix_flgoreading_vessel", "vessel_id"),
        Index("ix_flgoreading_datetime", "reading_datetime"),
        Index("ix_flgoreading_action_type", "action_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vessel_id: Mapped[int] = mapped_column(
        ForeignKey("vessels.id", ondelete="CASCADE"), nullable=False
    )
    # measurement / received (cf. FLGO_ACTION_TYPES — non contraint en base).
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)
    product_name: Mapped[str] = mapped_column(String(80), nullable=False)
    reading_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_volume_m3: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    total_rob_m3: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    remarks: Mapped[str | None] = mapped_column(Text)
    # api / xlsx_import (cf. FLGO_SOURCES — non contraint en base).
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    compartments: Mapped[list[FlgoTankCompartmentVolume]] = relationship(
        "FlgoTankCompartmentVolume",
        back_populates="reading",
        cascade="all, delete-orphan",
        order_by="FlgoTankCompartmentVolume.id",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<FlgoReading vessel={self.vessel_id} {self.action_type} "
            f"{self.product_name} @{self.reading_datetime}>"
        )


class FlgoTankCompartmentVolume(Base):
    """Détail par compartiment physique d'un :class:`FlgoReading`.

    ``compartment_code`` conserve le libellé Marad brut (traçabilité de la
    source, formats hétérogènes par navire — cf. module docstring).
    ``tank_code`` est **dérivé** (``services.flgo_sync.derive_tank_code``) —
    jamais saisi à la main.
    """

    __tablename__ = "flgo_tank_compartment_volumes"
    __table_args__ = (
        Index("ix_flgotankvol_reading", "flgo_reading_id"),
        Index("ix_flgotankvol_tank_code", "tank_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flgo_reading_id: Mapped[int] = mapped_column(
        ForeignKey("flgo_readings.id", ondelete="CASCADE"), nullable=False
    )
    compartment_code: Mapped[str] = mapped_column(String(120), nullable=False)
    # 14 / 15 / 16 / 17 / other — correspondance directe avec vessel_tanks.tank_code.
    tank_code: Mapped[str] = mapped_column(String(10), nullable=False)
    volume_m3: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    mass_t: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))

    reading: Mapped[FlgoReading] = relationship("FlgoReading", back_populates="compartments")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<FlgoTankCompartmentVolume reading={self.flgo_reading_id} "
            f"{self.compartment_code}={self.volume_m3}>"
        )


class FlgoVoyageConsumptionRef(Base):
    """Contrôle croisé indépendant conso ME/AE par voyage (type ``CheckConsumption``).

    Schéma seul dans ce lot (cf. module docstring) — alimentera R15/R17 au
    lot 8 une fois un import fiable disponible (lot 13).
    """

    __tablename__ = "flgo_voyage_consumption_refs"
    __table_args__ = (Index("ix_flgovoyageref_leg", "leg_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id", ondelete="CASCADE"), nullable=False)
    me_consumption_t: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    ae_consumption_t: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    ecart_t: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<FlgoVoyageConsumptionRef leg={self.leg_id} "
            f"me={self.me_consumption_t} ae={self.ae_consumption_t}>"
        )
