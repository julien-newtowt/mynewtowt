"""Origine du leg : ``legs.origin`` (reprise d'historique TOWT, ADR-014).

NEWTOWT est la reprise d'une compagnie antérieure (TOWT). Les voyages
2024-2026 de cette compagnie sont repris dans l'ERP comme des FAITS en lecture
seule : ``origin = 'towt_archive'``. Les legs vécus dans l'ERP portent
``origin = 'newtowt'`` (défaut serveur — aucune ligne existante ne change de
sens). Un leg d'archive est exclu de la renumérotation des codes, refuse
toute mutation (planning, escale, déclarations départ/arrivée) et se filtre
dans /planning. Index sur la colonne : c'est un critère de filtre.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260902_0138"
down_revision = "20260901_0137"
branch_labels = None
depends_on = None


TRIGGER_FN = """
CREATE OR REPLACE FUNCTION legs_refuse_towt_archive_write() RETURNS trigger AS $$
BEGIN
    -- Échappement explicite (scripts de reprise/correction) :
    --   SET LOCAL newtowt.allow_towt_archive_write = 'on';
    IF current_setting('newtowt.allow_towt_archive_write', true) = 'on' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF OLD.origin = 'towt_archive' THEN
        RAISE EXCEPTION 'leg % est une archive TOWT (lecture seule, ADR-014)', OLD.leg_code
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
"""
TRIGGER = """
CREATE TRIGGER trg_legs_towt_archive_readonly
BEFORE UPDATE OR DELETE ON legs
FOR EACH ROW EXECUTE FUNCTION legs_refuse_towt_archive_write();
"""


def upgrade():
    op.add_column(
        "legs",
        sa.Column("origin", sa.String(length=20), nullable=False, server_default="newtowt"),
    )
    op.create_index("ix_legs_origin", "legs", ["origin"])
    # Garde-fou au niveau base (PostgreSQL) : l'immutabilité de l'archive est
    # une propriété du schéma, pas seulement une promesse applicative.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(TRIGGER_FN)
        op.execute(TRIGGER)


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_legs_towt_archive_readonly ON legs")
        op.execute("DROP FUNCTION IF EXISTS legs_refuse_towt_archive_write()")
    op.drop_index("ix_legs_origin", table_name="legs")
    op.drop_column("legs", "origin")
