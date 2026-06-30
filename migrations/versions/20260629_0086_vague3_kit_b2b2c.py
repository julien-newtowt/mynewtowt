"""Vague 3 — kit B2B2C : co-branding client + terroir café par booking.

Adds:
- ``client_accounts.brand_name`` / ``brand_logo_path`` : marque + logo du
  client pour co-brander les assets du kit (espace ``/me/brand``).
- ``bookings.coffee_origin`` / ``coffee_region`` / ``coffee_producer`` :
  terroir café injecté dans le récit d'origine du certificat Anemos
  (pack par expédition).

Revision ID: 20260629_0086
Revises: 20260629_0085
Create Date: 2026-06-30 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260629_0086"
down_revision = "20260629_0085"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("client_accounts", sa.Column("brand_name", sa.String(length=120), nullable=True))
    op.add_column(
        "client_accounts", sa.Column("brand_logo_path", sa.String(length=255), nullable=True)
    )
    op.add_column("bookings", sa.Column("coffee_origin", sa.String(length=20), nullable=True))
    op.add_column("bookings", sa.Column("coffee_region", sa.String(length=120), nullable=True))
    op.add_column("bookings", sa.Column("coffee_producer", sa.String(length=160), nullable=True))


def downgrade():
    op.drop_column("bookings", "coffee_producer")
    op.drop_column("bookings", "coffee_region")
    op.drop_column("bookings", "coffee_origin")
    op.drop_column("client_accounts", "brand_logo_path")
    op.drop_column("client_accounts", "brand_name")
