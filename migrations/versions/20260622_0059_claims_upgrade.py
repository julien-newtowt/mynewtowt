"""claims upgrade — documents, insurer link, provision history

Revision ID: 20260622_0059
Revises: 20260622_0058
Create Date: 2026-06-22 14:00:00

Plan de rattrapage claims (cf. docs/strategy/CLAIMS_GAP_ANALYSIS.md) :
- E1 : pièces jointes (claim_documents) ;
- E2 : lien structuré vers le contrat d'assurance (claims.insurance_contract_id) ;
- E6 : historique des provisions (claim_provision_history).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0059"
down_revision: Union[str, None] = "20260622_0058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # E2 — lien assureur structuré
    op.add_column(
        "claims",
        sa.Column("insurance_contract_id", sa.Integer, sa.ForeignKey("insurance_contracts.id")),
    )
    # E1 — pièces jointes
    op.create_table(
        "claim_documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "claim_id",
            sa.Integer,
            sa.ForeignKey("claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("doc_type", sa.String(20), server_default="autre", nullable=False),
        sa.Column("label", sa.String(200)),
        sa.Column("file_path", sa.String(500)),
        sa.Column("file_mime", sa.String(80)),
        sa.Column("uploaded_by", sa.String(200)),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_claim_documents_claim_id", "claim_documents", ["claim_id"])
    # E6 — historique des provisions
    op.create_table(
        "claim_provision_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "claim_id",
            sa.Integer,
            sa.ForeignKey("claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount_eur", sa.Numeric(12, 2)),
        sa.Column("reason", sa.Text),
        sa.Column("author_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("author_name", sa.String(200)),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_claim_provision_history_claim_id", "claim_provision_history", ["claim_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_claim_provision_history_claim_id", table_name="claim_provision_history")
    op.drop_table("claim_provision_history")
    op.drop_index("ix_claim_documents_claim_id", table_name="claim_documents")
    op.drop_table("claim_documents")
    op.drop_column("claims", "insurance_contract_id")
