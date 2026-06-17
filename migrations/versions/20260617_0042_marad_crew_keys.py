"""Marad (read-only) : clés externes de réconciliation crew.

Ajoute ``crew_members.marad_id`` et ``crew_certifications.marad_document_id``
pour l'intégration LECTURE SEULE des données crew depuis Marad (MaraSoft).
cf. docs/integrations/marad-crew-readonly.md.

Revision ID: 20260617_0042
Revises: 20260617_0041
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260617_0042"
down_revision = "20260617_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Marad expose des identifiants GUID (ex. "3fa85f64-5717-4562-b3fc-2c963f66afa6").
    op.add_column("crew_members", sa.Column("marad_id", sa.String(length=36), nullable=True))
    op.create_index("ix_crew_members_marad_id", "crew_members", ["marad_id"], unique=True)
    op.add_column(
        "crew_certifications", sa.Column("marad_document_id", sa.String(length=36), nullable=True)
    )
    op.create_index(
        "ix_crew_certifications_marad_document_id",
        "crew_certifications",
        ["marad_document_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_crew_certifications_marad_document_id", table_name="crew_certifications")
    op.drop_column("crew_certifications", "marad_document_id")
    op.drop_index("ix_crew_members_marad_id", table_name="crew_members")
    op.drop_column("crew_members", "marad_id")
