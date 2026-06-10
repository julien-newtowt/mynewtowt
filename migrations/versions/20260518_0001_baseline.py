"""baseline schema — v3.0.0

Revision ID: 20260518_0001
Revises:
Create Date: 2026-05-18 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260518_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(50), unique=True, nullable=False),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200)),
        sa.Column("role", sa.String(40), nullable=False, server_default="operation"),
        sa.Column("language", sa.String(5), server_default="fr"),
        sa.Column("is_active", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("must_change_password", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("mfa_secret", sa.String(64)),
        sa.Column("mfa_enabled", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_active", "users", ["is_active"])

    op.create_table(
        "client_accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("contact_name", sa.String(200)),
        sa.Column("phone", sa.String(50)),
        sa.Column("vat_number", sa.String(50)),
        sa.Column("country", sa.String(2)),
        sa.Column("billing_address", sa.Text),
        sa.Column("language", sa.String(5), server_default="fr"),
        sa.Column("is_verified", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("mfa_secret", sa.String(64)),
        sa.Column("mfa_enabled", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("must_change_password", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("segment", sa.String(20), server_default="occasional", nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_client_accounts_segment", "client_accounts", ["segment"])

    op.create_table(
        "vessels",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(4), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("imo_number", sa.String(20)),
        sa.Column("flag", sa.String(2)),
        sa.Column("dwt", sa.Float),
        sa.Column("capacity_palettes", sa.Integer, server_default="850", nullable=False),
        sa.Column("default_speed_kn", sa.Float, server_default="8.0", nullable=False),
        sa.Column("default_elongation", sa.Float, server_default="1.15", nullable=False),
        sa.Column("opex_daily_sea_eur", sa.Float),
        sa.Column("is_active", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "ports",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("locode", sa.String(5), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("latitude", sa.Float),
        sa.Column("longitude", sa.Float),
        sa.Column("timezone", sa.String(50)),
    )

    op.create_table(
        "legs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("leg_code", sa.String(20), unique=True, nullable=False),
        sa.Column("vessel_id", sa.Integer, sa.ForeignKey("vessels.id"), nullable=False),
        sa.Column("departure_port_id", sa.Integer, sa.ForeignKey("ports.id"), nullable=False),
        sa.Column("arrival_port_id", sa.Integer, sa.ForeignKey("ports.id"), nullable=False),
        sa.Column("etd_ref", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eta_ref", sa.DateTime(timezone=True), nullable=False),
        sa.Column("etd", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atd", sa.DateTime(timezone=True)),
        sa.Column("ata", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), server_default="planned", nullable=False),
        sa.Column("is_bookable", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("public_capacity_palettes", sa.Integer),
        sa.Column("public_price_per_palette_eur", sa.Numeric(8, 2)),
        sa.Column("booking_open_at", sa.DateTime(timezone=True)),
        sa.Column("booking_close_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legs_etd", "legs", ["etd"])
    op.create_index("ix_legs_status", "legs", ["status"])
    op.create_index("ix_legs_bookable", "legs", ["is_bookable"])
    op.create_index("ix_legs_vessel", "legs", ["vessel_id"])

    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("reference", sa.String(20), unique=True, nullable=False),
        sa.Column("client_account_id", sa.Integer, sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id"), nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("total_palettes", sa.Integer, server_default="0", nullable=False),
        sa.Column("total_weight_kg", sa.Numeric(10, 2), server_default="0", nullable=False),
        sa.Column("total_cubage_m3", sa.Numeric(10, 3), server_default="0", nullable=False),
        sa.Column("hazardous", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("oversize", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("estimated_price_eur", sa.Numeric(10, 2)),
        sa.Column("confirmed_price_eur", sa.Numeric(10, 2)),
        sa.Column("pickup_address", sa.Text),
        sa.Column("delivery_address", sa.Text),
        sa.Column("shipper_reference", sa.String(100)),
        sa.Column("notes", sa.Text),
        sa.Column("signed_terms_version", sa.String(20)),
        sa.Column("signed_terms_at", sa.DateTime(timezone=True)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_bookings_status", "bookings", ["status"])
    op.create_index("ix_bookings_client_status", "bookings", ["client_account_id", "status"])
    op.create_index("ix_bookings_leg", "bookings", ["leg_id"])

    op.create_table(
        "booking_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("booking_id", sa.Integer, sa.ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pallet_format", sa.String(20), nullable=False),
        sa.Column("pallet_count", sa.Integer, nullable=False),
        sa.Column("cargo_description", sa.String(500), nullable=False),
        sa.Column("unit_weight_kg", sa.Numeric(10, 2)),
        sa.Column("total_weight_kg", sa.Numeric(10, 2)),
        sa.Column("stackable", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("hazardous", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("imdg_class", sa.String(20)),
        sa.Column("un_number", sa.String(10)),
        sa.Column("hs_code", sa.String(20)),
        sa.Column("temperature_min", sa.Integer),
        sa.Column("temperature_max", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_booking_items_booking", "booking_items", ["booking_id"])

    op.create_table(
        "client_invoices",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("reference", sa.String(30), unique=True, nullable=False),
        sa.Column("booking_id", sa.Integer, sa.ForeignKey("bookings.id")),
        sa.Column("client_account_id", sa.Integer, sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("amount_excl_vat_eur", sa.Numeric(10, 2), nullable=False),
        sa.Column("vat_amount_eur", sa.Numeric(10, 2), nullable=False),
        sa.Column("amount_incl_vat_eur", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.CHAR(3), server_default="EUR", nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("stripe_payment_intent_id", sa.String(100)),
        sa.Column("pdf_url", sa.String(500)),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_invoices_status", "client_invoices", ["status"])
    op.create_index("ix_invoices_client", "client_invoices", ["client_account_id"])

    op.create_table(
        "co2_certificates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("reference", sa.String(30), unique=True, nullable=False),
        sa.Column("booking_id", sa.Integer, sa.ForeignKey("bookings.id")),
        sa.Column("client_account_id", sa.Integer, sa.ForeignKey("client_accounts.id"), nullable=False),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id")),
        sa.Column("tonnage_transported_t", sa.Numeric(8, 3), nullable=False),
        sa.Column("distance_nm", sa.Numeric(8, 2), nullable=False),
        sa.Column("co2_emitted_kg", sa.Numeric(10, 3), nullable=False),
        sa.Column("co2_conventional_kg", sa.Numeric(10, 3), nullable=False),
        sa.Column("co2_avoided_kg", sa.Numeric(10, 3), nullable=False),
        sa.Column("pdf_url", sa.String(500)),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_certificates_client", "co2_certificates", ["client_account_id"])

    op.create_table(
        "feature_flags",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("enabled", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("rollout_pct", sa.Integer, server_default="0", nullable=False),
        sa.Column("audience", sa.JSON, server_default="{}", nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("updated_by_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "rate_limit_attempts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column("identifier", sa.String(255), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_rate_limit_scope_id_at", "rate_limit_attempts", ["scope", "identifier", "attempted_at"])

    op.create_table(
        "activity_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer),
        sa.Column("user_name", sa.String(200)),
        sa.Column("user_role", sa.String(40)),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("module", sa.String(40)),
        sa.Column("entity_type", sa.String(60)),
        sa.Column("entity_id", sa.Integer),
        sa.Column("entity_label", sa.String(200)),
        sa.Column("detail", sa.Text),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )


def downgrade() -> None:
    op.drop_table("activity_logs")
    op.drop_table("rate_limit_attempts")
    op.drop_table("feature_flags")
    op.drop_table("co2_certificates")
    op.drop_table("client_invoices")
    op.drop_table("booking_items")
    op.drop_table("bookings")
    op.drop_table("legs")
    op.drop_table("ports")
    op.drop_table("vessels")
    op.drop_table("client_accounts")
    op.drop_table("users")
