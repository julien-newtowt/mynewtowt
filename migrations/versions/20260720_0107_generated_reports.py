"""Add generated_reports (archivage des PDF générés serveur — trombinoscope).

Aucun document généré par l'application (facture, BL, carnet de bord...) n'est
aujourd'hui persisté côté serveur : chaque PDF est produit à la demande et
streamé directement. Le trombinoscope introduit une première exigence
d'archivage (cf. docs/strategy/CAHIER_DES_CHARGES_TROMBINOSCOPE.md §4.2) :
une table légère de métadonnées (type, période, chemin fichier), le fichier
lui-même vivant sous settings.upload_dir/generated_reports/, hors périmètre
de services.safe_files (contenu généré, pas uploadé par un utilisateur).

Revision ID: 20260720_0107
Revises: 20260720_0106
Create Date: 2026-07-20 00:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260720_0107"
down_revision = "20260720_0106"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "generated_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(length=60), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "generated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
    )
    op.create_index(
        "ix_generated_reports_type_period", "generated_reports", ["type", "period"]
    )


def downgrade():
    op.drop_index("ix_generated_reports_type_period", table_name="generated_reports")
    op.drop_table("generated_reports")
