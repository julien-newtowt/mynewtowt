"""Journal des lots d'export Silae — lot L5 du SIRH.

Crée la table ``silae_export_batches`` : trace les exports EVP vers Silae
(contenu CSV conservé, statut du flux). Voir
``docs/strategy/CAHIER_DES_CHARGES_SIRH.md`` §4.7 / §10.

Revision ID: 20260618_0050
Revises: 20260618_0049
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260618_0050"
down_revision = "20260618_0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "silae_export_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="evp"),
        sa.Column("format", sa.String(length=10), nullable=False, server_default="csv"),
        sa.Column("file_path", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="generated"),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_silae_export_batches_period", "silae_export_batches", ["period"]
    )


def downgrade() -> None:
    op.drop_index("ix_silae_export_batches_period", table_name="silae_export_batches")
    op.drop_table("silae_export_batches")
