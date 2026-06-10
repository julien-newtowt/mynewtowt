"""Booking lifecycle timestamps — loaded / at_sea / discharged / delivered.

Permet d'horodater chaque transition de voyage (en plus de
``submitted_at`` / ``confirmed_at`` déjà présents) afin d'alimenter la
timeline de suivi client (`/me/track/{ref}`) et les déclencheurs de
notifications + émission Anemos.

Revision ID: 20260519_0013
Revises: 20260519_0012
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260519_0013"
down_revision = "20260519_0012"
branch_labels = None
depends_on = None


_COLUMNS = ("loaded_at", "at_sea_at", "discharged_at", "delivered_at")


def upgrade() -> None:
    for col in _COLUMNS:
        op.add_column(
            "bookings",
            sa.Column(col, sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    for col in reversed(_COLUMNS):
        op.drop_column("bookings", col)
