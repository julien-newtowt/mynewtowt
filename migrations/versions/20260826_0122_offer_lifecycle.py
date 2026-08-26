"""Offres commerciales — cycle de vie métier (en cours / validée / échue / annulée).

Lot 4 de la refonte commerciale.

Le cycle précédent (``draft/sent/accepted/declined/expired``) était en partie
fictif : ``declined`` et ``expired`` n'étaient **assignés par aucune route** —
aucun bouton n'annulait une offre, aucun traitement ne la faisait échoir. Une
offre restait donc « envoyée » indéfiniment, y compris après le départ du navire.

Le nouveau cycle est celui du métier :

* ``en_cours`` — proposée, réserve du volume prévisionnel sur le leg ;
* ``valide``   — acceptée, déclenche l'établissement de la booking note ;
* ``echue``    — validité dépassée **ou** navire parti (ATD) ;
* ``annule``   — retirée sur décision du commercial.

Correspondance appliquée aux offres existantes :

    draft, sent → en_cours     (proposition encore ouverte)
    accepted    → valide
    declined    → annule
    expired     → echue

``draft`` et ``sent`` fusionnent : la distinction n'était pas exploitée (aucun
envoi réel n'existait, seul le statut changeait) et la règle métier ne prévoit
pas d'état intermédiaire avant « en cours ».

⚠️ ``grid_id`` et ``leg_id`` **restent nullables** malgré la règle « une seule
grille et un seul leg, obligatoires ». Les rendre NOT NULL exigerait d'inventer
une grille ou un voyage pour les offres antérieures. La contrainte est appliquée
à la création côté application ; ``is_legacy`` marque ici les offres historiques
incomplètes pour qu'on ne les prenne pas pour une saisie bâclée.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260826_0122"
down_revision = "20260826_0121"
branch_labels = None
depends_on = None

_FORWARD = {
    "draft": "en_cours",
    "sent": "en_cours",
    "accepted": "valide",
    "declined": "annule",
    "expired": "echue",
}
# La descente ne peut pas restaurer la distinction draft/sent (perdue à la
# montée) : tout ``en_cours`` redevient ``sent``, l'état le plus proche.
_BACKWARD = {
    "en_cours": "sent",
    "valide": "accepted",
    "annule": "declined",
    "echue": "expired",
}


def upgrade():
    with op.batch_alter_table("rate_offers") as batch:
        batch.add_column(sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("cancelled_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("is_legacy", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
    op.create_index("ix_rate_offers_status", "rate_offers", ["status"])
    op.create_index("ix_rate_offers_leg_id", "rate_offers", ["leg_id"])

    conn = op.get_bind()
    for old, new in _FORWARD.items():
        conn.execute(
            sa.text("UPDATE rate_offers SET status = :new WHERE status = :old"),
            {"new": new, "old": old},
        )

    # Horodatages du nouveau cycle, repris de ceux de l'ancien quand ils existent.
    conn.execute(
        sa.text(
            "UPDATE rate_offers SET validated_at = accepted_at "
            "WHERE status = 'valide' AND accepted_at IS NOT NULL"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE rate_offers SET cancelled_at = declined_at "
            "WHERE status = 'annule' AND declined_at IS NOT NULL"
        )
    )

    # Marque les offres antérieures à la règle « 1 grille + 1 leg ».
    conn.execute(
        sa.text(
            "UPDATE rate_offers SET is_legacy = true "
            "WHERE grid_id IS NULL OR leg_id IS NULL"
        )
    )


def downgrade():
    conn = op.get_bind()
    for new, old in _BACKWARD.items():
        conn.execute(
            sa.text("UPDATE rate_offers SET status = :old WHERE status = :new"),
            {"old": old, "new": new},
        )

    op.drop_index("ix_rate_offers_leg_id", table_name="rate_offers")
    op.drop_index("ix_rate_offers_status", table_name="rate_offers")
    with op.batch_alter_table("rate_offers") as batch:
        batch.drop_column("updated_at")
        batch.drop_column("is_legacy")
        batch.drop_column("expired_at")
        batch.drop_column("cancelled_reason")
        batch.drop_column("cancelled_at")
        batch.drop_column("validated_at")
