"""ONB-03 — pièces jointes leg (documents bord / agent d'escale)

Revision ID: 20260622_0065
Revises: 20260622_0064
Create Date: 2026-06-22 22:30:00

Reprise V2 (``OnboardAttachment``) : table des documents catégorisés
rattachés à un leg (BL signés, lettres de protestation, constats, factures
agent, photos…). Les fichiers sont stockés hors base (``services.safe_files``) ;
seule la métadonnée + le chemin relatif sont persistés.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0065"
down_revision: Union[str, None] = "20260622_0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "leg_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "leg_id",
            sa.Integer(),
            sa.ForeignKey("legs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(40), nullable=False, server_default="other"),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("original_name", sa.String(255), nullable=True),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_mime", sa.String(80), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("uploaded_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("uploaded_by_name", sa.String(200), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_leg_attachments_leg_id", "leg_attachments", ["leg_id"])


def downgrade() -> None:
    op.drop_index("ix_leg_attachments_leg_id", table_name="leg_attachments")
    op.drop_table("leg_attachments")
