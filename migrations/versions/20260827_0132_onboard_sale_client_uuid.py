"""Vente rapide hors connexion — clé d'idempotence générée par le client.

Encaisser en mer supposait de rejouer une saisie au retour du réseau. Le
parcours écran par écran ne s'y prête pas : il enchaîne trois requêtes
dépendantes (créer la vente, ajouter chaque ligne, encaisser), la deuxième
ayant besoin de la référence renvoyée par la première. Une opération atomique,
elle, se rejoue telle quelle.

``client_uuid`` est généré par le navigateur avant la mise en file d'attente et
porte l'idempotence : un rejeu renvoie la vente déjà enregistrée au lieu d'en
créer une seconde — donc un second encaissement. La contrainte d'unicité est le
filet de dernier recours derrière le contrôle applicatif, comme
``cashbox_movement_id`` l'est pour le règlement.

Nullable : les ventes créées écran par écran n'en ont pas besoin, leur
idempotence tient au verrou de règlement.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260827_0132"
down_revision = "20260827_0131"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("onboard_sales", sa.Column("client_uuid", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_onboard_sale_client_uuid", "onboard_sales", ["client_uuid"])


def downgrade():
    op.drop_constraint("uq_onboard_sale_client_uuid", "onboard_sales", type_="unique")
    op.drop_column("onboard_sales", "client_uuid")
