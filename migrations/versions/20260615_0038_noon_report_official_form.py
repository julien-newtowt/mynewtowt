"""Noon report : alignement sur le formulaire officiel TOWT (CFOTE_05).

Ajoute à ``noon_reports`` les champs voyage / SOSP / ETA multi-vitesses / ROB
(t) / draft-trim, et trois tables filles : relevés machine par moteur, météo
horaire et voilure horaire.

Revision ID: 20260615_0038
Revises: 20260615_0037
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260615_0038"
down_revision = "20260615_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Colonnes scalaires sur noon_reports.
    cols = [
        sa.Column("report_type", sa.String(length=30), nullable=True),
        sa.Column("previous_port", sa.String(length=10), nullable=True),
        sa.Column("next_port", sa.String(length=10), nullable=True),
        sa.Column("vessel_condition", sa.String(length=20), nullable=True),
        sa.Column("deadweight_t", sa.Float(), nullable=True),
        sa.Column("draft_fwd_m", sa.Float(), nullable=True),
        sa.Column("draft_aft_m", sa.Float(), nullable=True),
        sa.Column("trim_m", sa.Float(), nullable=True),
        sa.Column("time_since_last_h", sa.Float(), nullable=True),
        sa.Column("distance_since_last_nm", sa.Float(), nullable=True),
        sa.Column("speed_since_last_kn", sa.Float(), nullable=True),
        sa.Column("time_since_sosp_h", sa.Float(), nullable=True),
        sa.Column("distance_since_sosp_nm", sa.Float(), nullable=True),
        sa.Column("speed_since_sosp_kn", sa.Float(), nullable=True),
        sa.Column("distance_to_go_nm", sa.Float(), nullable=True),
        sa.Column("announced_eta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("etb", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eta_70_kt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eta_75_kt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eta_80_kt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eta_85_kt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eta_90_kt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_consumption_t", sa.Float(), nullable=True),
        sa.Column("go_density", sa.Float(), nullable=True),
        sa.Column("rob_do_t", sa.Float(), nullable=True),
        sa.Column("rob_uree_t", sa.Float(), nullable=True),
        sa.Column("rob_fw_t", sa.Float(), nullable=True),
        sa.Column("production_fw_t", sa.Float(), nullable=True),
    ]
    for col in cols:
        op.add_column("noon_reports", col)

    op.create_table(
        "noon_report_engines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "noon_report_id",
            sa.Integer(),
            sa.ForeignKey("noon_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("engine", sa.String(length=40), nullable=False),
        sa.Column("running_hours_h", sa.Float(), nullable=True),
        sa.Column("do_consumption_t", sa.Float(), nullable=True),
        sa.Column("running_hours_d", sa.Float(), nullable=True),
        sa.Column("running_hours_d1", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_noon_report_engines_noon_report_id", "noon_report_engines", ["noon_report_id"]
    )

    op.create_table(
        "noon_report_weather",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "noon_report_id",
            sa.Integer(),
            sa.ForeignKey("noon_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot_time", sa.String(length=5), nullable=True),
        sa.Column("tws_kn", sa.Float(), nullable=True),
        sa.Column("awa_deg", sa.Float(), nullable=True),
        sa.Column("aws_kn", sa.Float(), nullable=True),
        sa.Column("sea_state", sa.Integer(), nullable=True),
        sa.Column("sea_direction_deg", sa.Float(), nullable=True),
        sa.Column("ship_speed_kn", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_noon_report_weather_noon_report_id", "noon_report_weather", ["noon_report_id"]
    )

    op.create_table(
        "noon_report_sails",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "noon_report_id",
            sa.Integer(),
            sa.ForeignKey("noon_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot_time", sa.String(length=5), nullable=True),
        sa.Column("j0", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fwd_j1", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fwd_ms", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("aft_j1", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("aft_ms", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sail_boost", sa.Float(), nullable=True),
        sa.Column("me_ps_load_pct", sa.Float(), nullable=True),
        sa.Column("me_sb_load_pct", sa.Float(), nullable=True),
    )
    op.create_index("ix_noon_report_sails_noon_report_id", "noon_report_sails", ["noon_report_id"])


def downgrade() -> None:
    op.drop_index("ix_noon_report_sails_noon_report_id", table_name="noon_report_sails")
    op.drop_table("noon_report_sails")
    op.drop_index("ix_noon_report_weather_noon_report_id", table_name="noon_report_weather")
    op.drop_table("noon_report_weather")
    op.drop_index("ix_noon_report_engines_noon_report_id", table_name="noon_report_engines")
    op.drop_table("noon_report_engines")

    for name in (
        "production_fw_t",
        "rob_fw_t",
        "rob_uree_t",
        "rob_do_t",
        "go_density",
        "total_consumption_t",
        "eta_90_kt",
        "eta_85_kt",
        "eta_80_kt",
        "eta_75_kt",
        "eta_70_kt",
        "etb",
        "announced_eta",
        "distance_to_go_nm",
        "speed_since_sosp_kn",
        "distance_since_sosp_nm",
        "time_since_sosp_h",
        "speed_since_last_kn",
        "distance_since_last_nm",
        "time_since_last_h",
        "trim_m",
        "draft_aft_m",
        "draft_fwd_m",
        "deadweight_t",
        "vessel_condition",
        "next_port",
        "previous_port",
        "report_type",
    ):
        op.drop_column("noon_reports", name)
