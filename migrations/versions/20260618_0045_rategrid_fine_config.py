"""grilles tarifaires — paramétrage fin : surcharge IMDG (%) + minimum de facturation

Revision ID: 20260618_0045
Revises: 20260618_0044
Create Date: 2026-06-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260618_0045"
down_revision = "20260618_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rate_grids", sa.Column("hazardous_surcharge_pct", sa.Numeric(5, 2), nullable=True)
    )
    op.add_column("rate_grids", sa.Column("min_charge_eur", sa.Numeric(10, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("rate_grids", "min_charge_eur")
    op.drop_column("rate_grids", "hazardous_surcharge_pct")
