"""QHSE — réconciliation des ré-imports (D10 : import_batch_id, source_code).

Corrige le trou de schéma identifié dans le cahier des charges QHSE (D10,
``docs/strategy/PLAN_UPGRADE_PHASE2_2026-08.md``) : « aucune clé
``source_code``/``import_batch_id`` ⇒ import non réconciliable, dédoublonnage
impossible sans migration ». Confirmé en pratique : ``qhse_reports`` était
vide en production ET en dev — aucun import réel n'avait encore eu lieu — et
le routeur documentait lui-même la limite (« ré-importer le même fichier crée
de nouveaux rapports plutôt que de les mettre à jour »).

Le FMS (source de vérité, arbitrage D10 — MyTOWT est un **miroir en lecture**)
exporte périodiquement l'intégralité des signalements, y compris ceux déjà
vus lors d'un import précédent, avec leur workflow CAPA/évaluation mis à jour
entre-temps (``ClosedDate``/``CorrectiveActionFinishedDate`` remplis plus
tard). Sans réconciliation, chaque ré-export duplique tout le registre.

- ``qhse_import_batches`` — un enregistrement par appel à ``/qhse/import``
  (qui a importé, quand, depuis quel fichier, avec quels compteurs). Reprend
  le rôle documenté pour ``activity_logs`` dans le cahier des charges (« pas
  de table d'audit QHSE dédiée ») uniquement pour la partie *comptable*
  (compteurs interrogeables) ; ``activity_logs`` reste la trace narrative de
  l'action, inchangée.
- ``qhse_reports.import_batch_id`` — quel import a créé ou le plus récemment
  rafraîchi cette ligne. ``ON DELETE SET NULL`` : un lot n'est jamais un
  invariant dont dépendrait l'intégrité d'un rapport.
- ``qhse_reports.source_code`` — clé naturelle de réconciliation, hachage
  SHA-256 de ``(vessel_id, date d'émission, sujet normalisé, description
  normalisée)``. Index, **volontairement pas UNIQUE** : en l'absence d'un
  identifiant FMS stable dans l'export (aucune des 41 colonnes n'en porte
  un — cahier des charges §3.1/§16.1), c'est une clé du meilleur effort ; une
  collision plausible mais non prouvée doit dégrader en doublon détecté plus
  tard, jamais en échec bloquant d'import. Validée sur les 190 lignes réelles
  (Anemos + Artemis) : 190 clés distinctes, y compris le cas adverse documenté
  au §3.3 (trois signalements PSC Artemis, même navire, même date, même sujet
  générique « PSC », distingués uniquement par leur ``Description``).

Revision ID: 20260903_0141
Revises: 20260903_0140
Create Date: 2026-09-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260903_0141"
down_revision = "20260903_0140"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qhse_import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column(
            "imported_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_qhse_batch_user"),
            nullable=True,
        ),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("flagged_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.add_column(
        "qhse_reports",
        sa.Column(
            "import_batch_id",
            sa.Integer(),
            sa.ForeignKey(
                "qhse_import_batches.id", ondelete="SET NULL", name="fk_qhse_report_batch"
            ),
            nullable=True,
        ),
    )
    op.add_column("qhse_reports", sa.Column("source_code", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_qhse_reports_source_code", "qhse_reports", ["source_code"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_qhse_reports_source_code", table_name="qhse_reports")
    op.drop_column("qhse_reports", "source_code")
    op.drop_column("qhse_reports", "import_batch_id")
    op.drop_table("qhse_import_batches")
