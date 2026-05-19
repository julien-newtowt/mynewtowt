"""Phase 2 ERP — commercial, packing list, stowage, SOF, crew enrich, insurance

Revision ID: 20260519_0002
Revises: 20260519_0001
Create Date: 2026-05-19 11:30:00

Crée les tables manquantes pour faire revenir le coeur ERP de la V3.0.0 :
- commercial_clients, rate_grids, rate_grid_lines, rate_offers,
  commercial_orders, order_assignments
- packing_lists, packing_list_batches, packing_list_audit,
  packing_list_documents, portal_access_logs, portal_messages
- stowage_plans, stowage_items
- sof_events, eta_shifts, onboard_messages, onboard_message_mentions,
  cargo_documents
- crew_tickets, insurance_contracts
- enrich crew_members : schengen_days_in_window, schengen_window_end,
  visa_us_expires_at, visa_br_expires_at, seaman_book_*
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260519_0002"
down_revision: Union[str, None] = "20260519_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── Commercial ─────────────────────────────────────────────────
    op.create_table(
        "commercial_clients",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("client_type", sa.String(30), nullable=False),
        sa.Column("contact_name", sa.String(200)),
        sa.Column("contact_email", sa.String(200)),
        sa.Column("contact_phone", sa.String(50)),
        sa.Column("address", sa.Text),
        sa.Column("country", sa.String(2)),
        sa.Column("vat_number", sa.String(40)),
        sa.Column("notes", sa.Text),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("pipedrive_org_id", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_commercial_clients_name", "commercial_clients", ["name"])

    op.create_table(
        "rate_grids",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("reference", sa.String(20), unique=True, nullable=False),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("commercial_clients.id"), nullable=False, index=True),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("valid_to", sa.Date),
        sa.Column("currency", sa.String(3), server_default="EUR", nullable=False),
        sa.Column("base_rate_per_palette", sa.Numeric(10, 2), nullable=False),
        sa.Column("adjustment_index", sa.Numeric(6, 4), server_default="1.0000", nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "rate_grid_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("grid_id", sa.Integer, sa.ForeignKey("rate_grids.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("bracket_key", sa.String(20), nullable=False),
        sa.Column("bracket_label", sa.String(80), nullable=False),
        sa.Column("max_qty", sa.Integer, nullable=False),
        sa.Column("coeff", sa.Numeric(6, 4), nullable=False),
    )

    op.create_table(
        "rate_offers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("reference", sa.String(20), unique=True, nullable=False),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("commercial_clients.id"), nullable=False, index=True),
        sa.Column("grid_id", sa.Integer, sa.ForeignKey("rate_grids.id")),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("estimated_palettes", sa.Integer),
        sa.Column("proposed_rate_eur", sa.Numeric(10, 2)),
        sa.Column("total_eur", sa.Numeric(12, 2)),
        sa.Column("valid_until", sa.Date),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("declined_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text),
        sa.Column("pipedrive_deal_id", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "commercial_orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("reference", sa.String(20), unique=True, nullable=False),
        sa.Column("client_id", sa.Integer, sa.ForeignKey("commercial_clients.id"), nullable=False, index=True),
        sa.Column("offer_id", sa.Integer, sa.ForeignKey("rate_offers.id")),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), index=True),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("booked_palettes", sa.Integer, server_default="0", nullable=False),
        sa.Column("rate_per_palette_eur", sa.Numeric(10, 2)),
        sa.Column("total_eur", sa.Numeric(12, 2)),
        sa.Column("cargo_description", sa.Text),
        sa.Column("description_of_goods", sa.Text),
        sa.Column("shipper_name", sa.String(200)),
        sa.Column("shipper_address", sa.Text),
        sa.Column("consignee_name", sa.String(200)),
        sa.Column("consignee_address", sa.Text),
        sa.Column("notify_name", sa.String(200)),
        sa.Column("notify_address", sa.Text),
        sa.Column("pipedrive_deal_id", sa.Integer),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "order_assignments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("commercial_orders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), nullable=False, index=True),
        sa.Column("palettes_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("pallet_format", sa.String(20), server_default="EPAL", nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ─── Packing lists & portal ──────────────────────────────────────
    op.create_table(
        "packing_lists",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("commercial_orders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("token", sa.String(32), unique=True, nullable=False, index=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(200)),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "packing_list_batches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("packing_list_id", sa.Integer, sa.ForeignKey("packing_lists.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("batch_number", sa.Integer),
        sa.Column("pallet_format", sa.String(20), server_default="EPAL", nullable=False),
        sa.Column("pallet_count", sa.Integer, server_default="1", nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("hs_code", sa.String(20)),
        sa.Column("weight_kg", sa.Float),
        sa.Column("cubage_m3", sa.Float),
        sa.Column("length_cm", sa.Float),
        sa.Column("width_cm", sa.Float),
        sa.Column("height_cm", sa.Float),
        sa.Column("hazardous", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("imdg_class", sa.String(20)),
        sa.Column("un_number", sa.String(10)),
        sa.Column("stackable", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("marks_and_numbers", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "packing_list_audit",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("packing_list_id", sa.Integer, sa.ForeignKey("packing_lists.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("batch_id", sa.Integer),
        sa.Column("actor", sa.String(40), nullable=False),
        sa.Column("actor_name", sa.String(200)),
        sa.Column("field", sa.String(60), nullable=False),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
    )

    op.create_table(
        "packing_list_documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("packing_list_id", sa.Integer, sa.ForeignKey("packing_lists.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("label", sa.String(200)),
        sa.Column("file_path", sa.String(500)),
        sa.Column("file_mime", sa.String(80)),
        sa.Column("uploaded_by", sa.String(200)),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "portal_access_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("portal_type", sa.String(40), server_default="cargo", nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, index=True),
        sa.Column("packing_list_id", sa.Integer),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("user_agent", sa.String(400)),
        sa.Column("path", sa.String(200)),
        sa.Column("accessed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
    )

    op.create_table(
        "portal_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("packing_list_id", sa.Integer, sa.ForeignKey("packing_lists.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("sender", sa.String(20), nullable=False),
        sa.Column("sender_name", sa.String(200)),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("is_read", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ─── Stowage ─────────────────────────────────────────────────────
    op.create_table(
        "stowage_plans",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id", ondelete="CASCADE"), nullable=False, unique=True, index=True),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("approved_by", sa.String(200)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "stowage_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("stowage_plans.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("commercial_orders.id")),
        sa.Column("batch_id", sa.Integer, sa.ForeignKey("packing_list_batches.id")),
        sa.Column("zone", sa.String(20), nullable=False),
        sa.Column("pallet_format", sa.String(20), server_default="EPAL", nullable=False),
        sa.Column("pallet_count", sa.Integer, server_default="1", nullable=False),
        sa.Column("weight_kg", sa.Float),
        sa.Column("is_dangerous", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("is_oversized", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ─── SOF + Onboard messaging ─────────────────────────────────────
    op.create_table(
        "sof_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("label", sa.String(200)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("port_id", sa.Integer, sa.ForeignKey("ports.id")),
        sa.Column("latitude", sa.Float),
        sa.Column("longitude", sa.Float),
        sa.Column("notes", sa.Text),
        sa.Column("recorded_by_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("recorded_by_name", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "eta_shifts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("previous_eta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("new_eta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("detail", sa.Text),
        sa.Column("declared_by_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("declared_by_name", sa.String(200)),
        sa.Column("declared_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "onboard_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), index=True),
        sa.Column("vessel_id", sa.Integer, sa.ForeignKey("vessels.id"), index=True),
        sa.Column("author_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("author_name", sa.String(200), nullable=False),
        sa.Column("is_bot", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("pinned", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
    )

    op.create_table(
        "onboard_message_mentions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("message_id", sa.Integer, sa.ForeignKey("onboard_messages.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("mentioned_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("mentioned_text", sa.String(80), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "cargo_documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("reference", sa.String(100)),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("party_name", sa.String(200)),
        sa.Column("body", sa.Text),
        sa.Column("file_path", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ─── Crew enrich + tickets + insurance ───────────────────────────
    op.add_column("crew_members", sa.Column("schengen_days_in_window", sa.Integer))
    op.add_column("crew_members", sa.Column("schengen_window_end", sa.Date))
    op.add_column("crew_members", sa.Column("visa_us_expires_at", sa.Date))
    op.add_column("crew_members", sa.Column("visa_br_expires_at", sa.Date))
    op.add_column("crew_members", sa.Column("seaman_book_number", sa.String(60)))
    op.add_column("crew_members", sa.Column("seaman_book_expires_at", sa.Date))

    op.create_table(
        "crew_tickets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("crew_member_id", sa.Integer, sa.ForeignKey("crew_members.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("assignment_id", sa.Integer, sa.ForeignKey("crew_assignments.id")),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id")),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("reference", sa.String(100)),
        sa.Column("carrier", sa.String(100)),
        sa.Column("departure_at", sa.DateTime(timezone=True)),
        sa.Column("arrival_at", sa.DateTime(timezone=True)),
        sa.Column("departure_location", sa.String(200)),
        sa.Column("arrival_location", sa.String(200)),
        sa.Column("cost_eur", sa.Float),
        sa.Column("file_path", sa.String(500)),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "insurance_contracts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("reference", sa.String(80), nullable=False),
        sa.Column("insurer", sa.String(200), nullable=False),
        sa.Column("broker", sa.String(200)),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("valid_to", sa.Date, nullable=False),
        sa.Column("premium_eur", sa.Float),
        sa.Column("deductible_eur", sa.Float),
        sa.Column("coverage_amount_eur", sa.Float),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("file_path", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("insurance_contracts")
    op.drop_table("crew_tickets")
    op.drop_column("crew_members", "seaman_book_expires_at")
    op.drop_column("crew_members", "seaman_book_number")
    op.drop_column("crew_members", "visa_br_expires_at")
    op.drop_column("crew_members", "visa_us_expires_at")
    op.drop_column("crew_members", "schengen_window_end")
    op.drop_column("crew_members", "schengen_days_in_window")
    op.drop_table("cargo_documents")
    op.drop_table("onboard_message_mentions")
    op.drop_table("onboard_messages")
    op.drop_table("eta_shifts")
    op.drop_table("sof_events")
    op.drop_table("stowage_items")
    op.drop_table("stowage_plans")
    op.drop_table("portal_messages")
    op.drop_table("portal_access_logs")
    op.drop_table("packing_list_documents")
    op.drop_table("packing_list_audit")
    op.drop_table("packing_list_batches")
    op.drop_table("packing_lists")
    op.drop_table("order_assignments")
    op.drop_table("commercial_orders")
    op.drop_table("rate_offers")
    op.drop_table("rate_grid_lines")
    op.drop_table("rate_grids")
    op.drop_index("ix_commercial_clients_name", "commercial_clients")
    op.drop_table("commercial_clients")
