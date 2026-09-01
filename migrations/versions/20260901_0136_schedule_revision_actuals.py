"""Historisation du RÉEL dans ``schedule_revisions`` (séquence départ/arrivée).

La refonte de la séquence de planification (déclaration « départ du POL » /
« arrivée au POD ») historise désormais TOUS les mouvements de dates, réel
compris : pose et correction d'ATD/ATA, avec l'éventuel re-ancrage d'ETA qui
en découle. Quatre colonnes nullable rejoignent la table (NULL sur les
révisions purement prévisionnelles) ; deux nouvelles valeurs de ``source``
apparaissent côté applicatif : ``departure_declared`` / ``arrival_declared``
(la colonne ``source`` est un VARCHAR(20) libre — pas de contrainte à migrer).
"""

import sqlalchemy as sa
from alembic import op

revision = "20260901_0136"
down_revision = "20260828_0135"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "schedule_revisions", sa.Column("old_atd", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "schedule_revisions", sa.Column("new_atd", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "schedule_revisions", sa.Column("old_ata", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "schedule_revisions", sa.Column("new_ata", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade():
    op.drop_column("schedule_revisions", "new_ata")
    op.drop_column("schedule_revisions", "old_ata")
    op.drop_column("schedule_revisions", "new_atd")
    op.drop_column("schedule_revisions", "old_atd")
