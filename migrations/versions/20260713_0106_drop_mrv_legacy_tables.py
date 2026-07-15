"""Suppression définitive du legacy MRV — DROP mrv_events / mrv_parameters.

Le décommissionnement du lot 14 (migration ``20260709_0105``) avait
volontairement CONSERVÉ ``mrv_events``/``mrv_parameters`` en archive lecture
seule, faute de preuve qu'elles pouvaient être supprimées sans risque.

Vérification approfondie complémentaire (2026-07-13) : aucune preuve que
``MRVEvent`` a jamais servi à une déclaration réglementaire réellement soumise
aux autorités — la déclaration EU MRV 2025 a été produite par un outil externe
(« OVDAdmin »), pas par mynewtowt. Le CRUD manuel de ``MRVEvent`` n'a existé
que ~2,5 semaines dans un dépôt qui n'a que 5 semaines d'historique Git total,
et aucune migration ne contient de données réelles insérées (DDL uniquement).
Décision : DROP complet des deux tables et de tout le code applicatif associé
(modèle, services ``mrv_compute``/``mrv_sync``, écran d'archive).

``decimal_to_dms`` (seule fonction encore active, exports OVDLA/OVDBR) a été
déplacée vers ``app.utils.geo`` avant cette migration.

⚠ GATE HUMAIN avant application sur un environnement avec données réelles :
vérifier ``SELECT COUNT(*) FROM mrv_events;`` / ``SELECT COUNT(*) FROM
mrv_parameters;`` sur la vraie base de production. Ne pas lancer
``alembic upgrade head`` en production avant cette vérification.

Revision ID: 20260713_0106
Revises: 20260709_0105
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260713_0106"
down_revision = "20260709_0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("mrv_parameters")
    op.drop_table("mrv_events")


def downgrade() -> None:
    # Recrée le schéma dans son état juste avant cette migration (identique au
    # modèle historique ``app.models.mrv``, retiré de l'application par cette
    # même suppression) — réversible au niveau structure ; la DONNÉE serait
    # perdue (un DROP n'est jamais réversible côté contenu).
    op.create_table(
        "mrv_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("leg_id", sa.Integer(), sa.ForeignKey("legs.id"), nullable=False, index=True),
        sa.Column("event_kind", sa.String(40), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fuel_type", sa.String(20), server_default="MDO", nullable=False),
        sa.Column("fuel_volume_l", sa.Numeric(12, 2)),
        sa.Column("fuel_mass_t", sa.Numeric(10, 3)),
        sa.Column("rob_l", sa.Numeric(12, 2)),
        sa.Column("distance_nm", sa.Numeric(10, 2)),
        sa.Column("time_at_sea_h", sa.Numeric(8, 2)),
        sa.Column("cargo_carried_t", sa.Numeric(10, 2)),
        sa.Column("notes", sa.Text()),
        sa.Column("noon_report_id", sa.Integer(), sa.ForeignKey("noon_reports.id"), unique=True),
        sa.Column("sof_event_id", sa.Integer(), sa.ForeignKey("sof_events.id"), unique=True),
        sa.Column("port_me_do_counter", sa.Numeric(12, 2)),
        sa.Column("stbd_me_do_counter", sa.Numeric(12, 2)),
        sa.Column("fwd_gen_do_counter", sa.Numeric(12, 2)),
        sa.Column("aft_gen_do_counter", sa.Numeric(12, 2)),
        sa.Column("bunkering_qty_t", sa.Numeric(10, 3)),
        sa.Column("me_consumption_t", sa.Numeric(10, 3)),
        sa.Column("ae_consumption_t", sa.Numeric(10, 3)),
        sa.Column("total_consumption_t", sa.Numeric(10, 3)),
        sa.Column("rob_calculated_t", sa.Numeric(10, 3)),
        sa.Column("lat_deg", sa.Integer()),
        sa.Column("lat_min", sa.Numeric(6, 3)),
        sa.Column("lat_ns", sa.CHAR(1)),
        sa.Column("lon_deg", sa.Integer()),
        sa.Column("lon_min", sa.Numeric(6, 3)),
        sa.Column("lon_ew", sa.CHAR(1)),
        sa.Column("quality_status", sa.String(20)),
        sa.Column("quality_notes", sa.Text()),
        sa.Column("created_by", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "mrv_parameters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), unique=True, nullable=False),
        sa.Column("value", sa.Numeric(12, 4), nullable=False),
        sa.Column("unit", sa.String(20)),
        sa.Column("description", sa.Text()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
