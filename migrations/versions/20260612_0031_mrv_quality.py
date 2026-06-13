"""B5 — contrôle qualité ROB déclaré vs calculé (±2 t) sur les events MRV.

mrv_events.quality_status / quality_notes : qualité du point ROB lors de la
génération depuis un noon report signé ('ok' | 'warning', écart toléré ±2 t).

Revision ID: 20260612_0031
Revises: 20260612_0030
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0031"
down_revision = "20260612_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mrv_events",
        sa.Column("quality_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "mrv_events",
        sa.Column("quality_notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mrv_events", "quality_notes")
    op.drop_column("mrv_events", "quality_status")
