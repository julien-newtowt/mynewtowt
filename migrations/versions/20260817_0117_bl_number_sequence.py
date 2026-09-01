"""Séquence de numéros de connaissement — non recyclable, par voyage.

Cf. ``docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md`` §4.4 :

    « Séquence de numéros non recyclable : remplacer le comptage par une séquence
    append-only, pour qu'un numéro consommé ne puisse **jamais** être réattribué
    même après suppression d'une ligne. »

## Le défaut corrigé

Le numéro était calculé comme *nombre de BL déjà émis sur le leg + 1*. Deux
conséquences, toutes deux graves sur un registre opposable :

1. **recyclage** — supprimer un lot faisait baisser le compteur, et le numéro suivant
   réattribuait un numéro **déjà consommé** ;
2. **blocage** — si le lot supprimé n'était pas le dernier (001, 002, 003 avec 002
   supprimé), le compteur valait 2, le code retentait 003, entrait en collision avec
   la contrainte d'unicité et **échouait après 5 tentatives**. L'émission devenait
   impossible sur ce voyage.

## L'amorçage, qui est le point délicat

Les lignes sont créées **à la demande** par le service, et amorcées sur le **plus
grand suffixe déjà émis** pour le voyage — jamais sur leur nombre. Amorcer sur le
nombre recyclerait dès la première émission sur un voyage historique.

Cette migration ne fait donc **aucun backfill** : il n'y a rien à pré-remplir, et
un backfill fondé sur un comptage aurait réintroduit le défaut qu'on corrige.

``ck_bl_sequence_non_negative`` interdit un compteur négatif, qui signalerait une
décrémentation — précisément ce que cette table exclut.

Réversible : ``downgrade`` supprime la table. Les numéros déjà attribués vivent dans
``packing_list_batches.bl_number`` et ne sont pas touchés.

Revision ID: 20260817_0117
Revises: 20260817_0116
Create Date: 2026-08-17 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260817_0117"
down_revision = "20260817_0116"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bl_number_sequences",
        sa.Column(
            "leg_id",
            sa.Integer(),
            sa.ForeignKey("legs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("last_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("last_seq >= 0", name="ck_bl_sequence_non_negative"),
    )


def downgrade():
    op.drop_table("bl_number_sequences")
