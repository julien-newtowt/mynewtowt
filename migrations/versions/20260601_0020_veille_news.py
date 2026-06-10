"""veille d'actualité — news_sources + news_items

Revision ID: 20260601_0020
Revises: 20260526_0019
Create Date: 2026-06-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260601_0020"
down_revision = "20260526_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("query", sa.String(500), nullable=False),
        sa.Column("countries", sa.String(120), nullable=True),
        sa.Column("languages", sa.String(120), nullable=True),
        sa.Column("category", sa.String(60), nullable=True),
        sa.Column("target_roles", sa.String(200), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by_id", sa.Integer(),
            sa.ForeignKey("users.id"), nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )

    op.create_table(
        "news_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id", sa.Integer(),
            sa.ForeignKey("news_sources.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("external_id", sa.String(80), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("link", sa.String(1000), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("publisher", sa.String(200), nullable=True),
        sa.Column("image_url", sa.String(1000), nullable=True),
        sa.Column("language", sa.String(12), nullable=True),
        sa.Column("country", sa.String(60), nullable=True),
        sa.Column("category", sa.String(60), nullable=True),
        sa.Column("pub_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("external_id", name="uq_news_items_external_id"),
    )
    op.create_index("ix_news_items_source_id", "news_items", ["source_id"])
    op.create_index("ix_news_items_external_id", "news_items", ["external_id"])
    op.create_index("ix_news_items_pub_date", "news_items", ["pub_date"])


def downgrade() -> None:
    op.drop_index("ix_news_items_pub_date", "news_items")
    op.drop_index("ix_news_items_external_id", "news_items")
    op.drop_index("ix_news_items_source_id", "news_items")
    op.drop_table("news_items")
    op.drop_table("news_sources")
