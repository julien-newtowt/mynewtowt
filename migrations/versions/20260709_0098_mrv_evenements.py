"""MRV lot 3 — modèle événementiel déclaratif (capture) + relevés.

Couche 2 « Capture événementielle » de l'architecture déclarative MRV
(plan §2.2). *Joined-table inheritance* :

- ``nav_events`` (table mère, discriminant ``event_type``) ;
- ``nav_event_noon`` / ``nav_event_portcall`` / ``nav_event_anchoring``
  (3 tables filles ; Departure+Arrival partagent portcall, Begin+End
  partagent anchoring) ;
- 4 tables de relevés : ``nav_event_engine_readings`` (tout type d'événement)
  + weather/sail/hold (rattachés au NoonEvent).

Aucun seed : les référentiels navire (cuves/moteurs) et les événements sont
créés par les services (lot 1) / la saisie bord (lot 4).

Revision ID: 20260709_0098
Revises: 20260709_0097
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260709_0098"
down_revision = "20260709_0097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Table mère nav_events ───────────────────────────────────────
    op.create_table(
        "nav_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "leg_id",
            sa.Integer(),
            sa.ForeignKey("legs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "vessel_id",
            sa.Integer(),
            sa.ForeignKey("vessels.id"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("datetime_local", sa.DateTime(timezone=False), nullable=True),
        sa.Column("timezone", sa.String(length=40), nullable=True),
        sa.Column("datetime_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lat_decimal", sa.Numeric(9, 6), nullable=True),
        sa.Column("lon_decimal", sa.Numeric(9, 6), nullable=True),
        sa.Column("position_source", sa.String(length=20), nullable=True),
        sa.Column("position_justification", sa.Text(), nullable=True),
        sa.Column("cargo_mrv_t", sa.Numeric(12, 3), nullable=True),
        sa.Column(
            "status",
            sa.String(length=12),
            nullable=False,
            server_default="brouillon",
        ),
        sa.Column(
            "author_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "validated_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("client_uuid", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "leg_id", "event_type", "datetime_utc", name="uq_nav_event_leg_type_dt"
        ),
        sa.UniqueConstraint("client_uuid", name="uq_nav_events_client_uuid"),
    )
    op.create_index("ix_nav_events_vessel_dt", "nav_events", ["vessel_id", "datetime_utc"])
    op.create_index("ix_nav_events_leg", "nav_events", ["leg_id"])
    op.create_index("ix_nav_events_status", "nav_events", ["status"])
    op.create_index("ix_nav_events_author", "nav_events", ["author_user_id"])

    # ── 2. Fille NoonEvent ─────────────────────────────────────────────
    op.create_table(
        "nav_event_noon",
        sa.Column(
            "id",
            sa.Integer(),
            sa.ForeignKey("nav_events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("time_from_sosp_h", sa.Numeric(12, 3), nullable=True),
        sa.Column("distance_from_sosp_nm", sa.Numeric(12, 3), nullable=True),
        sa.Column("distance_to_go_nm", sa.Numeric(12, 3), nullable=True),
        sa.Column("announced_eta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("etb", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eta_7_to_10kt", sa.JSON(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
    )

    # ── 3. Fille PortCallEvent (Departure/Arrival) ─────────────────────
    op.create_table(
        "nav_event_portcall",
        sa.Column(
            "id",
            sa.Integer(),
            sa.ForeignKey("nav_events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("draft_fwd_m", sa.Numeric(8, 3), nullable=True),
        sa.Column("draft_aft_m", sa.Numeric(8, 3), nullable=True),
        sa.Column("trim_m", sa.Numeric(8, 3), nullable=True),
        sa.Column("vessel_condition", sa.String(length=20), nullable=True),
        sa.Column("rob_t", sa.Numeric(12, 3), nullable=True),
        sa.Column("cargo_bl_t", sa.Numeric(12, 3), nullable=True),
        sa.Column("etd_confirmed", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eta_announced", sa.DateTime(timezone=True), nullable=True),
        sa.Column("etb", sa.DateTime(timezone=True), nullable=True),
    )

    # ── 4. Fille AnchoringEvent (Begin/End) ────────────────────────────
    op.create_table(
        "nav_event_anchoring",
        sa.Column(
            "id",
            sa.Integer(),
            sa.ForeignKey("nav_events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("sequence_no", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "paired_event_id",
            sa.Integer(),
            sa.ForeignKey("nav_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("duration_h", sa.Numeric(12, 3), nullable=True),
    )

    # ── 5. Relevés compteurs moteur (tout type d'événement) ────────────
    op.create_table(
        "nav_event_engine_readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("nav_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "engine_id",
            sa.Integer(),
            sa.ForeignKey("vessel_engines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("running_hours_counter_h", sa.Numeric(12, 3), nullable=True),
        sa.Column("fuel_counter_l", sa.Numeric(14, 3), nullable=True),
        sa.Column(
            "is_counter_reset",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "reset_confirmed_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reset_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_nav_engine_readings_event", "nav_event_engine_readings", ["event_id"])
    op.create_index("ix_nav_engine_readings_engine", "nav_event_engine_readings", ["engine_id"])

    # ── 6. Relevés météo (NoonEvent) ───────────────────────────────────
    op.create_table(
        "nav_event_weather_readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("nav_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot_time", sa.String(length=5), nullable=True),
        sa.Column("tws_kn", sa.Numeric(6, 2), nullable=True),
        sa.Column("awa_deg", sa.Numeric(6, 2), nullable=True),
        sa.Column("aws_kn", sa.Numeric(6, 2), nullable=True),
        sa.Column("sea_state", sa.Integer(), nullable=True),
        sa.Column("sea_direction_deg", sa.Numeric(6, 2), nullable=True),
        sa.Column("ship_speed_kn", sa.Numeric(6, 2), nullable=True),
    )
    op.create_index("ix_nav_weather_readings_event", "nav_event_weather_readings", ["event_id"])

    # ── 7. Relevés voilure (NoonEvent) ─────────────────────────────────
    op.create_table(
        "nav_event_sail_readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("nav_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot_time", sa.String(length=5), nullable=True),
        sa.Column("j0", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fwd_j1", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fwd_ms", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("aft_j1", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("aft_ms", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sail_boost_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("me_ps_load_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("me_sb_load_pct", sa.Numeric(6, 2), nullable=True),
    )
    op.create_index("ix_nav_sail_readings_event", "nav_event_sail_readings", ["event_id"])

    # ── 8. Relevés cales (NoonEvent, 9 zones) ──────────────────────────
    op.create_table(
        "nav_event_hold_readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("nav_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period", sa.String(length=10), nullable=True),
        sa.Column("zone", sa.String(length=20), nullable=True),
        sa.Column("temp_c", sa.Numeric(6, 2), nullable=True),
        sa.Column("rh_pct", sa.Numeric(6, 2), nullable=True),
    )
    op.create_index("ix_nav_hold_readings_event", "nav_event_hold_readings", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_nav_hold_readings_event", table_name="nav_event_hold_readings")
    op.drop_table("nav_event_hold_readings")

    op.drop_index("ix_nav_sail_readings_event", table_name="nav_event_sail_readings")
    op.drop_table("nav_event_sail_readings")

    op.drop_index("ix_nav_weather_readings_event", table_name="nav_event_weather_readings")
    op.drop_table("nav_event_weather_readings")

    op.drop_index("ix_nav_engine_readings_engine", table_name="nav_event_engine_readings")
    op.drop_index("ix_nav_engine_readings_event", table_name="nav_event_engine_readings")
    op.drop_table("nav_event_engine_readings")

    op.drop_table("nav_event_anchoring")
    op.drop_table("nav_event_portcall")
    op.drop_table("nav_event_noon")

    op.drop_index("ix_nav_events_author", table_name="nav_events")
    op.drop_index("ix_nav_events_status", table_name="nav_events")
    op.drop_index("ix_nav_events_leg", table_name="nav_events")
    op.drop_index("ix_nav_events_vessel_dt", table_name="nav_events")
    op.drop_table("nav_events")
