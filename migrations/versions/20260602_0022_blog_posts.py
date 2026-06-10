"""carnet de construction & actualités — table blog_posts (+ 2 entrées §5)

Revision ID: 20260602_0022
Revises: 20260602_0021
Create Date: 2026-06-02
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "20260602_0022"
down_revision = "20260602_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    blog = op.create_table(
        "blog_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(160), nullable=False),
        sa.Column("category", sa.String(20), nullable=False, server_default="carnet"),
        sa.Column("lang", sa.String(12), nullable=False, server_default="fr"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("lead", sa.String(500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author", sa.String(120), nullable=True),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("slug", name="uq_blog_posts_slug"),
    )
    op.create_index("ix_blog_posts_slug", "blog_posts", ["slug"])
    op.create_index("ix_blog_posts_published_at", "blog_posts", ["published_at"])

    # Deux premières entrées du carnet (dossier §5) — jalons positifs.
    op.bulk_insert(
        blog,
        [
            {
                "slug": "atlantis-entre-en-essais",
                "category": "carnet",
                "lang": "fr",
                "title": "Atlantis entre en essais",
                "lead": (
                    "Atlantis, premier des quatre nouveaux sisterships, achève sa "
                    "construction chez Piriou et entame ses essais en mer."
                ),
                "body": (
                    "<p>Atlantis, premier des quatre nouveaux sisterships, achève sa "
                    "construction chez Piriou et entame ses essais en mer — d'abord les "
                    "essais moteur, puis les essais sous voiles, dernière grande étape "
                    "avant de rejoindre Anemos et Artemis sur la ligne.</p>"
                    "<p>Voir ce navire passer de la cale d'assemblage à la mer, c'est "
                    "voir la flotte grandir pour de vrai.</p>"
                ),
                "author": "NewTowt",
                "is_published": True,
                "published_at": datetime(2026, 5, 15, tzinfo=timezone.utc),
            },
            {
                "slug": "quatre-sisterships-pour-etendre-la-ligne",
                "category": "carnet",
                "lang": "fr",
                "title": "Quatre sisterships pour étendre la ligne",
                "lead": (
                    "Quatre voiliers-cargos identiques — Atlantis, Astérias, "
                    "Archimedes et Atlas — construits par Piriou au Vietnam."
                ),
                "body": (
                    "<p>Notre extension de flotte compte quatre voiliers-cargos "
                    "identiques — Atlantis, Astérias, Archimedes et Atlas — construits "
                    "par Piriou au Vietnam, sur les chantiers de Song Thu et de Ba Son.</p>"
                    "<p>Ils avancent à des rythmes différents : Atlantis touche au but "
                    "quand Archimedes en est encore à la coque, ce qui permet de suivre, "
                    "sur une même série, toutes les étapes de la naissance d'un "
                    "voilier-cargo.</p>"
                    "<p>En les bâtissant, NewTowt fait aussi grandir une filière du "
                    "transport à la voile.</p>"
                ),
                "author": "NewTowt",
                "is_published": True,
                "published_at": datetime(2026, 4, 20, tzinfo=timezone.utc),
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_blog_posts_published_at", "blog_posts")
    op.drop_index("ix_blog_posts_slug", "blog_posts")
    op.drop_table("blog_posts")
