"""Contrôle de caisse — état déclaré par le commandant, écarts historisés.

À chaque fin d'embarquement et chaque fin de mois, le commandant sortant
déclare l'état complet de sa caisse, **coupure par coupure** et par devise.

La clôture mensuelle existante ne connaissait qu'un solde compté global, saisi
d'un bloc : un total non détaillé n'est pas vérifiable, et son rythme mensuel
découvre un écart après le débarquement de celui qui tenait la caisse. Ces
tables donnent au cash un **détenteur nommé**, un moment de passation, et un
écart figé au moment du contrôle — avec le solde théorique de ce moment, pour
qu'un mouvement saisi après coup ne réécrive pas un contrôle déjà rendu.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260827_0128"
down_revision = "20260827_0127"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cash_counts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cashbox_id", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("counted_on", sa.Date(), nullable=False),
        sa.Column("declared_by_id", sa.Integer(), nullable=True),
        sa.Column("declared_by_name", sa.String(length=200), nullable=False),
        sa.Column("handover_to_name", sa.String(length=200), nullable=True),
        sa.Column("leg_id", sa.Integer(), nullable=True),
        sa.Column("closure_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="declare"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["cashbox_id"], ["onboard_cashboxes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["declared_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["leg_id"], ["legs.id"]),
        sa.ForeignKeyConstraint(["closure_id"], ["cashbox_closures.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "trigger IN ('fin_embarquement', 'fin_de_mois', 'controle')",
            name="ck_cash_counts_trigger",
        ),
        sa.CheckConstraint(
            "status IN ('declare', 'valide', 'conteste')", name="ck_cash_counts_status"
        ),
    )
    op.create_index("ix_cash_counts_cashbox_id", "cash_counts", ["cashbox_id"])
    op.create_index("ix_cash_counts_counted_on", "cash_counts", ["counted_on"])
    op.create_index("ix_cash_counts_status", "cash_counts", ["status"])
    op.create_index("ix_cash_counts_leg_id", "cash_counts", ["leg_id"])
    op.create_index("ix_cash_counts_closure_id", "cash_counts", ["closure_id"])
    op.create_index("ix_cash_counts_box_date", "cash_counts", ["cashbox_id", "counted_on"])

    op.create_table(
        "cash_count_currencies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cash_count_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("bulk_coins_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("counted_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("computed_balance", sa.Numeric(14, 2), nullable=False),
        sa.Column("variance", sa.Numeric(14, 2), nullable=False),
        sa.Column("variance_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["cash_count_id"], ["cash_counts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("cash_count_id", "currency", name="uq_cash_count_currency"),
        sa.CheckConstraint("bulk_coins_amount >= 0", name="ck_cash_count_bulk_non_negative"),
        sa.CheckConstraint("counted_total >= 0", name="ck_cash_count_total_non_negative"),
    )
    op.create_index(
        "ix_cash_count_currencies_cash_count_id", "cash_count_currencies", ["cash_count_id"]
    )

    op.create_table(
        "cash_count_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cash_count_currency_id", sa.Integer(), nullable=False),
        sa.Column("denomination", sa.Numeric(12, 2), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("line_total", sa.Numeric(14, 2), nullable=False),
        sa.ForeignKeyConstraint(
            ["cash_count_currency_id"], ["cash_count_currencies.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "cash_count_currency_id", "denomination", name="uq_cash_count_line_denomination"
        ),
        sa.CheckConstraint("quantity >= 0", name="ck_cash_count_line_qty_non_negative"),
        sa.CheckConstraint("denomination > 0", name="ck_cash_count_line_denomination_positive"),
        sa.CheckConstraint("kind IN ('billet', 'piece')", name="ck_cash_count_line_kind"),
    )
    op.create_index(
        "ix_cash_count_lines_currency_id", "cash_count_lines", ["cash_count_currency_id"]
    )


def downgrade():
    op.drop_table("cash_count_lines")
    op.drop_table("cash_count_currencies")
    op.drop_table("cash_counts")
