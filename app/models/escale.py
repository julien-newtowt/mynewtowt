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
    "passage_paf",
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
    # ESC-04 — intervenant (nom/société qui réalise l'opération) : affiché
    # partout en V2 (manutentionnaire, agent, prestataire…).
    intervenant: Mapped[str | None] = mapped_column(String(200))
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

    # ESC-04 — durées dérivées (heures) des bornes prévue/réelle. Reprise V2 :
    # mêmes accesseurs ``planned_duration_hours`` / ``actual_duration_hours``,
    # calculés à la volée plutôt que stockés (toujours cohérents).
    @staticmethod
    def _duration_h(start: datetime | None, end: datetime | None) -> float | None:
        if start is None or end is None:
            return None
        # Normalise les tz (SQLite renvoie naïf, Postgres aware) avant le delta.
        if start.tzinfo is None and end.tzinfo is not None:
            end = end.replace(tzinfo=None)
        elif start.tzinfo is not None and end.tzinfo is None:
            start = start.replace(tzinfo=None)
        hours = (end - start).total_seconds() / 3600
        return round(hours, 2) if hours >= 0 else None

    @property
    def planned_duration_hours(self) -> float | None:
        return self._duration_h(self.planned_start, self.planned_end)

    @property
    def actual_duration_hours(self) -> float | None:
        return self._duration_h(self.actual_start, self.actual_end)


class DockerShift(Base):
    __tablename__ = "docker_shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, index=True)
    direction: Mapped[str | None] = mapped_column(String(10))  # IMPORT/EXPORT
    # B3 — cale (hold) sur laquelle travaille la vacation, alignée sur le
    # plan d'arrimage (cf. ``app.models.stowage.HOLDS`` : "AR"/"AV").
    # NULL = non spécifiée. Permet de relier planning dockers ↔ stowage.
    hold: Mapped[str | None] = mapped_column(String(10))  # AR/AV (stowage HOLDS)
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

    # ESC-05 — productivité dockers (palettes/heure) calculée à la volée depuis
    # les colonnes existantes (target/done + bornes planifiées/réelles).
    @property
    def planned_rate(self) -> float | None:
        if self.palettes_target and self.planned_start and self.planned_end:
            hours = (self.planned_end - self.planned_start).total_seconds() / 3600
            return round(self.palettes_target / hours, 1) if hours > 0 else 0.0
        return None

    @property
    def actual_rate(self) -> float | None:
        if self.palettes_done and self.actual_start and self.actual_end:
            hours = (self.actual_end - self.actual_start).total_seconds() / 3600
            return round(self.palettes_done / hours, 1) if hours > 0 else 0.0
        return None

    @property
    def rate_delta_pct(self) -> float | None:
        pr, ar = self.planned_rate, self.actual_rate
        if pr and ar and pr > 0:
            return round((ar - pr) / pr * 100, 1)
        return None


# Garde-fou d'intégrité : toute cible du mapping FLX-04 doit exister dans
# la liste réglementaire SOF_EVENT_TYPES (sinon le SOF auto-généré serait
# rejeté par captain_router). Vérifié au chargement du module.
from app.models.sof_event import SOF_EVENT_TYPES as _SOF_EVENT_TYPES  # noqa: E402

_unknown_sof = set(ESCALE_ACTION_TO_SOF.values()) - set(_SOF_EVENT_TYPES)
assert not _unknown_sof, f"ESCALE_ACTION_TO_SOF cible des SOF inconnus: {_unknown_sof}"
