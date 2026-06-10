"""drop webauthn_credentials table

Revision ID: 20260526_0019
Revises: 20260526_0018
Create Date: 2026-05-26
"""
from alembic import op

revision = "20260526_0019"
down_revision = "20260526_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("webauthn_credentials")


def downgrade() -> None:
    op.create_table(
        "webauthn_credentials",
        # minimal schema to allow rollback — data non-recoverable
        __import__("sqlalchemy").Column("id", __import__("sqlalchemy").Integer, primary_key=True),
        __import__("sqlalchemy").Column("owner_type", __import__("sqlalchemy").String(10), nullable=False),
        __import__("sqlalchemy").Column("owner_id", __import__("sqlalchemy").Integer, nullable=False),
        __import__("sqlalchemy").Column("credential_id", __import__("sqlalchemy").LargeBinary, nullable=False),
        __import__("sqlalchemy").Column("public_key", __import__("sqlalchemy").LargeBinary, nullable=False),
        __import__("sqlalchemy").Column("sign_count", __import__("sqlalchemy").Integer, nullable=False, server_default="0"),
        __import__("sqlalchemy").Column("aaguid", __import__("sqlalchemy").String(36)),
        __import__("sqlalchemy").Column("label", __import__("sqlalchemy").String(100)),
        __import__("sqlalchemy").Column("created_at", __import__("sqlalchemy").DateTime(timezone=True), nullable=False, server_default=__import__("sqlalchemy").text("now()")),
    )
