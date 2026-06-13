"""FLX-03 + ENV-03 — liens MRV ↔ données du bord et méthode de calcul Anemos.

- mrv_events.noon_report_id / sof_event_id : lien unique vers la source
  du bord (noon report = référence n°1 du MRV, SOF mappé via
  SOF_TO_MRV_MAP) — garantit l'idempotence de la synchronisation.
- anemos_certificates.method / distance_source : traçabilité du calcul
  ('declared' vs 'theoretical' ; 'noon_reports' vs 'planned').

Revision ID: 20260612_0025
Revises: 20260612_0024
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0025"
down_revision = "20260612_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- mrv_events : liens vers les sources du bord -----------------------
    op.add_column(
        "mrv_events",
        sa.Column(
            "noon_report_id",
            sa.Integer(),
            sa.ForeignKey("noon_reports.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "mrv_events",
        sa.Column(
            "sof_event_id",
            sa.Integer(),
            sa.ForeignKey("sof_events.id"),
            nullable=True,
        ),
    )
    op.create_unique_constraint("uq_mrv_events_noon_report_id", "mrv_events", ["noon_report_id"])
    op.create_unique_constraint("uq_mrv_events_sof_event_id", "mrv_events", ["sof_event_id"])

    # --- anemos_certificates : méthode + source de distance ----------------
    op.add_column("anemos_certificates", sa.Column("method", sa.String(length=20), nullable=True))
    op.add_column(
        "anemos_certificates", sa.Column("distance_source", sa.String(length=20), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("anemos_certificates", "distance_source")
    op.drop_column("anemos_certificates", "method")
    op.drop_constraint("uq_mrv_events_sof_event_id", "mrv_events", type_="unique")
    op.drop_constraint("uq_mrv_events_noon_report_id", "mrv_events", type_="unique")
    op.drop_column("mrv_events", "sof_event_id")
    op.drop_column("mrv_events", "noon_report_id")
