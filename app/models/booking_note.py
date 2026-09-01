"""Booking note — document contractuel établi à la validation d'une offre.

⚠️ **Ne pas confondre avec la « confirmation de réservation »** (le PDF
``/me/bookings/{ref}/booking-note.pdf`` de l'espace client, renommé pour lever
l'ambiguïté). Ce modèle-ci porte la *booking note* au sens maritime : le contrat
de réservation d'espace en cale, sur trame de type BIMCO CONLINEBOOKING, signé
par le chargeur (« Merchant ») et le transporteur.

Elle est **établie automatiquement** à la validation d'une offre commerciale,
avec ses champs préremplis depuis l'offre, la grille, le client et le voyage.
Le commercial peut les corriger **avant diffusion** — d'où des colonnes propres
plutôt qu'un rendu à la volée : une fois le document diffusé, ce qui a été
envoyé au client doit rester consultable tel quel, même si l'offre ou le
référentiel évoluent ensuite.

Cycle : ``brouillon`` (modifiable) → ``diffusee`` (envoyée au client, gelée).
Le gel suit la logique déjà appliquée au connaissement : un document opposable
ne se réécrit pas en place, il se remplace par une nouvelle version.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.commercial import RateOffer

BOOKING_NOTE_STATUSES = ("brouillon", "diffusee")
BOOKING_NOTE_STATUS_LABELS: dict[str, str] = {
    "brouillon": "Brouillon",
    "diffusee": "Diffusée",
}


class BookingNote(Base):
    """Booking note contractuelle rattachée à une offre commerciale validée."""

    __tablename__ = "booking_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Une offre validée donne lieu à une seule booking note (idempotence de la
    # validation : revalider ne doit pas fabriquer un second contrat).
    offer_id: Mapped[int] = mapped_column(
        ForeignKey("rate_offers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    reference: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="brouillon", nullable=False)

    # ── Champs préremplis, modifiables avant diffusion ────────────────────
    # (correspondent aux zones surlignées du gabarit Word)
    issue_place: Mapped[str | None] = mapped_column(String(120))
    issued_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agents_pod: Mapped[str | None] = mapped_column(Text)
    vessel_name: Mapped[str | None] = mapped_column(String(120))
    # « Time for shipment (about) » — l'ETD du voyage.
    time_for_shipment: Mapped[str | None] = mapped_column(String(120))
    pol_text: Mapped[str | None] = mapped_column(String(200))
    pod_text: Mapped[str | None] = mapped_column(String(200))
    merchant_name: Mapped[str | None] = mapped_column(String(200))
    merchant_contact: Mapped[str | None] = mapped_column(String(200))
    merchant_address: Mapped[str | None] = mapped_column(Text)
    merchant_email: Mapped[str | None] = mapped_column(String(200))
    # Conditions tarifaires complètes (fret, paliers, options) — texte rendu.
    freight_terms: Mapped[str | None] = mapped_column(Text)
    # Conditions de règlement, reprises des échéances de la grille.
    payment_terms: Mapped[str | None] = mapped_column(Text)
    special_terms: Mapped[str | None] = mapped_column(Text)
    cargo_description: Mapped[str | None] = mapped_column(Text)

    # ── Diffusion ─────────────────────────────────────────────────────────
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_by_id: Mapped[int | None] = mapped_column(Integer)
    issued_by_name: Mapped[str | None] = mapped_column(String(200))
    # Empreinte du document effectivement diffusé — permet de démontrer que le
    # fichier détenu par le client est bien celui qui a été émis.
    document_sha256: Mapped[str | None] = mapped_column(String(64))

    # ── Signature électronique (lot YouSign) ──────────────────────────────
    signature_provider: Mapped[str | None] = mapped_column(String(40))
    signature_request_id: Mapped[str | None] = mapped_column(String(120), index=True)
    signature_status: Mapped[str | None] = mapped_column(String(40))
    signature_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_document_path: Mapped[str | None] = mapped_column(String(500))
    signed_document_sha256: Mapped[str | None] = mapped_column(String(64))

    auto_generated: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    offer: Mapped[RateOffer] = relationship()

    @property
    def status_label(self) -> str:
        return BOOKING_NOTE_STATUS_LABELS.get(self.status, self.status)

    def is_editable(self) -> bool:
        """Modifiable tant qu'elle n'a pas été diffusée au client."""
        return self.status == "brouillon"
