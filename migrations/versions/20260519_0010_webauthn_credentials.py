"""WebAuthn / Passkey credentials — polymorphe client | staff.

Stocke credential_id (base64url texte, unique), public_key (binaire
COSE-encoded), sign_count anti-clone, transports + name + aaguid pour
métadonnées.

Revision ID: 20260519_0010
Revises: 20260519_0009
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260519_0010"
down_revision = "20260519_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webauthn_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_type", sa.String(20), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.String(255), nullable=False, unique=True),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(120), nullable=True),
        sa.Column("transports", sa.String(80), nullable=True),
        sa.Column("aaguid", sa.String(40), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_webauthn_owner", "webauthn_credentials",
        ["owner_type", "owner_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_webauthn_owner", "webauthn_credentials")
    op.drop_table("webauthn_credentials")
