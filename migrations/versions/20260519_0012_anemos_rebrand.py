"""Rebrand certificat CO₂ → Label Anemos.

Renomme la table ``co2_certificates`` en ``anemos_certificates``. Les
colonnes (co2_emitted_kg, co2_avoided_kg, etc.) restent inchangées —
ce sont des métriques scientifiques (kg de CO₂), pas du branding.

Revision ID: 20260519_0012
Revises: 20260519_0011
"""
from __future__ import annotations

from alembic import op


revision = "20260519_0012"
down_revision = "20260519_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("co2_certificates", "anemos_certificates")


def downgrade() -> None:
    op.rename_table("anemos_certificates", "co2_certificates")
