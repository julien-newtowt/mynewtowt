"""notifications.target_client_id — ciblage des notifications côté client.

Permet d'adresser une notification in-app à un compte client (espace
``/me``) en plus du ciblage staff existant (``target_user_id`` /
``target_role``).

Revision ID: 20260519_0015
Revises: 20260519_0014
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260519_0015"
down_revision = "20260519_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("target_client_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_notifications_target_client",
        "notifications", "client_accounts",
        ["target_client_id"], ["id"],
    )
    op.create_index(
        "ix_notifications_target_client_id", "notifications", ["target_client_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_target_client_id", "notifications")
    op.drop_constraint("fk_notifications_target_client", "notifications", type_="foreignkey")
    op.drop_column("notifications", "target_client_id")
