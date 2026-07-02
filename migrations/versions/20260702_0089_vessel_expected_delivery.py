"""Horizon de livraison flotte (P5) — navires en construction.

Adds ``vessels.expected_delivery`` (libellé nullable, ex. « Juillet 2026 »,
« 2027 »). Renseigné pour les navires en construction ; NULL pour ceux en
service. Alimente la page publique /flotte sans date en dur dans le template.

Revision ID: 20260702_0089
Revises: 20260702_0088
Create Date: 2026-07-02 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260702_0089"
down_revision = "20260702_0088"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "vessels",
        sa.Column("expected_delivery", sa.String(length=40), nullable=True),
    )


def downgrade():
    op.drop_column("vessels", "expected_delivery")
