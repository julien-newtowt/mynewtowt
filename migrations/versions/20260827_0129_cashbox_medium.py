"""Caisse de bord — distinguer les espèces des encaissements par carte (ADR-011).

``settle_sale`` créditait la caisse quel que soit le moyen de paiement. Or
``OnboardCashbox`` décrit l'argent **physique** détenu à bord, et la clôture
compare son solde au **comptage des billets** saisi par le commandant. Les
règlements carte — encaissés chez le prestataire puis en banque, jamais dans le
coffre — gonflaient donc le solde théorique : la variance archivée était fausse
du montant des ventes CB, **chaque mois**, et une perte d'espèces réelle s'y
noyait sans être détectable.

Aggravant : les deux natures partageaient la même catégorie ``vente_a_bord``,
donc l'écart n'était même pas rattrapable a posteriori par filtrage.

La colonne ``medium`` sépare les deux. Les mouvements ``card`` restent au
journal et à l'export — le rapprochement bancaire se fait dans le logiciel
comptable, à partir de l'export mensuel et de l'extrait bancaire (ADR-011) —
mais sortent du solde théorique et de l'écart de comptage.

Reprise des données : ``cash`` par défaut, puis ``card`` pour les mouvements
rattachés à une vente réglée par carte. C'est exactement la distinction que
faisait déjà la donnée, on ne fait que la nommer.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260827_0129"
down_revision = "20260827_0128"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "cashbox_movements",
        sa.Column("medium", sa.String(length=4), nullable=False, server_default="cash"),
    )
    op.create_index("ix_cashbox_movements_medium", "cashbox_movements", ["medium"])

    # Reprise : les mouvements de caisse issus d'une vente réglée par carte.
    op.execute(
        sa.text(
            "UPDATE cashbox_movements SET medium = 'card' WHERE id IN ("
            "  SELECT cashbox_movement_id FROM onboard_sales"
            "  WHERE cashbox_movement_id IS NOT NULL AND payment_method = 'card'"
            ")"
        )
    )
    op.create_check_constraint(
        "ck_cashbox_mov_medium", "cashbox_movements", "medium IN ('cash', 'card')"
    )


def downgrade():
    op.drop_constraint("ck_cashbox_mov_medium", "cashbox_movements", type_="check")
    op.drop_index("ix_cashbox_movements_medium", table_name="cashbox_movements")
    op.drop_column("cashbox_movements", "medium")
