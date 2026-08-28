"""Gel de la caisse à la relève — rattachement du mouvement à l'état qui l'a figé.

ADR-013, décision 4 : à la déclaration d'un état de caisse de motif **fin
d'embarquement**, la comptabilité du commandant débarquant est figée. Une relève
est une décharge : si l'entrant — ou le sortant — peut encore écrire dans la
période remise, la décharge ne vaut rien et l'écart redevient inimputable.

Deux mécanismes verrouillent désormais le même champ ``locked_at`` : la clôture
mensuelle arrête un **mois comptable**, la relève arrête la **responsabilité
d'une personne**. Cette colonne garde la référence de celui qui a gelé, pour
qu'on sache toujours au titre de quoi un mouvement est en lecture seule.

Aucune reprise de données : les mouvements existants ne sont pas gelés
rétroactivement. Le premier état de fin d'embarquement déclaré après la mise en
service figera ce qui le précède — ce qui est bien le comportement attendu.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260827_0130"
down_revision = "20260827_0129"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cashbox_movements", sa.Column("cash_count_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_cashbox_movements_cash_count",
        "cashbox_movements",
        "cash_counts",
        ["cash_count_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_cashbox_movements_cash_count_id", "cashbox_movements", ["cash_count_id"])


def downgrade():
    op.drop_index("ix_cashbox_movements_cash_count_id", table_name="cashbox_movements")
    op.drop_constraint("fk_cashbox_movements_cash_count", "cashbox_movements", type_="foreignkey")
    op.drop_column("cashbox_movements", "cash_count_id")
