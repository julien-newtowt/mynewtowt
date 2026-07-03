"""Durcissement des règles de planification (audit 2026-07).

1. **``schedule_revisions``** — historique append-only des recalculs de
   dates (édition planning, drag-drop Gantt, ETA-shift capitaine, cascade).
   Survit à la suppression du leg (FK ``SET NULL`` + snapshot ``leg_code``).

2. **Normalisation des statuts** — la taxonomie canonique est
   ``in_progress`` ; les graphies historiques ``inprogress`` (écrites par
   d'anciens flux et testées par les templates) sont converties sur
   ``legs`` et ``scenario_legs``.

3. **Contrainte d'exclusion Postgres** (filet anti-course) : deux legs non
   annulés d'un même navire ne peuvent pas se chevaucher dans le temps —
   ``EXCLUDE USING gist (vessel_id WITH =, tstzrange(etd, eta) WITH &&)``.
   ``DEFERRABLE INITIALLY DEFERRED`` : la cascade décale plusieurs legs
   dans une même transaction, la contrainte est vérifiée au commit.
   Postgres uniquement (les tests SQLite s'appuient sur la validation
   applicative).

   La pose de la contrainte est **conditionnelle** : si des chevauchements
   existent déjà en base (données héritées d'avant la validation dure), la
   migration les LISTE en sortie et SAUTE la contrainte au lieu d'échouer
   — le déploiement n'est pas bloqué par un nettoyage de données, et la
   validation applicative protège de toute façon les nouvelles écritures.
   Une fois les legs corrigés dans /planning, poser la contrainte à la
   main (cf. runbook §4.2) :

       ALTER TABLE legs ADD CONSTRAINT legs_no_vessel_overlap
       EXCLUDE USING gist (vessel_id WITH =, tstzrange(etd, eta) WITH &&)
       WHERE (status <> 'cancelled') DEFERRABLE INITIALLY DEFERRED;

Revision ID: 20260703_0094
Revises: 20260702_0093
Create Date: 2026-07-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260703_0094"
down_revision = "20260702_0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Historique des recalculs ────────────────────────────────────
    op.create_table(
        "schedule_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "leg_id",
            sa.Integer(),
            sa.ForeignKey("legs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("leg_code", sa.String(length=20), nullable=True),
        sa.Column("vessel_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("batch_id", sa.String(length=32), nullable=False),
        sa.Column(
            "trigger_leg_id",
            sa.Integer(),
            sa.ForeignKey("legs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("old_etd", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_etd", sa.DateTime(timezone=True), nullable=True),
        sa.Column("old_eta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_eta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("user_name", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_schedule_revisions_leg_id", "schedule_revisions", ["leg_id"])
    op.create_index("ix_schedule_revisions_vessel_id", "schedule_revisions", ["vessel_id"])
    op.create_index("ix_schedule_revisions_batch_id", "schedule_revisions", ["batch_id"])
    op.create_index("ix_schedule_revisions_created", "schedule_revisions", ["created_at"])

    # ── 2. Normalisation des statuts de legs ──────────────────────────
    op.execute("UPDATE legs SET status = 'in_progress' WHERE status = 'inprogress'")
    op.execute("UPDATE scenario_legs SET status = 'in_progress' WHERE status = 'inprogress'")

    # ── 3. Contrainte d'exclusion anti-chevauchement (Postgres) ────────
    # Pose CONDITIONNELLE : des chevauchements hérités (saisis avant la
    # validation dure) rendraient l'ALTER TABLE impossible et bloqueraient
    # tout le déploiement. On les détecte d'abord ; s'il y en a, on les
    # liste en sortie de migration et on saute la contrainte (la validation
    # applicative couvre les nouvelles écritures). Cf. runbook §4.2 pour la
    # pose manuelle après nettoyage.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        overlaps = bind.execute(
            sa.text(
                """
                SELECT a.leg_code AS code_a, a.etd AS etd_a, a.eta AS eta_a,
                       b.leg_code AS code_b, b.etd AS etd_b, b.eta AS eta_b
                FROM legs a
                JOIN legs b
                  ON a.vessel_id = b.vessel_id AND a.id < b.id
                WHERE a.status <> 'cancelled' AND b.status <> 'cancelled'
                  AND a.etd < b.eta AND a.eta > b.etd
                ORDER BY a.etd
                """
            )
        ).fetchall()
        if overlaps:
            print(
                "\n[0094] ⚠ CONTRAINTE legs_no_vessel_overlap NON POSÉE — "
                f"{len(overlaps)} chevauchement(s) existant(s) à corriger dans /planning :"
            )
            for row in overlaps:
                print(
                    f"[0094]   {row.code_a} ({row.etd_a:%Y-%m-%d %H:%M} → "
                    f"{row.eta_a:%Y-%m-%d %H:%M})  ⟂  {row.code_b} "
                    f"({row.etd_b:%Y-%m-%d %H:%M} → {row.eta_b:%Y-%m-%d %H:%M})"
                )
            print(
                "[0094] Après correction, poser la contrainte manuellement "
                "(SQL dans docs/operations/01-runbook.md §4.2).\n"
            )
        else:
            op.execute(
                """
                ALTER TABLE legs ADD CONSTRAINT legs_no_vessel_overlap
                EXCLUDE USING gist (
                    vessel_id WITH =,
                    tstzrange(etd, eta) WITH &&
                )
                WHERE (status <> 'cancelled')
                DEFERRABLE INITIALLY DEFERRED
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE legs DROP CONSTRAINT IF EXISTS legs_no_vessel_overlap")
    op.execute("UPDATE legs SET status = 'inprogress' WHERE status = 'in_progress'")
    op.execute("UPDATE scenario_legs SET status = 'inprogress' WHERE status = 'in_progress'")
    op.drop_index("ix_schedule_revisions_created", table_name="schedule_revisions")
    op.drop_index("ix_schedule_revisions_batch_id", table_name="schedule_revisions")
    op.drop_index("ix_schedule_revisions_vessel_id", table_name="schedule_revisions")
    op.drop_index("ix_schedule_revisions_leg_id", table_name="schedule_revisions")
    op.drop_table("schedule_revisions")
