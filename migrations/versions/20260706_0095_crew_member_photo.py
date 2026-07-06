"""Add crew_members photo columns (photo d'identité — enrichissement ERP).

La liste des collaborateurs vient de Marad (lecture seule), mais la photo
d'identité est un enrichissement ERP local (comme le passeport ou les notes) :
trois colonnes optionnelles, jamais touchées par la sync Marad. Changement
additif (lignes existantes = pas de photo).

Revision ID: 20260706_0095
Revises: 20260703_0094
Create Date: 2026-07-06 00:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260706_0095"
down_revision = "20260703_0094"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("crew_members", sa.Column("photo_path", sa.String(length=500), nullable=True))
    op.add_column("crew_members", sa.Column("photo_filename", sa.String(length=255), nullable=True))
    op.add_column("crew_members", sa.Column("photo_mime", sa.String(length=80), nullable=True))


def downgrade():
    op.drop_column("crew_members", "photo_mime")
    op.drop_column("crew_members", "photo_filename")
    op.drop_column("crew_members", "photo_path")
