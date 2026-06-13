"""Ticket.escalated_at — dédup escalade SLA manager (FLX-08 / Bloc B4).

Horodatage de l'escalade vers le manager quand un ticket dépasse son SLA.
NULL tant que non escaladé ; garantit une seule notification par ticket.

Revision ID: 20260612_0030
Revises: 20260612_0029
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0030"
down_revision = "20260612_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tickets", "escalated_at")
