"""Add vessels.deadweight_t — symétrique de lightweight_t (G17).

Colonne additive (nullable), purement informative comme lightweight_t
(architecture §2.1) : ni lue ni calculée par compute_cargo_mrv (G10), le
Cargo MRV restant saisi directement par le Master. Distincte du `dwt`
commercial déjà existant (référentiel stowage/booking, hors périmètre MRV).

Revision ID: 20260716_0111
Revises: 20260715_0110
Create Date: 2026-07-16

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260716_0111"
down_revision = "20260715_0110"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "vessels",
        sa.Column(
            "deadweight_t",
            sa.Numeric(10, 3),
            nullable=True,
            comment="Port en lourd (deadweight) MRV en tonnes",
        ),
    )


def downgrade():
    op.drop_column("vessels", "deadweight_t")
