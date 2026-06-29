"""Add ports.is_shortcut for planning quick-pick shortcuts (PLN-07).

Colonne booléenne additive (server_default ``false`` → ports existants = non
raccourcis). Le formulaire de leg propose les ports marqués en un clic ; sans
aucun port marqué, il retombe sur la liste de raccourcis historique (pas de
régression).

Revision ID: 20260629_0083
Revises: 20260629_0082
Create Date: 2026-06-29 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260629_0083"
down_revision = "20260629_0082"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ports",
        sa.Column("is_shortcut", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("ports", "is_shortcut")
