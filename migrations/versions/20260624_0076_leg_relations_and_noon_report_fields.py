"""Add relations to Leg model and fields to NoonReport for Carnet de Bord ANEMOS.

Adds:
- Relations to VoyageHighlight and VoyagePhoto in Leg model
- propulsion_mode and sog_max fields to NoonReport

Revision ID: 20260624_0076
Revises: 20260624_0075
Create Date: 2026-06-24 00:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260624_0076"
down_revision = "20260624_0075"
branch_labels = None
depends_on = None


def upgrade():
    # Add propulsion_mode and sog_max to noon_reports
    op.add_column("noon_reports", sa.Column("sog_max", sa.Float(), nullable=True, comment="SOG maximum 24h"))
    op.add_column("noon_reports", sa.Column("propulsion_mode", sa.String(length=20), nullable=True, comment="Mode de propulsion: sail/assisted/motor"))


def downgrade():
    op.drop_column("noon_reports", "propulsion_mode")
    op.drop_column("noon_reports", "sog_max")
