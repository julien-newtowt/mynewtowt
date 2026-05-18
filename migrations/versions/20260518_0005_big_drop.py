"""big drop: cashbox, onboard (noon/watch/checklist/visitor), chat,
escale ops, crew, finance, MRV, claims, vessel positions

Revision ID: 20260518_0005
Revises: 20260518_0004
Create Date: 2026-05-18 23:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260518_0005"
down_revision: Union[str, None] = "20260518_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── onboard cashbox ──────────────────────────────────────────────
    op.create_table(
        "onboard_cashboxes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("vessel_id", sa.Integer, sa.ForeignKey("vessels.id"), unique=True, nullable=False),
        sa.Column("is_active", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "cashbox_movements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cashbox_id", sa.Integer, sa.ForeignKey("onboard_cashboxes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id")),
        sa.Column("port_id", sa.Integer, sa.ForeignKey("ports.id")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("recorded_by_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("receipt_url", sa.String(500)),
    )
    op.create_index("ix_cashbox_mov_cb_date", "cashbox_movements", ["cashbox_id", "occurred_at"])
    op.create_index("ix_cashbox_mov_currency", "cashbox_movements", ["currency"])
    op.create_table(
        "cashbox_closures",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cashbox_id", sa.Integer, sa.ForeignKey("onboard_cashboxes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("counted_balance", sa.Numeric(14, 2), nullable=False),
        sa.Column("computed_balance", sa.Numeric(14, 2), nullable=False),
        sa.Column("variance", sa.Numeric(14, 2), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("closed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("closed_by_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.UniqueConstraint("cashbox_id", "currency", "period_end", name="uq_closure_period"),
    )

    # ── onboard navigation ───────────────────────────────────────────
    op.create_table(
        "noon_reports",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), nullable=False, index=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Float, nullable=False),
        sa.Column("longitude", sa.Float, nullable=False),
        sa.Column("sog_avg", sa.Float),
        sa.Column("cog_avg", sa.Float),
        sa.Column("wind_speed_kn", sa.Float),
        sa.Column("wind_direction_deg", sa.Float),
        sa.Column("sea_state_bf", sa.Integer),
        sa.Column("visibility_nm", sa.Float),
        sa.Column("barometric_hpa", sa.Float),
        sa.Column("fuel_consumed_24h_l", sa.Float),
        sa.Column("distance_24h_nm", sa.Float),
        sa.Column("rob_fuel_l", sa.Float),
        sa.Column("remarks", sa.Text),
        sa.Column("recorded_by_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "watch_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), nullable=False, index=True),
        sa.Column("watch_date", sa.Date, nullable=False),
        sa.Column("watch_period", sa.String(5), nullable=False),
        sa.Column("officer_on_watch", sa.String(200)),
        sa.Column("officer_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("entry", sa.Text, nullable=False),
        sa.Column("weather_summary", sa.String(300)),
        sa.Column("signed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "onboard_checklists",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("items_json", sa.Text),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("signed_by_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("signed_by_name", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "visitor_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), nullable=False, index=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("company", sa.String(200)),
        sa.Column("purpose", sa.String(200)),
        sa.Column("id_document", sa.String(80)),
        sa.Column("time_in", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_out", sa.DateTime(timezone=True)),
        sa.Column("escorted_by", sa.String(200)),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── chat ─────────────────────────────────────────────────────────
    op.create_table(
        "chat_conversations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), index=True),
        sa.Column("client_account_id", sa.Integer, sa.ForeignKey("client_accounts.id"), index=True),
        sa.Column("title", sa.String(200)),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("conversation_id", sa.Integer, sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("tool_calls", sa.JSON),
        sa.Column("tool_results", sa.JSON),
        sa.Column("tokens_in", sa.Integer),
        sa.Column("tokens_out", sa.Integer),
        sa.Column("cost_usd", sa.Numeric(8, 4)),
        sa.Column("flagged_injection", sa.Boolean),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── escale ops ───────────────────────────────────────────────────
    op.create_table(
        "escale_operations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), nullable=False, index=True),
        sa.Column("direction", sa.String(10)),
        sa.Column("operation_type", sa.String(40), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("label", sa.String(200)),
        sa.Column("notes", sa.Text),
        sa.Column("planned_start", sa.DateTime(timezone=True)),
        sa.Column("planned_end", sa.DateTime(timezone=True)),
        sa.Column("actual_start", sa.DateTime(timezone=True)),
        sa.Column("actual_end", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), server_default="planned", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "docker_shifts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), nullable=False, index=True),
        sa.Column("direction", sa.String(10)),
        sa.Column("company", sa.String(200)),
        sa.Column("nb_dockers", sa.Integer, server_default="0"),
        sa.Column("palettes_target", sa.Integer),
        sa.Column("palettes_done", sa.Integer, server_default="0"),
        sa.Column("planned_start", sa.DateTime(timezone=True)),
        sa.Column("planned_end", sa.DateTime(timezone=True)),
        sa.Column("actual_start", sa.DateTime(timezone=True)),
        sa.Column("actual_end", sa.DateTime(timezone=True)),
        sa.Column("cost_eur", sa.Float),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── crew ─────────────────────────────────────────────────────────
    op.create_table(
        "crew_members",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(60), nullable=False),
        sa.Column("nationality", sa.CHAR(2)),
        sa.Column("date_of_birth", sa.Date),
        sa.Column("passport_number", sa.String(60)),
        sa.Column("passport_expires_at", sa.Date),
        sa.Column("schengen_status", sa.String(20), server_default="compliant", nullable=False),
        sa.Column("email", sa.String(255)),
        sa.Column("phone", sa.String(50)),
        sa.Column("is_active", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "crew_assignments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("crew_member_id", sa.Integer, sa.ForeignKey("crew_members.id"), nullable=False, index=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), nullable=False, index=True),
        sa.Column("role_on_board", sa.String(60)),
        sa.Column("embark_at", sa.DateTime(timezone=True)),
        sa.Column("disembark_at", sa.DateTime(timezone=True)),
        sa.Column("embark_port_id", sa.Integer, sa.ForeignKey("ports.id")),
        sa.Column("disembark_port_id", sa.Integer, sa.ForeignKey("ports.id")),
        sa.Column("notes", sa.Text),
    )
    op.create_table(
        "crew_certifications",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("crew_member_id", sa.Integer, sa.ForeignKey("crew_members.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("reference", sa.String(100)),
        sa.Column("issued_at", sa.Date),
        sa.Column("expires_at", sa.Date),
        sa.Column("document_url", sa.String(500)),
    )
    op.create_table(
        "crew_leaves",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("crew_member_id", sa.Integer, sa.ForeignKey("crew_members.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(20), server_default="requested", nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("decided_by_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── finance / KPI ────────────────────────────────────────────────
    op.create_table(
        "leg_finances",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), unique=True, nullable=False),
        sa.Column("revenue_eur", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("port_fees_eur", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("docker_costs_eur", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("opex_share_eur", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("other_costs_eur", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("margin_eur", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "opex_parameters",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("parameter_name", sa.String(80), unique=True, nullable=False),
        sa.Column("parameter_value", sa.Numeric(12, 4), nullable=False),
        sa.Column("unit", sa.String(20)),
        sa.Column("category", sa.String(40)),
        sa.Column("description", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "port_configs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("port_id", sa.Integer, sa.ForeignKey("ports.id"), unique=True, nullable=False),
        sa.Column("agency_fee_eur", sa.Numeric(10, 2)),
        sa.Column("pilot_fee_eur", sa.Numeric(10, 2)),
        sa.Column("berth_fee_per_day_eur", sa.Numeric(10, 2)),
        sa.Column("docker_fee_per_palette_eur", sa.Numeric(8, 2)),
        sa.Column("notes", sa.Text),
    )
    op.create_table(
        "leg_kpis",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), unique=True, nullable=False),
        sa.Column("palettes_carried", sa.Integer, server_default="0"),
        sa.Column("tonnage_kg", sa.Numeric(12, 2), server_default="0"),
        sa.Column("distance_nm", sa.Numeric(10, 2)),
        sa.Column("duration_hours", sa.Numeric(8, 2)),
        sa.Column("avg_speed_kn", sa.Numeric(6, 2)),
        sa.Column("on_time", sa.Boolean, server_default=sa.true()),
        sa.Column("occupancy_pct", sa.Numeric(5, 2)),
        sa.Column("co2_avoided_kg", sa.Numeric(12, 2)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── MRV ──────────────────────────────────────────────────────────
    op.create_table(
        "mrv_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), nullable=False, index=True),
        sa.Column("event_kind", sa.String(40), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fuel_type", sa.String(20), server_default="MDO", nullable=False),
        sa.Column("fuel_volume_l", sa.Numeric(12, 2)),
        sa.Column("fuel_mass_t", sa.Numeric(10, 3)),
        sa.Column("rob_l", sa.Numeric(12, 2)),
        sa.Column("distance_nm", sa.Numeric(10, 2)),
        sa.Column("time_at_sea_h", sa.Numeric(8, 2)),
        sa.Column("cargo_carried_t", sa.Numeric(10, 2)),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "mrv_parameters",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(80), unique=True, nullable=False),
        sa.Column("value", sa.Numeric(12, 4), nullable=False),
        sa.Column("unit", sa.String(20)),
        sa.Column("description", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── claims + positions ──────────────────────────────────────────
    op.create_table(
        "claims",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("reference", sa.String(20), unique=True, nullable=False),
        sa.Column("claim_type", sa.String(20), nullable=False),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), index=True),
        sa.Column("booking_id", sa.Integer, sa.ForeignKey("bookings.id")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), server_default="open", nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("declared_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("provision_eur", sa.Numeric(12, 2)),
        sa.Column("settled_eur", sa.Numeric(12, 2)),
        sa.Column("insurer", sa.String(200)),
        sa.Column("insurer_claim_ref", sa.String(80)),
        sa.Column("cargo_position", sa.String(40)),
        sa.Column("created_by_id", sa.Integer, sa.ForeignKey("users.id")),
    )
    op.create_table(
        "claim_timeline",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("claim_id", sa.Integer, sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("author_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("author_name", sa.String(200)),
        sa.Column("kind", sa.String(30), server_default="note", nullable=False),
        sa.Column("body", sa.Text, nullable=False),
    )
    op.create_table(
        "vessel_positions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("vessel_id", sa.Integer, sa.ForeignKey("vessels.id"), nullable=False, index=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("latitude", sa.Float),
        sa.Column("longitude", sa.Float),
        sa.Column("sog_kn", sa.Float),
        sa.Column("cog_deg", sa.Float),
        sa.Column("source", sa.String(40), server_default="manual", nullable=False),
    )


def downgrade() -> None:
    for tbl in [
        "vessel_positions", "claim_timeline", "claims",
        "mrv_parameters", "mrv_events",
        "leg_kpis", "port_configs", "opex_parameters", "leg_finances",
        "crew_leaves", "crew_certifications", "crew_assignments", "crew_members",
        "docker_shifts", "escale_operations",
        "chat_messages", "chat_conversations",
        "visitor_logs", "onboard_checklists", "watch_logs", "noon_reports",
        "cashbox_closures", "cashbox_movements", "onboard_cashboxes",
    ]:
        op.drop_table(tbl)
