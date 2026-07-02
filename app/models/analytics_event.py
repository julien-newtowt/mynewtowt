"""Événement d'instrumentation du tunnel de conversion (CONV-06).

Table append-only, légère : un événement par étape du parcours public/client
(landing → route → devis → réservation → confirmation). Sert à mesurer la
conversion `landing → booking` (cible ≥ 5 %), le taux `quote → booking`, le
délai `submitted → confirmed` et le % self-service.

Aucun outil tiers (Google Analytics, Segment…) — instrumentation interne,
exploitée par le tableau de bord commercial (`/dashboard/analytics/commercial`).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Événements du tunnel (cf. fiche /devis + wizard §5) + boucle B2B2C :
# `voyage_page_view` compte les consultations de la page publique de voyage
# (scans du QR imprimé sur le paquet) — North Star marketing B2B2C.
ANALYTICS_EVENTS = (
    "landing_view",
    "route_view",
    "quote_generated",
    "quote_pdf_download",
    "book_click",
    "booking_submitted",
    "account_created",
    "booking_confirmed",
    "voyage_page_view",
)


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nom de l'événement (cf. ANALYTICS_EVENTS).
    event: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    # Référence métier rattachée (leg_code, devis DEV-…, booking BK-…) si pertinent.
    reference: Mapped[str | None] = mapped_column(String(40))
    # Langue du visiteur au moment de l'événement (storytelling i18n).
    lang: Mapped[str | None] = mapped_column(String(5))
    # Canal : "public" (vitrine invité) ou "client" (espace authentifié).
    channel: Mapped[str | None] = mapped_column(String(20))
    # Détail libre court (jamais de PII en clair).
    detail: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_analytics_events_event_created", "event", "created_at"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AnalyticsEvent {self.event} {self.reference or ''}>"
