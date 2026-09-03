"""Voyage segment (leg)  backbone of the planning and booking system."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    event,
    func,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.voyage_highlight import VoyageHighlight
    from app.models.voyage_photo import VoyagePhoto


LEG_ORIGINS: tuple[str, ...] = ("newtowt", "towt_archive")
LEG_ORIGIN_TOWT = "towt_archive"

# Clé ``session.info`` autorisant explicitement l'écriture d'un leg d'archive
# (scripts de reprise/correction uniquement — jamais posée par une route).
LEG_ARCHIVE_WRITE_KEY = "allow_towt_archive_write"


class Leg(Base):
    __tablename__ = "legs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    vessel_id: Mapped[int] = mapped_column(ForeignKey("vessels.id"), nullable=False, index=True)
    departure_port_id: Mapped[int] = mapped_column(ForeignKey("ports.id"), nullable=False)
    arrival_port_id: Mapped[int] = mapped_column(ForeignKey("ports.id"), nullable=False)

    etd_ref: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    eta_ref: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    etd: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    eta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    atd: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ata: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(20), default="planned", nullable=False)

    # Origine du leg (reprise d'historique TOWT, ADR-014). ``newtowt`` = leg
    # vécu dans l'ERP ; ``towt_archive`` = voyage de l'ancienne compagnie repris
    # depuis les archives (Excel des traversées). Un leg d'archive est un FAIT :
    # lecture seule (``services.planning.assert_leg_mutable``), exclu de la
    # renumérotation des codes, filtrable dans /planning. Son ``leg_code`` est
    # le TRIP CODE TOWT d'origine (clé de rapprochement avec les noon reports
    # et l'ancien tableau de bord), jamais recalculé.
    origin: Mapped[str] = mapped_column(
        String(20), default="newtowt", server_default="newtowt", nullable=False
    )

    # Booking platform fields
    is_bookable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    public_capacity_palettes: Mapped[int | None] = mapped_column(Integer)
    public_price_per_palette_eur: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    booking_open_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    booking_close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Optional per-leg overrides for ETA computation. NULL => use the
    # vessel default (vessel.default_speed_kn / vessel.default_elongation).
    transit_speed_kn: Mapped[float | None] = mapped_column()
    elongation_coef: Mapped[float | None] = mapped_column()

    # Dure d'escale planifie  l'arrive (heures). Sert au planning :
    # le leg suivant du mme navire commence aprs ETA + port_stay_planned_hours.
    port_stay_planned_hours: Mapped[int | None] = mapped_column(Integer)

    # Distance orthodromique POLPOD (milles nautiques). Calcule par
    # haversine et persiste pour alimenter le certificat Anemos (CO vit).
    distance_nm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))

    # Escale closure lock  l'escale (oprations + shifts dockers) est
    # par-leg ; le verrou vit donc sur le leg. Une fois verrouille, les
    # endpoints create/edit/start/end/delete d'escale refusent toute
    # modification (cf. escale_router._assert_escale_unlocked).
    escale_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escale_locked_by: Mapped[str | None] = mapped_column(String(100))

    # Voyage closure workflow (submitted by captain  reviewed by ops  approved by manager)
    closure_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_submitted_by: Mapped[str | None] = mapped_column(String(100))
    closure_reviewed_by: Mapped[str | None] = mapped_column(String(100))
    closure_notes: Mapped[str | None] = mapped_column(Text)

    # Fin OPÉRATIONNELLE du voyage (PLN-SEQ) : posée quand le leg suivant du
    # même navire déclare son départ — le navire a quitté le quai, ce leg est
    # terminé même si la clôture administrative (closure_*) n'est pas encore
    # approuvée. Garantit « un seul leg actif par navire ». Jamais écrite
    # ailleurs que dans ``services.voyage_transitions``.
    voyage_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
        Index("ix_legs_etd", "etd"),
        Index("ix_legs_status", "status"),
        Index("ix_legs_bookable", "is_bookable"),
        Index("ix_legs_origin", "origin"),
    )

    # Relations pour Carnet de Bord ANEMOS
    highlights: Mapped[list[VoyageHighlight]] = relationship(
        "VoyageHighlight", back_populates="leg", cascade="all, delete-orphan"
    )
    photos: Mapped[list[VoyagePhoto]] = relationship(
        "VoyagePhoto", back_populates="leg", cascade="all, delete-orphan"
    )

    @property
    def is_archive(self) -> bool:
        """Leg repris des archives TOWT (lecture seule, ADR-014)."""
        return self.origin == LEG_ORIGIN_TOWT

    @property
    def phase(self) -> str:
        """Phase opérationnelle du voyage, dérivée du réel — jamais stockée.

        ``planifie`` → ``en_mer`` (départ déclaré, ATD posé) → ``a_quai``
        (arrivée déclarée, ATA posée) → ``termine`` (le leg suivant a
        appareillé — ``voyage_completed_at`` — ou clôture approuvée).
        ``annule`` est porté par ``status`` (décision humaine, sticky).
        Complète ``status`` (machine à états stockée, cf.
        ``services.planning.refresh_leg_status``) sans le remplacer : les
        consommateurs de ``status`` restent valides, l'UI affiche la phase.
        """
        if self.status == "cancelled":
            return "annule"
        if self.status == "completed" or self.voyage_completed_at is not None:
            return "termine"
        if self.ata is not None:
            return "a_quai"
        if self.atd is not None:
            return "en_mer"
        return "planifie"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Leg {self.leg_code} {self.etd.date()}{self.eta.date()}>"


@event.listens_for(Session, "before_flush")
def _refuse_archive_leg_writes(session: Session, flush_context, instances) -> None:
    """Garde ORM (ADR-014) : aucun UPDATE/DELETE d'un leg d'archive TOWT.

    Filet de sécurité derrière ``services.planning.assert_leg_mutable`` : il
    attrape tout écrivain qui n'appelle pas la garde (scénarios, décalage d'ETA
    bord, futurs chemins). La création (``session.new``) reste libre — c'est
    ainsi que la reprise insère l'archive. Échappement explicite :
    ``session.info[LEG_ARCHIVE_WRITE_KEY] = True`` (scripts uniquement).
    """
    if session.info.get(LEG_ARCHIVE_WRITE_KEY):
        return
    offenders = [
        obj
        for obj in list(session.dirty) + list(session.deleted)
        if isinstance(obj, Leg)
        and obj.origin == LEG_ORIGIN_TOWT
        and (obj in session.deleted or session.is_modified(obj))
    ]
    if offenders:
        from app.services.planning import LegArchivedError

        codes = ", ".join(sorted(o.leg_code for o in offenders))
        raise LegArchivedError(
            f"Écriture refusée sur un leg d'archive TOWT (lecture seule, ADR-014) : {codes}"
        )
