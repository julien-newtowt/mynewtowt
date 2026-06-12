"""Label Anemos — certificat de transport décarboné par voilier cargo.

Émis par booking à l'arrivée (status ∈ discharged|delivered). Atteste du
tonnage transporté, de la distance, et du CO₂ évité par rapport au
shipping conventionnel équivalent.

Note V3.6 : ce certificat a été renommé en "Label Anemos" (anciennement
"Certificat CO₂"). Les colonnes co2_* restent — ce sont des métriques
physiques, pas du branding. La table s'appelle désormais
``anemos_certificates`` (cf. migration 20260519_0012).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AnemosCertificate(Base):
    __tablename__ = "anemos_certificates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)

    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id"), index=True)
    client_account_id: Mapped[int] = mapped_column(
        ForeignKey("client_accounts.id"), nullable=False, index=True
    )
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"))

    tonnage_transported_t: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    distance_nm: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    co2_emitted_kg: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    co2_conventional_kg: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    co2_avoided_kg: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)

    # ENV-03 — traçabilité du calcul : 'declared' (consommations réelles
    # déclarées à bord) ou 'theoretical' (facteur forfaitaire 1,5 g/t·km) ;
    # distance issue de 'noon_reports' (réel parcouru) ou 'planned'.
    method: Mapped[str | None] = mapped_column(String(20))
    distance_source: Mapped[str | None] = mapped_column(String(20))

    pdf_url: Mapped[str | None] = mapped_column(String(500))

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
