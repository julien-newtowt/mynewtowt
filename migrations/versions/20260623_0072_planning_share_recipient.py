"""PLN-04 — destinataire + langue + sélection leg-à-leg sur planning_shares

Revision ID: 20260623_0072
Revises: 20260623_0071
Create Date: 2026-06-23 13:30:00

Reprise V2 : réintroduit sur ``planning_shares`` le suivi du destinataire
(nom/société/email/notes), la langue du rendu public (``lang``, corrige le
partage EN cassé) et la sélection leg-à-leg (``legs_ids``, CSV d'IDs). Colonnes
additives — migration sûre. ``lang`` posée à ``'fr'`` pour les lignes
existantes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260623_0072"
down_revision: Union[str, None] = "20260623_0071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("planning_shares", sa.Column("recipient_name", sa.String(200), nullable=True))
    op.add_column("planning_shares", sa.Column("recipient_company", sa.String(200), nullable=True))
    op.add_column("planning_shares", sa.Column("recipient_email", sa.String(200), nullable=True))
    op.add_column("planning_shares", sa.Column("recipient_notes", sa.Text(), nullable=True))
    op.add_column(
        "planning_shares",
        sa.Column("lang", sa.String(5), nullable=False, server_default="fr"),
    )
    op.add_column("planning_shares", sa.Column("legs_ids", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("planning_shares", "legs_ids")
    op.drop_column("planning_shares", "lang")
    op.drop_column("planning_shares", "recipient_notes")
    op.drop_column("planning_shares", "recipient_email")
    op.drop_column("planning_shares", "recipient_company")
    op.drop_column("planning_shares", "recipient_name")
