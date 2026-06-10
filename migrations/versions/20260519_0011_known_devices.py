"""Known device fingerprints — détection login depuis nouvel appareil.

Hash SHA-256 de (UA normalisé + IP préfixée /24 ou /48). Polymorphe
client / staff. Pas d'IP/UA en clair (light RGPD).

Revision ID: 20260519_0011
Revises: 20260519_0010
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260519_0011"
down_revision = "20260519_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "known_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_type", sa.String(20), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("fingerprint_hash", sa.String(64), nullable=False),
        sa.Column("label", sa.String(120), nullable=True),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_known_device_lookup", "known_devices",
        ["owner_type", "owner_id", "fingerprint_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_known_device_lookup", "known_devices")
    op.drop_table("known_devices")
