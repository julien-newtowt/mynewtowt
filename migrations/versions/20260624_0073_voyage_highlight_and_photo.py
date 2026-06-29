"""Voyage Highlight and Photo models for Carnet de Bord ANEMOS.

Creates:
- voyage_highlights table: remarkable points during a voyage
- voyage_photos table: photos organized in batches for a voyage

Note : ``voyage_highlights`` et ``voyage_photos`` se référencent mutuellement
(``photo_id`` ↔ ``highlight_id``). On crée d'abord les deux tables, puis on
ajoute la FK ``voyage_highlights.photo_id`` une fois ``voyage_photos`` existante
(FK circulaire). Les colonnes de chaque ``ForeignKeyConstraint`` doivent être
passées en **liste** (corrige un bug bloquant : une chaîne était comptée
caractère par caractère).

Revision ID: 20260624_0073
Revises: 20260623_0072
Create Date: 2026-06-24 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260624_0073"
down_revision = "20260623_0072"
branch_labels = None
depends_on = None


def upgrade():
    # Create voyage_highlights table (la FK photo_id est ajoutée après
    # voyage_photos — référence circulaire).
    op.create_table(
        "voyage_highlights",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("leg_id", sa.Integer(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=False, server_default="navigation"),
        sa.Column("photo_id", sa.Integer(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["leg_id"], ["legs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_voyage_highlights_leg_id"), "voyage_highlights", ["leg_id"], unique=False)
    op.create_index(op.f("ix_voyage_highlights_occurred_at"), "voyage_highlights", ["occurred_at"], unique=False)

    # Create voyage_photos table
    op.create_table(
        "voyage_photos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("leg_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.String(length=50), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False, server_default="other"),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("file_mime", sa.String(length=80), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("original_name", sa.String(length=255), nullable=True),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("highlight_id", sa.Integer(), nullable=True),
        sa.Column("crew_member_id", sa.Integer(), nullable=True),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=True),
        sa.Column("uploaded_by_name", sa.String(length=200), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["crew_member_id"], ["crew_members.id"]),
        sa.ForeignKeyConstraint(["highlight_id"], ["voyage_highlights.id"]),
        sa.ForeignKeyConstraint(["leg_id"], ["legs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_voyage_photos_leg_id"), "voyage_photos", ["leg_id"], unique=False)
    op.create_index(op.f("ix_voyage_photos_batch_id"), "voyage_photos", ["batch_id"], unique=False)

    # FK circulaire : voyage_highlights.photo_id -> voyage_photos.id, posée une
    # fois les deux tables créées.
    op.create_foreign_key(
        "fk_voyage_highlights_photo_id_voyage_photos",
        "voyage_highlights",
        "voyage_photos",
        ["photo_id"],
        ["id"],
    )


def downgrade():
    # Retirer d'abord la FK circulaire, sinon le DROP de voyage_photos échoue
    # (référencée par voyage_highlights.photo_id).
    op.drop_constraint(
        "fk_voyage_highlights_photo_id_voyage_photos", "voyage_highlights", type_="foreignkey"
    )
    op.drop_table("voyage_photos")
    op.drop_table("voyage_highlights")
