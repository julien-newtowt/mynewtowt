"""User.assigned_vessel_id — RBAC row-level pour rôle marins / manager_maritime.

Permet à ``captain_router`` de filtrer les legs visibles d'un commandant
à ceux de son navire de rattachement (sans bloquer les admins / managers
qui n'ont pas d'assigned_vessel_id défini).

Revision ID: 20260519_0007
Revises: 20260519_0006
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260519_0007"
down_revision = "20260519_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "assigned_vessel_id",
                sa.Integer(),
                sa.ForeignKey("vessels.id"),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("assigned_vessel_id")
