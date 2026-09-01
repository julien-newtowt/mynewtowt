"""Workflow du Bill of Lading — machine à états, validation, signature, révisions.

Ajoute sur ``packing_list_batches`` les colonnes du cycle
``draft`` → ``client_validated`` → ``master_signed`` → ``final``, décrit dans
``docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md``.

Rappel du besoin métier : l'émission actuelle est un ``GET`` non tracé produisant
un document **mutable** présenté comme définitif (mention « 3 OBL signés » même
sur un document non signé). Le draft explicite lève ce défaut, et le **point de
gel se déplace de l'émission à la signature du commandant** — avant signature un
connaissement n'engage personne, après il engage le transporteur.

Deux contraintes sont posées **en base**, pas seulement dans les formulaires :

- ``ck_bl_validator_client_xor_staff`` — le validateur du draft est **soit** le
  client titulaire du booking, **soit** un membre du staff agissant pour son
  compte (repli quand ``bookings.client_account_id`` est NULL), jamais les deux.
  C'est ce qui interdit qu'une validation du staff soit présentée comme venant du
  client. Les deux NULL restent permis : aucune validation encore intervenue.
- ``ck_bl_revision_positive`` — une révision commence à 1 et ne décroît jamais.

``bl_number`` et ``bl_issued_at``, préexistants, sont **conservés** : le numéro est
attribué à la génération du draft et ne bouge plus ensuite.

Réversible : ``downgrade`` retire les contraintes puis les colonnes, sans toucher
aux données préexistantes.

Revision ID: 20260814_0114
Revises: 20260807_0113
Create Date: 2026-08-14 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260814_0114"
down_revision = "20260807_0113"
branch_labels = None
depends_on = None


# (nom, type) des colonnes ajoutées — sert aussi au downgrade, pour éviter
# d'énumérer la liste deux fois et de la laisser dériver.
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("bl_state", sa.String(20)),
    ("bl_draft_at", sa.DateTime(timezone=True)),
    ("bl_issued_by_id", sa.Integer()),
    ("bl_issued_by_name", sa.String(200)),
    ("bl_client_validated_at", sa.DateTime(timezone=True)),
    ("bl_client_validated_by_id", sa.Integer()),
    ("bl_validated_on_behalf_by_id", sa.Integer()),
    ("bl_client_validated_by", sa.String(200)),
    ("bl_signed_at", sa.DateTime(timezone=True)),
    ("bl_signed_by_id", sa.Integer()),
    ("bl_signed_by_name", sa.String(200)),
    ("bl_signature_hash", sa.String(64)),
    ("bl_superseded_by_id", sa.Integer()),
)


def upgrade():
    for name, type_ in _COLUMNS:
        op.add_column("packing_list_batches", sa.Column(name, type_, nullable=True))

    # `bl_revision` est NOT NULL : un server_default est indispensable pour que
    # les lignes existantes soient valides au moment de l'ALTER.
    op.add_column(
        "packing_list_batches",
        sa.Column("bl_revision", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_index("ix_packing_list_batches_bl_state", "packing_list_batches", ["bl_state"])

    op.create_foreign_key(
        "fk_plb_bl_issued_by", "packing_list_batches", "users", ["bl_issued_by_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_plb_bl_validated_client",
        "packing_list_batches",
        "client_accounts",
        ["bl_client_validated_by_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_plb_bl_validated_on_behalf",
        "packing_list_batches",
        "users",
        ["bl_validated_on_behalf_by_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_plb_bl_signed_by", "packing_list_batches", "users", ["bl_signed_by_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_plb_bl_superseded_by",
        "packing_list_batches",
        "packing_list_batches",
        ["bl_superseded_by_id"],
        ["id"],
    )

    op.create_check_constraint(
        "ck_bl_validator_client_xor_staff",
        "packing_list_batches",
        "bl_client_validated_by_id IS NULL OR bl_validated_on_behalf_by_id IS NULL",
    )
    op.create_check_constraint(
        "ck_bl_revision_positive", "packing_list_batches", "bl_revision >= 1"
    )


def downgrade():
    op.drop_constraint("ck_bl_revision_positive", "packing_list_batches", type_="check")
    op.drop_constraint("ck_bl_validator_client_xor_staff", "packing_list_batches", type_="check")

    for fk in (
        "fk_plb_bl_superseded_by",
        "fk_plb_bl_signed_by",
        "fk_plb_bl_validated_on_behalf",
        "fk_plb_bl_validated_client",
        "fk_plb_bl_issued_by",
    ):
        op.drop_constraint(fk, "packing_list_batches", type_="foreignkey")

    op.drop_index("ix_packing_list_batches_bl_state", table_name="packing_list_batches")

    op.drop_column("packing_list_batches", "bl_revision")
    for name, _type in reversed(_COLUMNS):
        op.drop_column("packing_list_batches", name)
