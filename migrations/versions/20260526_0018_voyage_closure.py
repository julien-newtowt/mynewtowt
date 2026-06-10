"""voyage_closure — champs de clôture sur la table legs.

Revision ID: 20260526_0018
Revises: 20260519_0017
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260526_0018"
down_revision = "20260519_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("legs", sa.Column("closure_submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("legs", sa.Column("closure_reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("legs", sa.Column("closure_approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("legs", sa.Column("closure_submitted_by", sa.String(100), nullable=True))
    op.add_column("legs", sa.Column("closure_reviewed_by", sa.String(100), nullable=True))
    op.add_column("legs", sa.Column("closure_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("legs", "closure_notes")
    op.drop_column("legs", "closure_reviewed_by")
    op.drop_column("legs", "closure_submitted_by")
    op.drop_column("legs", "closure_approved_at")
    op.drop_column("legs", "closure_reviewed_at")
    op.drop_column("legs", "closure_submitted_at")
