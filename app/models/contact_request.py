"""Demandes de cotation / contact issues de la vitrine publique.

La vitrine ne réalise aucune transaction. Une ``ContactRequest`` matérialise
une demande entrante (chargeur, négociant, transitaire) qui est journalisée
puis reprise par l'équipe commerciale — préparant le relais vers la
plateforme de réservation de l'extranet. Aucun paiement n'a lieu ici.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ContactRequest(Base):
    __tablename__ = "contact_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Coordonnées du demandeur
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    company: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(40))

    # Éléments de la demande (cotation)
    pol: Mapped[str | None] = mapped_column(String(120))  # port d'origine
    pod: Mapped[str | None] = mapped_column(String(120))  # port de destination
    cargo_nature: Mapped[str | None] = mapped_column(String(200))
    volume_weight: Mapped[str | None] = mapped_column(String(120))
    desired_dates: Mapped[str | None] = mapped_column(String(120))
    message: Mapped[str | None] = mapped_column(Text)

    # Métadonnées
    lang: Mapped[str | None] = mapped_column(String(12))
    # Suivi commercial : new → contacted → qualified → closed.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ContactRequest {self.id} {self.email!r} {self.pol}->{self.pod}>"
