"""MRV lot 9 — grand livre d'émissions unifié : matérialisation par voyage.

Crée ``voyage_emission_summaries`` — matérialisation (cache recalculable, jamais
source de vérité) du grand livre d'émissions (``services.emission_ledger``) par
voyage : consommations par périmètre (ME/AE/total/escale/mouillage/hors
mouillage), émissions multi-GES (CO₂ TtW, CH₄/N₂O en grammes, WtT distinct),
distance, cargo B/L + MRV, facteur d'émission par méthode A/B/C, référence
(best-effort) vers ``emission_factors`` et origine (``events`` / ``legacy_noon``).

Revision ID: 20260709_0102
Revises: 20260709_0101
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260709_0102"
down_revision = "20260709_0101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voyage_emission_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "leg_id",
            sa.Integer(),
            sa.ForeignKey("legs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        # Consommations par périmètre (tonnes).
        sa.Column("conso_me_t", sa.Numeric(18, 6), nullable=True),
        sa.Column("conso_ae_t", sa.Numeric(18, 6), nullable=True),
        sa.Column("conso_total_t", sa.Numeric(18, 6), nullable=True),
        sa.Column("conso_escale_t", sa.Numeric(18, 6), nullable=True),
        sa.Column("conso_mouillage_t", sa.Numeric(18, 6), nullable=True),
        sa.Column("conso_hors_mouillage_t", sa.Numeric(18, 6), nullable=True),
        # Émissions multi-GES (assiette hors mouillage).
        sa.Column("co2_t", sa.Numeric(18, 6), nullable=True),
        sa.Column("ch4_g", sa.Numeric(20, 6), nullable=True),
        sa.Column("n2o_g", sa.Numeric(20, 6), nullable=True),
        sa.Column("wtt_co2eq_t", sa.Numeric(18, 6), nullable=True),
        # Distance / cargo.
        sa.Column("distance_nm", sa.Numeric(10, 2), nullable=True),
        sa.Column("cargo_bl_t", sa.Numeric(12, 3), nullable=True),
        sa.Column("cargo_mrv_t", sa.Numeric(12, 3), nullable=True),
        # Facteur d'émission par méthode A/B/C (gCO₂/t·km).
        sa.Column("ef_method_a", sa.Numeric(14, 4), nullable=True),
        sa.Column("ef_method_b", sa.Numeric(14, 4), nullable=True),
        sa.Column("ef_method_c", sa.Numeric(14, 4), nullable=True),
        sa.Column(
            "factors_ref",
            sa.Integer(),
            sa.ForeignKey("emission_factors.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_voyage_emission_summaries_leg", "voyage_emission_summaries", ["leg_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_voyage_emission_summaries_leg", table_name="voyage_emission_summaries"
    )
    op.drop_table("voyage_emission_summaries")
