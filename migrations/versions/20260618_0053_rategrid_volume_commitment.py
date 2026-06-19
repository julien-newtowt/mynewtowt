"""grilles tarifaires — engagement minimum de volume (volume_commitment)

Revision ID: 20260618_0053
Revises: 20260618_0052
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260618_0053"
down_revision = "20260618_0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rate_grids", sa.Column("volume_commitment", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("rate_grids", "volume_commitment")
