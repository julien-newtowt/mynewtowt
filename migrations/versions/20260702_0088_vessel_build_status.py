"""Statut de flotte (P4) — navire en service / en construction.

Adds ``vessels.build_status`` (« operational » | « under_construction »),
défaut « operational ». Permet à la vitrine de dériver « 2 en opération,
4 en construction » d'une donnée plutôt que d'un texte en dur.

Revision ID: 20260702_0088
Revises: 20260701_0087
Create Date: 2026-07-02 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260702_0088"
down_revision = "20260701_0087"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "vessels",
        sa.Column(
            "build_status",
            sa.String(length=20),
            nullable=False,
            server_default="operational",
        ),
    )


def downgrade():
    op.drop_column("vessels", "build_status")
