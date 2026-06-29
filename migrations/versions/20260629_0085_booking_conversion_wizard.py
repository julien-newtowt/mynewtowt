"""Wizard de conversion : autocréation compte, analytics, relance J+1, annulation.

Adds:
- ``analytics_events`` : table d'instrumentation du tunnel (CONV-06).
- ``bookings.client_account_id`` rendu NULLABLE (brouillon invité avant
  autocréation du compte à la validation).
- ``bookings.source_quote_reference`` : devis à l'origine de la réservation.
- ``bookings.cancellation_fee_eur`` : frais d'annulation (grille COM-08).
- ``quotes.followup_sent_at`` : relance J+1 sur devis non converti.

Revision ID: 20260629_0085
Revises: 20260629_0084
Create Date: 2026-06-29 00:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260629_0085"
down_revision = "20260629_0084"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analytics_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event", sa.String(length=40), nullable=False),
        sa.Column("reference", sa.String(length=40), nullable=True),
        sa.Column("lang", sa.String(length=5), nullable=True),
        sa.Column("channel", sa.String(length=20), nullable=True),
        sa.Column("detail", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_analytics_events_event", "analytics_events", ["event"])
    op.create_index(
        "ix_analytics_events_event_created",
        "analytics_events",
        ["event", "created_at"],
    )

    # client_account_id : NOT NULL → NULLABLE (brouillon invité).
    op.alter_column(
        "bookings",
        "client_account_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "bookings",
        sa.Column("source_quote_reference", sa.String(length=24), nullable=True),
    )
    op.create_index(
        "ix_bookings_source_quote_reference",
        "bookings",
        ["source_quote_reference"],
    )
    op.add_column(
        "bookings",
        sa.Column("cancellation_fee_eur", sa.Numeric(10, 2), nullable=True),
    )

    op.add_column(
        "quotes",
        sa.Column("followup_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("quotes", "followup_sent_at")
    op.drop_column("bookings", "cancellation_fee_eur")
    op.drop_index("ix_bookings_source_quote_reference", table_name="bookings")
    op.drop_column("bookings", "source_quote_reference")
    op.alter_column(
        "bookings",
        "client_account_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_index("ix_analytics_events_event_created", table_name="analytics_events")
    op.drop_index("ix_analytics_events_event", table_name="analytics_events")
    op.drop_table("analytics_events")
