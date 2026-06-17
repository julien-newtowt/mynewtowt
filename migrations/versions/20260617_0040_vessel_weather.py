"""Historique météo au point GPS (snapshots Windy, cron 30 min).

Crée ``vessel_weather`` : la météo (vent, courant, vague, température) relevée
au dernier point GPS connu de chaque navire, capturée toutes les 30 min et
historisée pour consultation ultérieure (page Performance › Navigation, y
compris pour les legs déjà réalisés).

Revision ID: 20260617_0040
Revises: 20260615_0039
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260617_0040"
down_revision = "20260615_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vessel_weather",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "vessel_id",
            sa.Integer(),
            sa.ForeignKey("vessels.id"),
            nullable=False,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("wind_speed_kn", sa.Float(), nullable=True),
        sa.Column("wind_direction_deg", sa.Float(), nullable=True),
        sa.Column("current_speed_kn", sa.Float(), nullable=True),
        sa.Column("current_direction_deg", sa.Float(), nullable=True),
        sa.Column("wave_height_m", sa.Float(), nullable=True),
        sa.Column("wave_direction_deg", sa.Float(), nullable=True),
        sa.Column("wave_period_s", sa.Float(), nullable=True),
        sa.Column("temperature_c", sa.Float(), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False, server_default="windy"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("vessel_id", "recorded_at", name="uq_vessel_weather_vessel_time"),
    )
    op.create_index("ix_vessel_weather_vessel_id", "vessel_weather", ["vessel_id"])
    op.create_index("ix_vessel_weather_recorded_at", "vessel_weather", ["recorded_at"])


def downgrade() -> None:
    op.drop_index("ix_vessel_weather_recorded_at", table_name="vessel_weather")
    op.drop_index("ix_vessel_weather_vessel_id", table_name="vessel_weather")
    op.drop_table("vessel_weather")
