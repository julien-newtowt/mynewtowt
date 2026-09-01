"""Rectification d'un mouvement de caisse — par contre-écriture.

Dernier manque P0 du module : aucune route ne permettait de corriger un montant
mal saisi. Le grand livre n'a ni UPDATE ni DELETE, et c'est délibéré — une
écriture passée fait foi. Le contournement observé était une contre-écriture
manuelle rangée dans une catégorie fourre-tout, qui gonflait les totaux
d'entrées et de sorties sans dire ce qu'elle rectifiait.

``reverses_movement_id`` nomme explicitement le mouvement annulé. Unique : un
mouvement ne se rectifie qu'une fois, sans quoi deux contre-écritures
successives feraient dériver le solde. C'est le même patron que
``refund_cashbox_movement_id`` pour la vente.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260827_0133"
down_revision = "20260827_0132"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "cashbox_movements", sa.Column("reverses_movement_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_cashbox_movements_reverses",
        "cashbox_movements",
        "cashbox_movements",
        ["reverses_movement_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_cashbox_movement_reverses", "cashbox_movements", ["reverses_movement_id"]
    )


def downgrade():
    op.drop_constraint("uq_cashbox_movement_reverses", "cashbox_movements", type_="unique")
    op.drop_constraint("fk_cashbox_movements_reverses", "cashbox_movements", type_="foreignkey")
    op.drop_column("cashbox_movements", "reverses_movement_id")
