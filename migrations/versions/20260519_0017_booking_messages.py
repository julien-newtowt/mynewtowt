"""booking_messages — messagerie client ↔ équipe par réservation.

Revision ID: 20260519_0017
Revises: 20260519_0016
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260519_0017"
down_revision = "20260519_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "booking_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "booking_id", sa.Integer(),
            sa.ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("sender", sa.String(20), nullable=False),
        sa.Column("sender_name", sa.String(200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_booking_messages_booking_id", "booking_messages", ["booking_id"])


def downgrade() -> None:
    op.drop_index("ix_booking_messages_booking_id", "booking_messages")
    op.drop_table("booking_messages")
