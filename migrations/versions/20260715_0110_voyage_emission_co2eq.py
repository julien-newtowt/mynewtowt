"""Add voyage_emission_summaries.co2eq_t — CO2eq GWP-100 tank-to-wake (G13).

Colonne additive (nullable) : CO2eq TtW calculé via les GWP-100 de l'Annexe I
du règlement EU 2015/757 (CH4 = 25, N2O = 298) — DISTINCT du wtt_co2eq_t déjà
présent (Well-to-Tank, FuelEU), jamais sommés entre eux. Un ``refresh_summary``
repeuple cette colonne pour les voyages déjà matérialisés (cache recalculable,
jamais source de vérité).

Revision ID: 20260715_0110
Revises: 20260715_0109
Create Date: 2026-07-15

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260715_0110"
down_revision = "20260715_0109"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "voyage_emission_summaries", sa.Column("co2eq_t", sa.Numeric(18, 6), nullable=True)
    )


def downgrade():
    op.drop_column("voyage_emission_summaries", "co2eq_t")
