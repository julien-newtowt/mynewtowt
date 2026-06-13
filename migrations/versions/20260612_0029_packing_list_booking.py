"""Packing list ← booking (rail B) : booking_id, loading_date, order XOR booking.

Première brique de la fusion des rails A (commandes) / B (bookings) : une
packing list peut désormais provenir d'un booking client en plus d'une
commande opérateur.

- ``packing_lists.order_id`` devient nullable.
- Ajout de ``packing_lists.booking_id`` (FK bookings, nullable, indexé).
- Ajout de ``packing_lists.loading_date`` (= ETD du leg, alimente la
  cascade de dates ultérieure).
- Contrainte CHECK XOR : exactement une des deux origines est renseignée.

Migration additive et réversible (aucun backfill — la nullabilité couvre
les lignes existantes, toutes rattachées à un ``order_id``).

Revision ID: 20260612_0029
Revises: 20260612_0028
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0029"
down_revision = "20260612_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "packing_lists",
        sa.Column("booking_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "packing_lists",
        sa.Column("loading_date", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column(
        "packing_lists",
        "order_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_foreign_key(
        "fk_packing_lists_booking",
        "packing_lists",
        "bookings",
        ["booking_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_packing_lists_booking_id", "packing_lists", ["booking_id"]
    )
    op.create_check_constraint(
        "ck_packing_lists_order_xor_booking",
        "packing_lists",
        "(order_id IS NULL) <> (booking_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_packing_lists_order_xor_booking", "packing_lists", type_="check"
    )
    op.drop_index("ix_packing_lists_booking_id", "packing_lists")
    op.drop_constraint("fk_packing_lists_booking", "packing_lists", type_="foreignkey")
    op.alter_column(
        "packing_lists",
        "order_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_column("packing_lists", "loading_date")
    op.drop_column("packing_lists", "booking_id")
