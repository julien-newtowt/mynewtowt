"""Page publique de voyage (B2B2C) — opt-in par réservation.

Adds:
- ``bookings.voyage_public`` : le client choisit de publier « le voyage de
  ce lot » sur ``/voyage/{reference}`` (destination du QR imprimé sur le
  paquet par le torréfacteur). Défaut ``false`` — jamais publié sans opt-in.

Revision ID: 20260701_0087
Revises: 20260629_0086
Create Date: 2026-07-01 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260701_0087"
down_revision = "20260629_0086"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "bookings",
        sa.Column("voyage_public", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("bookings", "voyage_public")
