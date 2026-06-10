"""tickets + ticket_comments

Revision ID: 20260518_0004
Revises: 20260518_0003
Create Date: 2026-05-18 23:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260518_0004"
down_revision: Union[str, None] = "20260518_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("reference", sa.String(20), unique=True, nullable=False),
        sa.Column("leg_id", sa.Integer, sa.ForeignKey("legs.id")),
        sa.Column("port_id", sa.Integer, sa.ForeignKey("ports.id")),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("priority", sa.String(4), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), server_default="open", nullable=False),
        sa.Column("created_by_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("assigned_to_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("external_contact", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_reason", sa.Text),
        sa.Column("sla_target_at", sa.DateTime(timezone=True)),
        sa.Column("sla_breached", sa.Boolean, server_default=sa.false(), nullable=False),
    )
    op.create_index("ix_tickets_status", "tickets", ["status"])
    op.create_index("ix_tickets_priority_status", "tickets", ["priority", "status"])
    op.create_index("ix_tickets_assigned", "tickets", ["assigned_to_id"])
    op.create_index("ix_tickets_leg", "tickets", ["leg_id"])

    op.create_table(
        "ticket_comments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("author_name", sa.String(200)),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("is_internal", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ticket_comments_ticket", "ticket_comments", ["ticket_id"])


def downgrade() -> None:
    op.drop_index("ix_ticket_comments_ticket", table_name="ticket_comments")
    op.drop_table("ticket_comments")
    op.drop_index("ix_tickets_leg", table_name="tickets")
    op.drop_index("ix_tickets_assigned", table_name="tickets")
    op.drop_index("ix_tickets_priority_status", table_name="tickets")
    op.drop_index("ix_tickets_status", table_name="tickets")
    op.drop_table("tickets")
