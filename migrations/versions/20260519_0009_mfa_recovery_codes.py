"""MFA recovery codes — 10 codes single-use hashés.

Permet de récupérer l'accès à un compte MFA quand le device d'auth est
perdu / volé / réinstallé. Codes générés à l'activation, affichés une
seule fois en clair, stockés hashés (SHA-256) en DB. Polymorphe via
``owner_type`` : "client" ou "staff".

Revision ID: 20260519_0009
Revises: 20260519_0008
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260519_0009"
down_revision = "20260519_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mfa_recovery_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_type", sa.String(20), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_mfa_recovery_owner", "mfa_recovery_codes",
        ["owner_type", "owner_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_mfa_recovery_owner", "mfa_recovery_codes")
    op.drop_table("mfa_recovery_codes")
