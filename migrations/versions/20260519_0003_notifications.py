"""notifications table

Revision ID: 20260519_0003
Revises: 20260519_0002
Create Date: 2026-05-19 12:30:00

Crée la table des notifications dashboard (cargo / claim / EOSP / SOSP /
new_order / new_cargo_message / eta_shift / packing_to_review / leg_locked
/ info).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260519_0003"
down_revision: Union[str, None] = "20260519_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("target_user_id", sa.Integer, sa.ForeignKey("users.id"), index=True),
        sa.Column("target_role", sa.String(40)),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.Text),
        sa.Column("link", sa.String(500)),
        sa.Column("is_read", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("is_archived", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False, index=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("notifications")
