"""MRV lot 10 — sorties réglementaires OVDLA / OVDBR (entrées gelées).

Crée les deux tables de snapshot des datasets déposés chez DNV :

- ``mrv_log_abstract_entries`` — 1:1 (UNIQUE) avec ``nav_events`` : ligne OVDLA
  gelée + ``verification_status`` (taxonomie qualité, ``under_conformity``
  bloque la consolidation) + ``source_system`` (défaut ``MyTOWT`` — Q10) ;
- ``mrv_bunkering_entries`` — 1:1 (UNIQUE) avec ``bunker_operations`` : ligne
  OVDBR gelée.

Le ``payload`` JSON porte la ligne figée (en-têtes OVDLA/OVDBR exacts).

Revision ID: 20260709_0104
Revises: 20260709_0103
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260709_0104"
down_revision = "20260709_0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mrv_log_abstract_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("nav_events.id", ondelete="CASCADE", name="fk_mrv_la_event"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "source_system", sa.String(length=40), nullable=False, server_default="MyTOWT"
        ),
        sa.Column(
            "verification_status",
            sa.String(length=20),
            nullable=False,
            server_default="conform",
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_mrv_log_abstract_event", "mrv_log_abstract_entries", ["event_id"]
    )
    op.create_index(
        "ix_mrv_log_abstract_status",
        "mrv_log_abstract_entries",
        ["verification_status"],
    )

    op.create_table(
        "mrv_bunkering_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "bunker_id",
            sa.Integer(),
            sa.ForeignKey(
                "bunker_operations.id", ondelete="CASCADE", name="fk_mrv_br_bunker"
            ),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "source_system", sa.String(length=40), nullable=False, server_default="MyTOWT"
        ),
        sa.Column(
            "verification_status",
            sa.String(length=20),
            nullable=False,
            server_default="conform",
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_mrv_bunkering_bunker", "mrv_bunkering_entries", ["bunker_id"]
    )
    op.create_index(
        "ix_mrv_bunkering_status", "mrv_bunkering_entries", ["verification_status"]
    )


def downgrade() -> None:
    op.drop_index("ix_mrv_bunkering_status", table_name="mrv_bunkering_entries")
    op.drop_index("ix_mrv_bunkering_bunker", table_name="mrv_bunkering_entries")
    op.drop_table("mrv_bunkering_entries")
    op.drop_index("ix_mrv_log_abstract_status", table_name="mrv_log_abstract_entries")
    op.drop_index("ix_mrv_log_abstract_event", table_name="mrv_log_abstract_entries")
    op.drop_table("mrv_log_abstract_entries")
