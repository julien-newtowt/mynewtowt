"""Add signature/lock columns to cargo_documents (EVO-09).

Applique le mécanisme IMO (signature SHA-256 + verrouillage) aux documents
cargo guidés (NOR/LOP/Mate's Receipt…), comme SOF/noon/watch. Colonnes
additives : ``is_locked`` NOT NULL server_default ``false`` (documents
existants = non signés) ; les autres nullable.

Revision ID: 20260629_0081
Revises: 20260624_0080
Create Date: 2026-06-29 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260629_0081"
down_revision = "20260624_0080"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "cargo_documents", sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("cargo_documents", sa.Column("signed_by_id", sa.Integer(), nullable=True))
    op.add_column(
        "cargo_documents", sa.Column("signed_by_name", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "cargo_documents", sa.Column("signature_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "cargo_documents",
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_foreign_key(
        "fk_cargo_documents_signed_by_id_users",
        "cargo_documents",
        "users",
        ["signed_by_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint(
        "fk_cargo_documents_signed_by_id_users", "cargo_documents", type_="foreignkey"
    )
    op.drop_column("cargo_documents", "is_locked")
    op.drop_column("cargo_documents", "signature_hash")
    op.drop_column("cargo_documents", "signed_by_name")
    op.drop_column("cargo_documents", "signed_by_id")
    op.drop_column("cargo_documents", "signed_at")
