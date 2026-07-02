"""Carnet éditorial (P8) — rubrique + photo de couverture.

Adds ``blog_posts.topic`` (rubrique éditoriale : arrivees/chantier/equipage/
clients, nullable) et ``blog_posts.cover_image`` (chemin/URL de la photo de
couverture, nullable). Les deux sont optionnels — aucun billet existant n'est
impacté.

Revision ID: 20260702_0090
Revises: 20260702_0089
Create Date: 2026-07-02 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260702_0090"
down_revision = "20260702_0089"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("blog_posts", sa.Column("topic", sa.String(length=20), nullable=True))
    op.add_column("blog_posts", sa.Column("cover_image", sa.String(length=300), nullable=True))


def downgrade():
    op.drop_column("blog_posts", "cover_image")
    op.drop_column("blog_posts", "topic")
