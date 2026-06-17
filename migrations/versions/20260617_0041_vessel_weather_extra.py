"""Météo historisée : pression, visibilité, humidité, nébulosité.

Complète ``vessel_weather`` pour le bloc « conditions actuelles par navire »
de la page Performance › Navigation (rose des vents, anémomètre, pression
atmosphérique, visibilité, température…).

Revision ID: 20260617_0041
Revises: 20260617_0040
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260617_0041"
down_revision = "20260617_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vessel_weather", sa.Column("pressure_hpa", sa.Float(), nullable=True))
    op.add_column("vessel_weather", sa.Column("visibility_km", sa.Float(), nullable=True))
    op.add_column("vessel_weather", sa.Column("humidity_pct", sa.Float(), nullable=True))
    op.add_column("vessel_weather", sa.Column("cloud_cover_pct", sa.Float(), nullable=True))


def downgrade() -> None:
    for name in ("cloud_cover_pct", "humidity_pct", "visibility_km", "pressure_hpa"):
        op.drop_column("vessel_weather", name)
