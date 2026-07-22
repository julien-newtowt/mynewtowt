"""QHSE — fondations (Phase 0) : rapports, CAPA, root-cause, codes déficience.

Crée le socle du module QHSE (sécurité/qualité/environnement) :

- ``qhse_reports`` — rapport source (accident/NC/near-miss/observation/
  déficience/casualty), référence ``vessels``/``legs``/``users``/
  ``crew_members``/``claims`` existants — pas de duplication d'entité, pas
  de table ``port_call`` inventée (n'existe pas dans ce dépôt ; le contexte
  voyage passe par ``leg_id``).
- ``deficiency_codes`` + ``qhse_report_deficiency_codes`` — référentiel des
  codes PSC/Class et son association many-to-many avec un rapport.
- ``qhse_corrective_actions`` / ``qhse_root_cause_evaluations`` — les deux
  workflows containment/prévention, en 1:0..1 avec ``qhse_reports``.

Revision ID: 20260722_0106
Revises: 20260709_0105
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260722_0106"
down_revision = "20260709_0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qhse_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "vessel_id",
            sa.Integer(),
            sa.ForeignKey("vessels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "leg_id",
            sa.Integer(),
            sa.ForeignKey("legs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("grade", sa.String(length=20), nullable=False),
        sa.Column(
            "report_source",
            sa.String(length=20),
            nullable=False,
            server_default="operational",
        ),
        sa.Column("issued_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_place", sa.String(length=200), nullable=True),
        sa.Column("issued_by_raw", sa.String(length=200), nullable=True),
        sa.Column(
            "reporter_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "reporter_crew_member_id",
            sa.Integer(),
            sa.ForeignKey("crew_members.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reporter_organization_type", sa.String(length=30), nullable=True),
        sa.Column("contact", sa.String(length=200), nullable=True),
        sa.Column("description_added_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "description_added_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "claim_id",
            sa.Integer(),
            sa.ForeignKey("claims.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_qhse_reports_vessel", "qhse_reports", ["vessel_id"])
    op.create_index("ix_qhse_reports_leg", "qhse_reports", ["leg_id"])
    op.create_index("ix_qhse_reports_grade", "qhse_reports", ["grade"])
    op.create_index("ix_qhse_reports_issued_date", "qhse_reports", ["issued_date"])

    op.create_table(
        "deficiency_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=20), nullable=False, unique=True),
        sa.Column("authority", sa.String(length=80), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
    )

    op.create_table(
        "qhse_report_deficiency_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "report_id",
            sa.Integer(),
            sa.ForeignKey("qhse_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "deficiency_code_id",
            sa.Integer(),
            sa.ForeignKey("deficiency_codes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("report_id", "deficiency_code_id", name="uq_qhse_report_defcode"),
    )
    op.create_index("ix_qhse_report_defcodes_report", "qhse_report_deficiency_codes", ["report_id"])

    op.create_table(
        "qhse_corrective_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "report_id",
            sa.Integer(),
            sa.ForeignKey("qhse_reports.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("limit_date", sa.Date(), nullable=True),
        sa.Column("postponed_date", sa.Date(), nullable=True),
        sa.Column("finished_date", sa.Date(), nullable=True),
        sa.Column("proposed_date", sa.Date(), nullable=True),
        sa.Column(
            "proposed_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approved_date", sa.Date(), nullable=True),
        sa.Column(
            "approved_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("implemented_date", sa.Date(), nullable=True),
        sa.Column(
            "implemented_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "responsible_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("responsible_rank", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
    )

    op.create_table(
        "qhse_root_cause_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "report_id",
            sa.Integer(),
            sa.ForeignKey("qhse_reports.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("root_cause_text", sa.Text(), nullable=True),
        sa.Column("root_cause_category", sa.String(length=40), nullable=True),
        sa.Column("preventative_action", sa.Text(), nullable=True),
        sa.Column("limit_date", sa.Date(), nullable=True),
        sa.Column("postponed_date", sa.Date(), nullable=True),
        sa.Column("finished_date", sa.Date(), nullable=True),
        sa.Column("proposed_date", sa.Date(), nullable=True),
        sa.Column(
            "proposed_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approved_date", sa.Date(), nullable=True),
        sa.Column(
            "approved_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("implemented_date", sa.Date(), nullable=True),
        sa.Column(
            "implemented_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "responsible_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("responsible_rank", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
    )


def downgrade() -> None:
    op.drop_table("qhse_root_cause_evaluations")
    op.drop_table("qhse_corrective_actions")
    op.drop_index("ix_qhse_report_defcodes_report", table_name="qhse_report_deficiency_codes")
    op.drop_table("qhse_report_deficiency_codes")
    op.drop_table("deficiency_codes")
    op.drop_index("ix_qhse_reports_issued_date", table_name="qhse_reports")
    op.drop_index("ix_qhse_reports_grade", table_name="qhse_reports")
    op.drop_index("ix_qhse_reports_leg", table_name="qhse_reports")
    op.drop_index("ix_qhse_reports_vessel", table_name="qhse_reports")
    op.drop_table("qhse_reports")
