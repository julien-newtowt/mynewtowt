"""Lien réservation → devis d'origine (conversion devis→booking, COM-13).

Revision ID: 20260612_0037
Revises: 20260612_0036
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0037"
down_revision = "20260612_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bookings", sa.Column("quote_id", sa.Integer(), nullable=True))
    op.create_index("ix_bookings_quote_id", "bookings", ["quote_id"])
    op.create_foreign_key(
        "fk_bookings_quote",
        "bookings",
        "quotes",
        ["quote_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_bookings_quote", "bookings", type_="foreignkey")
    op.drop_index("ix_bookings_quote_id", table_name="bookings")
    op.drop_column("bookings", "quote_id")
