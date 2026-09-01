"""Add crew_members first_name/last_name/agency (trombinoscope Armement).

Le trombinoscope mensuel (cf. docs/strategy/CAHIER_DES_CHARGES_TROMBINOSCOPE.md)
affiche Nom et Prénom comme deux champs distincts, alors que ``full_name`` est
aujourd'hui un champ unique. Ajout additif de ``first_name``/``last_name``
(``full_name`` est conservé pour compatibilité ascendante) avec reprise de
données par heuristique (premier mot = prénom, reste = nom) sur les lignes
existantes — approche validée avec le service Armement, à corriger au cas par
cas en cas d'anomalie sur un nom composé.

``agency`` (enrichissement ERP local, comme la photo — jamais alimenté par la
sync Marad tant que celle-ci ne l'expose pas) porte l'agence de sous-traitance
pour le personnel externe (ex. "Pelican Marine Services"), regroupé à part
dans le trombinoscope. ``NULL`` = marin employé directement.

Revision ID: 20260720_0106
Revises: 20260709_0105
Create Date: 2026-07-20 00:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260720_0106"
down_revision = "20260709_0105"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("crew_members", sa.Column("first_name", sa.String(length=100), nullable=True))
    op.add_column("crew_members", sa.Column("last_name", sa.String(length=100), nullable=True))
    op.add_column("crew_members", sa.Column("agency", sa.String(length=120), nullable=True))

    # Reprise heuristique depuis full_name : premier mot = prénom, reste = nom.
    # Idempotent (ne touche que les lignes où first_name est encore NULL).
    crew_members = sa.table(
        "crew_members",
        sa.column("id", sa.Integer),
        sa.column("full_name", sa.String),
        sa.column("first_name", sa.String),
        sa.column("last_name", sa.String),
    )
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(crew_members.c.id, crew_members.c.full_name).where(
            crew_members.c.first_name.is_(None)
        )
    ).fetchall()
    for row in rows:
        name = (row.full_name or "").strip()
        if not name:
            continue
        parts = name.split(" ", 1)
        first = parts[0][:100]
        last = parts[1][:100] if len(parts) > 1 else None
        conn.execute(
            crew_members.update()
            .where(crew_members.c.id == row.id)
            .values(first_name=first, last_name=last)
        )


def downgrade():
    op.drop_column("crew_members", "agency")
    op.drop_column("crew_members", "last_name")
    op.drop_column("crew_members", "first_name")
