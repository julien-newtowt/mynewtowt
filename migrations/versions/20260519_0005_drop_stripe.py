"""Drop stripe_payment_intent_id from client_invoices.

NEWTOWT ne traite plus de paiement en ligne en V3.1 — la facturation se
fait par virement bancaire après confirmation commerciale (sous 4h).

Revision ID: 20260519_0005
Revises: 20260519_0004
Create Date: 2026-05-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260519_0005"
down_revision = "20260519_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("client_invoices") as batch:
        batch.drop_column("stripe_payment_intent_id")


def downgrade() -> None:
    with op.batch_alter_table("client_invoices") as batch:
        batch.add_column(sa.Column("stripe_payment_intent_id", sa.String(100)))
