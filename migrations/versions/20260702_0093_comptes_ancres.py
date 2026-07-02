"""Comptes-ancres (P11) — attributs stratégiques sur le client commercial.

Ajoute sur ``commercial_clients`` la notion de **compte-ancre** (partenaire
stratégique qui sécurise le remplissage) :
- ``is_anchor`` (bool) — le client est un compte-ancre ;
- ``annual_volume_commitment`` (int, nullable) — engagement de volume annuel
  (palettes/an) ;
- ``capacity_priority`` (int, défaut 0) — rang de priorité d'allocation de cale
  (0 = standard) ;
- ``co_branding_status`` (str, défaut « none ») — statut de co-branding
  (none | pending | active).

Revision ID: 20260702_0093
Revises: 20260702_0091
Create Date: 2026-07-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260702_0093"
down_revision = "20260702_0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "commercial_clients",
        sa.Column(
            "is_anchor",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "commercial_clients",
        sa.Column("annual_volume_commitment", sa.Integer(), nullable=True),
    )
    op.add_column(
        "commercial_clients",
        sa.Column(
            "capacity_priority",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "commercial_clients",
        sa.Column(
            "co_branding_status",
            sa.String(length=20),
            nullable=False,
            server_default="none",
        ),
    )
    op.create_index("ix_commercial_clients_is_anchor", "commercial_clients", ["is_anchor"])


def downgrade() -> None:
    op.drop_index("ix_commercial_clients_is_anchor", table_name="commercial_clients")
    op.drop_column("commercial_clients", "co_branding_status")
    op.drop_column("commercial_clients", "capacity_priority")
    op.drop_column("commercial_clients", "annual_volume_commitment")
    op.drop_column("commercial_clients", "is_anchor")
