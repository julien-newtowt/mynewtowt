"""Révisions de connaissement — instantané du document annulé.

Cf. ``docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md`` §4.1 :

    « À partir de ``master_signed``, la correction ne passe plus par l'édition mais par
    une **révision numérotée** (``TUAW_…_R2``) qui annule explicitement la précédente,
    les deux restant tracées. »

## ⚠️ Écart assumé par rapport au §4.2 de la spec

Le §4.2 plaçait ``bl_superseded_by_id`` en clé étrangère vers
``packing_list_batches`` — ce qui suppose qu'une révision **crée un nouveau lot**.
Vérification faite dans le code, ce modèle **corromprait tous les agrégats** : le lot
porte la marchandise, et ``pdf_generator`` somme ``pallet_count`` / ``weight_kg`` sur
``pl.batches`` (packing list PDF, avis d'arrivée), l'export Excel les liste tous, le
stowage les localise, le ratio de complétude les compte. Un lot cloné par révision
**doublerait** chacun de ces totaux, et il aurait fallu filtrer « non périmé » dans
**chaque** agrégat — avec double comptage silencieux au premier oubli.

D'où l'inversion : **le lot reste unique**, c'est le **document** qui est versionné
dans ``bl_revisions``. Les agrégats existants restent justes sans être touchés.

Conséquence : ``packing_list_batches.bl_superseded_by_id`` **n'a plus d'objet** et est
retirée. Elle avait été ajoutée par ``20260814_0114`` (même branche, non fusionnée) et
**aucun code ne la lit** : la laisser en place serait exactement le piège d'une colonne
qui a l'air de vouloir dire quelque chose. ``bl_revision`` est conservée — c'est le
numéro de révision courant du lot.

``reason`` est NOT NULL : réviser un titre opposable sans dire pourquoi n'aurait aucune
valeur probante. ``signed_content`` conserve la sérialisation canonique de ce qui avait
été signé — sans elle, on saurait qu'un document a existé sans savoir ce qu'il disait.

Réversible : ``downgrade`` restaure la colonne (sans données, elles n'existaient pas)
et supprime la table.

Revision ID: 20260817_0118
Revises: 20260817_0117
Create Date: 2026-08-17 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260817_0118"
down_revision = "20260817_0117"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bl_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("packing_list_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("bl_number", sa.String(50), nullable=False),
        sa.Column("signature_hash", sa.String(64), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_by_name", sa.String(200), nullable=True),
        sa.Column("signed_content", sa.Text(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "superseded_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("superseded_by_name", sa.String(200), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("batch_id", "revision", name="uq_bl_revision_batch_revision"),
        sa.CheckConstraint("revision >= 1", name="ck_bl_revision_snapshot_positive"),
    )
    op.create_index("ix_bl_revisions_batch_id", "bl_revisions", ["batch_id"])

    # Colonne sans objet dans le modèle retenu, et jamais lue par le code.
    op.drop_constraint("fk_plb_bl_superseded_by", "packing_list_batches", type_="foreignkey")
    op.drop_column("packing_list_batches", "bl_superseded_by_id")


def downgrade():
    op.add_column(
        "packing_list_batches", sa.Column("bl_superseded_by_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_plb_bl_superseded_by",
        "packing_list_batches",
        "packing_list_batches",
        ["bl_superseded_by_id"],
        ["id"],
    )
    op.drop_index("ix_bl_revisions_batch_id", table_name="bl_revisions")
    op.drop_table("bl_revisions")
