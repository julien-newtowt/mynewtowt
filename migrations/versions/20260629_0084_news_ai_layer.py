"""Couche IA de la veille (EVO-04) : score IA par item + digest quotidien.

Additif et rétrocompatible :
- ``news_items.ai_score`` (int, nullable) — score de pertinence affiné par IA,
  rempli au cron quand ``ANTHROPIC_API_KEY`` est présente ; ``NULL`` sinon →
  l'UI retombe sur le scoring heuristique (lot 70).
- table ``news_digests`` — synthèse markdown du jour (1 ligne / jour / langue),
  upsert idempotent au cron.

Revision ID: 20260629_0084
Revises: 20260629_0083
Create Date: 2026-06-29 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260629_0084"
down_revision = "20260629_0083"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("news_items", sa.Column("ai_score", sa.Integer(), nullable=True))
    op.create_table(
        "news_digests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("lang", sa.String(length=12), nullable=False, server_default="fr"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("day", "lang", name="uq_news_digests_day_lang"),
    )


def downgrade():
    op.drop_table("news_digests")
    op.drop_column("news_items", "ai_score")
