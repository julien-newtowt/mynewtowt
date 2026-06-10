"""packing_list_documents — rattachement direct à un booking.

Un document peut désormais être rattaché soit à une packing list (portail
expéditeur), soit directement à un booking (upload client depuis /me).
``packing_list_id`` devient nullable et on ajoute ``booking_id``.

Revision ID: 20260519_0016
Revises: 20260519_0015
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260519_0016"
down_revision = "20260519_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "packing_list_documents",
        sa.Column("booking_id", sa.Integer(), nullable=True),
    )
    op.alter_column(
        "packing_list_documents", "packing_list_id",
        existing_type=sa.Integer(), nullable=True,
    )
    op.create_foreign_key(
        "fk_pld_booking", "packing_list_documents", "bookings",
        ["booking_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index(
        "ix_pld_booking_id", "packing_list_documents", ["booking_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_pld_booking_id", "packing_list_documents")
    op.drop_constraint("fk_pld_booking", "packing_list_documents", type_="foreignkey")
    op.alter_column(
        "packing_list_documents", "packing_list_id",
        existing_type=sa.Integer(), nullable=False,
    )
    op.drop_column("packing_list_documents", "booking_id")
