"""Add ANEMOS Carnet de Bord fields to Vessel model.

Adds technical specifications for the vessel that are needed for the
Carnet de Bord ANEMOS report (REF - referential data).

Revision ID: 20260624_0074
Revises: 20260624_0073
Create Date: 2026-06-24 00:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260624_0074"
down_revision = "20260624_0073"
branch_labels = None
depends_on = None


def upgrade():
    # Add dimensions
    op.add_column("vessels", sa.Column("loa_m", sa.Float(), nullable=True, comment="Longueur hors-tout (Length Overall) en mètres"))
    op.add_column("vessels", sa.Column("beam_m", sa.Float(), nullable=True, comment="Largeur (Beam) en mètres"))
    op.add_column("vessels", sa.Column("height_m", sa.Float(), nullable=True, comment="Hauteur totale en mètres"))
    op.add_column("vessels", sa.Column("mast_height_m", sa.Float(), nullable=True, comment="Hauteur de mât en mètres"))
    op.add_column("vessels", sa.Column("draft_max_m", sa.Float(), nullable=True, comment="Tirant d'eau maximal en mètres"))

    # Add sail area
    op.add_column("vessels", sa.Column("sail_area_sqm", sa.Float(), nullable=True, comment="Surface totale de voilure en m2"))

    # Add capacities
    op.add_column("vessels", sa.Column("capacity_barriques", sa.Integer(), nullable=True, comment="Capacité en barriques"))
    op.add_column("vessels", sa.Column("capacity_pax", sa.Integer(), nullable=True, comment="Capacité en passagers"))

    # Add identification
    op.add_column("vessels", sa.Column("home_port", sa.String(length=100), nullable=True, comment="Port d'attache"))
    op.add_column("vessels", sa.Column("port_of_registry", sa.String(length=100), nullable=True, comment="Port d'immatriculation"))

    # Add build dates
    op.add_column("vessels", sa.Column("build_start_date", sa.Date(), nullable=True, comment="Date de début de construction"))
    op.add_column("vessels", sa.Column("build_end_date", sa.Date(), nullable=True, comment="Date de fin de construction / mise en service"))

    # Add descriptions for Carnet de Bord
    op.add_column("vessels", sa.Column("description", sa.Text(), nullable=True, comment="Description du navire pour le Carnet de Bord"))
    op.add_column("vessels", sa.Column("crew_description", sa.Text(), nullable=True, comment="Description de l'équipage type pour ce navire"))


def downgrade():
    # Drop all added columns
    op.drop_column("vessels", "crew_description")
    op.drop_column("vessels", "description")
    op.drop_column("vessels", "build_end_date")
    op.drop_column("vessels", "build_start_date")
    op.drop_column("vessels", "port_of_registry")
    op.drop_column("vessels", "home_port")
    op.drop_column("vessels", "capacity_pax")
    op.drop_column("vessels", "capacity_barriques")
    op.drop_column("vessels", "sail_area_sqm")
    op.drop_column("vessels", "draft_max_m")
    op.drop_column("vessels", "mast_height_m")
    op.drop_column("vessels", "height_m")
    op.drop_column("vessels", "beam_m")
    op.drop_column("vessels", "loa_m")
