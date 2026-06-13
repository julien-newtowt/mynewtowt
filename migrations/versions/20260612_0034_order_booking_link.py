"""B2.2 — back-link commande ↔ réservation (reprise rail A → rail B).

Ajoute ``commercial_orders.booking_id`` (FK bookings, nullable, indexé) : le
marqueur de reprise d'une commande héritée en réservation ``channel="operator"``.
Sert d'idempotence au script ``scripts.migrate_orders_to_bookings`` et de
dé-doublonnage capacité (une commande reprise est comptée via son booking,
plus directement).

Migration additive et réversible — aucun backfill (la nullabilité couvre les
lignes existantes, non encore reprises).

Revision ID: 20260612_0034
Revises: 20260612_0033
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0034"
down_revision = "20260612_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "commercial_orders",
        sa.Column("booking_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_commercial_orders_booking",
        "commercial_orders",
        "bookings",
        ["booking_id"],
        ["id"],
    )
    op.create_index(
        "ix_commercial_orders_booking_id", "commercial_orders", ["booking_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_commercial_orders_booking_id", "commercial_orders")
    op.drop_constraint(
        "fk_commercial_orders_booking", "commercial_orders", type_="foreignkey"
    )
    op.drop_column("commercial_orders", "booking_id")
