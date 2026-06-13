"""LegFinance.claims_cost_eur — coût des sinistres affectés au leg (FLX-09).

Revision ID: 20260612_0028
Revises: 20260612_0027
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0028"
down_revision = "20260612_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leg_finances",
        sa.Column(
            "claims_cost_eur",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("leg_finances", "claims_cost_eur")
