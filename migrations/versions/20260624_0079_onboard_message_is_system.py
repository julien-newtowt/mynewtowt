"""Add onboard_messages.is_system for system journal messages (ONB-04).

Les messages système (journal des actions clés : SOF signé, clôture d'escale…)
sont postés automatiquement dans le fil de bord et ne sont ni éditables ni
supprimables par l'équipage. Colonne booléenne avec défaut serveur ``false``
→ changement additif sûr (lignes existantes = messages humains/bot).

Revision ID: 20260624_0079
Revises: 20260624_0078
Create Date: 2026-06-24 00:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260624_0079"
down_revision = "20260624_0078"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "onboard_messages",
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade():
    op.drop_column("onboard_messages", "is_system")
