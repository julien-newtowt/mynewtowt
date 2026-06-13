"""Escale operations + docker shifts (Import / Export direction)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

DIRECTIONS = ("IMPORT", "EXPORT", "BOTH")
OPERATION_TYPES = ("technique", "armement", "relations_externes", "documentaire", "commercial")
OPERATION_ACTIONS = (
    "nor",
    "eosp",
    "sosp",
    "pilot_on",
    "pilot_off",
    "gangway_up",
    "gangway_down",
    "embarquement",
    "debarquement",
    "soutage",
    "avitaillement",
    "relation_presse",
    "inspection",
    "autre",
)

# FLX-04 — relier escale ↔ onboard (SOF). Une opération d'escale dont
# l'``action`` figure ici génère automatiquement l'événement SOF
# équivalent côté commandant (cf. escale_router._sync_sof_from_operation),
# évitant à l'équipage de ressaisir la chronologie portuaire.
#
# Seules les actions ayant un équivalent SOF **non ambigu** sont mappées :
# les codes cibles sont validés contre ``sof_event.SOF_EVENT_TYPES`` au
# chargement du module (assertion ci-dessous). Les actions sans équivalent
# clair (embarquement/debarquement = ambigu start/end, soutage,
# avitaillement, relation_presse, inspection, autre) sont volontairement
# omises.
ESCALE_ACTION_TO_SOF: dict[str, str] = {
    "nor": "NOR",
    "eosp": "EOSP",
    "sosp": "SOSP",
    "pilot_on": "PILOT_ON",
    "pilot_off": "PILOT_OFF",
    "gangway_up": "GANGWAY_UP",
    "gangway_down": "GANGWAY_DOWN",
}


class EscaleOperation(Base):
    __tablename__ = "escale_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, index=True)
    direction: Mapped[str | None] = mapped_column(String(10))  # IMPORT/EXPORT/BOTH
    operation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="planned", nullable=False)
    # Coût de l'opération d'escale (EUR). ``cost_forecast`` = prévisionnel,
    # ``cost_actual`` = réel constaté. Le rollup financier (FLX-05) somme
    # ``cost_actual`` quand renseigné, sinon ``cost_forecast``.
    cost_forecast: Mapped[float | None] = mapped_column()
    cost_actual: Mapped[float | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DockerShift(Base):
    __tablename__ = "docker_shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, index=True)
    direction: Mapped[str | None] = mapped_column(String(10))  # IMPORT/EXPORT
    company: Mapped[str | None] = mapped_column(String(200))
    nb_dockers: Mapped[int] = mapped_column(Integer, default=0)
    palettes_target: Mapped[int | None] = mapped_column(Integer)
    palettes_done: Mapped[int] = mapped_column(Integer, default=0)
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cost_eur: Mapped[float | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Garde-fou d'intégrité : toute cible du mapping FLX-04 doit exister dans
# la liste réglementaire SOF_EVENT_TYPES (sinon le SOF auto-généré serait
# rejeté par captain_router). Vérifié au chargement du module.
from app.models.sof_event import SOF_EVENT_TYPES as _SOF_EVENT_TYPES  # noqa: E402

_unknown_sof = set(ESCALE_ACTION_TO_SOF.values()) - set(_SOF_EVENT_TYPES)
assert not _unknown_sof, f"ESCALE_ACTION_TO_SOF cible des SOF inconnus: {_unknown_sof}"
