"""Suppression des soldes CP/RTT côté MyNewtowt (décision de cadrage).

Les soldes de congés sont gérés dans Silae (source de vérité) et il n'y a
pas de RTT dans la convention transport/maritime. On retire donc les
colonnes ``cp_balance`` / ``rtt_balance`` de ``employees``.

Revision ID: 20260618_0052
Revises: 20260618_0051
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260618_0052"
down_revision = "20260618_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("employees", "cp_balance")
    op.drop_column("employees", "rtt_balance")


def downgrade() -> None:
    op.add_column(
        "employees",
        sa.Column("rtt_balance", sa.Numeric(6, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "employees",
        sa.Column("cp_balance", sa.Numeric(6, 2), nullable=False, server_default="0"),
    )
