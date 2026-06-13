"""B3 — cale (hold) sur les shifts dockers (escale ↔ stowage).

Relie le planning des vacations dockers au plan d'arrimage 18 zones :
``docker_shifts.hold`` désigne la cale travaillée par la vacation,
alignée sur ``app.models.stowage.HOLDS`` ("AR"/"AV"). Nullable
(NULL = cale non spécifiée) — aucun backfill nécessaire.

Revision ID: 20260612_0033
Revises: 20260612_0032
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0033"
down_revision = "20260612_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "docker_shifts",
        sa.Column("hold", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("docker_shifts", "hold")
