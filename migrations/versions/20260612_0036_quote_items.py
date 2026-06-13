"""Devis : stockage des lignes palettes (conversion devis → réservation).

Revision ID: 20260612_0036
Revises: 20260612_0035
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0036"
down_revision = "20260612_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quotes", sa.Column("items_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("quotes", "items_json")
