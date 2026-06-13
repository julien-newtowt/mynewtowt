"""B2 — canal de vente unifié sur les réservations (rail A + B).

bookings.channel : la réservation est remplie soit par l'opérateur depuis le
back-office ('operator'), soit par le client depuis le wizard public
('client'). Les lignes existantes sont rétro-classées 'client' (default).

Revision ID: 20260612_0032
Revises: 20260612_0031
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0032"
down_revision = "20260612_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column(
            "channel",
            sa.String(length=20),
            nullable=False,
            server_default="client",
        ),
    )
    op.create_index("ix_bookings_channel", "bookings", ["channel"])


def downgrade() -> None:
    op.drop_index("ix_bookings_channel", table_name="bookings")
    op.drop_column("bookings", "channel")
