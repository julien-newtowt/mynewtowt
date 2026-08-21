"""Matérialisation du grand livre d'émissions par voyage (MRV lot 9).

Couche 3 (restitution) — ``voyage_emission_summaries`` est la **matérialisation
par leg** du grand livre d'émissions (``services.emission_ledger``) : un cache
recalculable (``refresh_summary``), **jamais source de vérité**. Les vraies
sources restent les événements (``nav_events``) ou, en repli, les
``noon_reports`` legacy — ``source`` porte l'origine (``events`` /
``legacy_noon``).

Une ligne par voyage (``leg_id`` UNIQUE). Recalculée à la finalisation /
validation d'un événement (hook ``event_capture``) ou à la demande. Le
``factors_ref`` pointe (best-effort) sur la ligne ``emission_factors`` appliquée
— NULL si un repli codé a servi.

Convention d'unités (plan §2.7) : masses en tonnes, distance en milles, CH₄/N₂O
en grammes (jamais sommés au CO₂ TtW en tonnes), WtT en tCO₂eq (distinct du TtW).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Origine du calcul matérialisé — magasin d'événements ou repli noon legacy.
LEDGER_SOURCES: tuple[str, ...] = ("events", "legacy_noon")


class VoyageEmissionSummary(Base):
    """Cache par voyage des grandeurs du grand livre (recalculable)."""

    __tablename__ = "voyage_emission_summaries"
    __table_args__ = (Index("ix_voyage_emission_summaries_leg", "leg_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Un seul résumé par voyage (= Leg existant, consommé jamais recréé).
    leg_id: Mapped[int] = mapped_column(
        ForeignKey("legs.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # ── Consommations par périmètre (tonnes) ─────────────────────────────
    conso_me_t: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    conso_ae_t: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    conso_total_t: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    conso_escale_t: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    conso_mouillage_t: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    conso_hors_mouillage_t: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))

    # ── Émissions multi-GES (assiette hors mouillage) ────────────────────
    # CO₂ TtW en tonnes ; CH₄/N₂O en GRAMMES (jamais sommés au CO₂ t) ;
    # WtT (well-to-tank, FuelEU) en tCO₂eq — DISTINCT du TtW.
    co2_t: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    ch4_g: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    n2o_g: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    # CO₂eq TtW (GWP-100, Annexe I EU 2015/757, G13) — DISTINCT du WtT ci-dessous.
    co2eq_t: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    wtt_co2eq_t: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))

    # ── Distance / cargo ─────────────────────────────────────────────────
    distance_nm: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    cargo_bl_t: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    cargo_mrv_t: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))

    # ── Facteur d'émission (gCO₂/t·km) par méthode A/B/C ────────────────
    ef_method_a: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    ef_method_b: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    ef_method_c: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))

    # Référence (best-effort) vers la ligne ``emission_factors`` appliquée —
    # NULL si un repli codé a servi (fail-closed).
    factors_ref: Mapped[int | None] = mapped_column(
        ForeignKey("emission_factors.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VoyageEmissionSummary leg={self.leg_id} source={self.source} co2={self.co2_t}>"
