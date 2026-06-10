"""vitrine — table contact_requests (demandes de cotation/contact)

Revision ID: 20260602_0021
Revises: 20260601_0020
Create Date: 2026-06-02
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260602_0021"
down_revision = "20260601_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contact_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("company", sa.String(200), nullable=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("pol", sa.String(120), nullable=True),
        sa.Column("pod", sa.String(120), nullable=True),
        sa.Column("cargo_nature", sa.String(200), nullable=True),
        sa.Column("volume_weight", sa.String(120), nullable=True),
        sa.Column("desired_dates", sa.String(120), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("lang", sa.String(12), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_contact_requests_email", "contact_requests", ["email"])


def downgrade() -> None:
    op.drop_index("ix_contact_requests_email", "contact_requests")
    op.drop_table("contact_requests")
