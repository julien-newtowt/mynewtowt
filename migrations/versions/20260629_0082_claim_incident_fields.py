"""Add claims.incident_location / incident_context (ONB-08).

Champs additifs nullable pour qualifier un sinistre : lieu de l'incident (port,
position en mer, zone à bord) et circonstances/contexte. Changement additif sûr.

Revision ID: 20260629_0082
Revises: 20260629_0081
Create Date: 2026-06-29 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260629_0082"
down_revision = "20260629_0081"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("claims", sa.Column("incident_location", sa.String(length=200), nullable=True))
    op.add_column("claims", sa.Column("incident_context", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("claims", "incident_context")
    op.drop_column("claims", "incident_location")
