"""Add claims.crew_member_id for crew-claim rattachement (ONB-06).

Un sinistre équipage (``claim_type = 'crew'``) peut désormais être rattaché au
marin concerné (``crew_members.id``), en plus du leg / booking. Colonne
nullable → changement additif sûr.

Revision ID: 20260624_0078
Revises: 20260624_0077
Create Date: 2026-06-24 00:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260624_0078"
down_revision = "20260624_0077"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "claims",
        sa.Column(
            "crew_member_id",
            sa.Integer(),
            sa.ForeignKey("crew_members.id"),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column("claims", "crew_member_id")
