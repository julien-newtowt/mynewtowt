"""add source column to ports + index on lat/lon

Revision ID: 20260518_0003
Revises: 20260518_0002
Create Date: 2026-05-18 22:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260518_0003"
down_revision: Union[str, None] = "20260518_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ports",
        sa.Column("source", sa.String(40), server_default="manual", nullable=False),
    )
    op.add_column(
        "ports",
        sa.Column("function_code", sa.String(8)),  # UN/LOCODE function field
    )
    op.add_column(
        "ports",
        sa.Column("subdivision", sa.String(8)),  # ISO 3166-2 subdivision
    )
    op.create_index("ix_ports_source", "ports", ["source"])
    op.create_index("ix_ports_country", "ports", ["country"])
    # Spatial-ish: index on (lat, lon) for nearby queries (without PostGIS).
    op.create_index("ix_ports_lat_lon", "ports", ["latitude", "longitude"])


def downgrade() -> None:
    op.drop_index("ix_ports_lat_lon", table_name="ports")
    op.drop_index("ix_ports_country", table_name="ports")
    op.drop_index("ix_ports_source", table_name="ports")
    op.drop_column("ports", "subdivision")
    op.drop_column("ports", "function_code")
    op.drop_column("ports", "source")
