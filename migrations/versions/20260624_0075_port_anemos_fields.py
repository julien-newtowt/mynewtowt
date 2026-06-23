"""Add ANEMOS Carnet de Bord fields to Port model.

Adds editorial descriptions for ports that are needed for the
Carnet de Bord ANEMOS report (MAN - human curation).

Revision ID: 20260624_0075
Revises: 20260624_0074
Create Date: 2026-06-24 00:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260624_0075"
down_revision = "20260624_0074"
branch_labels = None
depends_on = None


def upgrade():
    # Add description for Carnet de Bord
    op.add_column("ports", sa.Column("description", sa.Text(), nullable=True, comment="Description du port pour le Carnet de Bord ANEMOS"))
    
    # Add category for Carnet de Bord
    op.add_column("ports", sa.Column("anemos_category", sa.String(length=50), nullable=True, comment="Catégorie pour le Carnet de Bord (ex: 'departure', 'arrival', 'escale')"))


def downgrade():
    op.drop_column("ports", "anemos_category")
    op.drop_column("ports", "description")
