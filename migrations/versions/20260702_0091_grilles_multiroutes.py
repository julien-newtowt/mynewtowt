"""Grilles tarifaires multi-routes (P11) — marqueur de révision.

Contexte (important) : la refonte multi-routes (1 grille = 1 client/défaut +
1 période + N routes) a **déjà été livrée** dans l'historique de cette branche
par la migration ``20260619_0054_rategrid_multiroutes`` (drop/recreate de
``rate_grid_lines`` en routes + colonnes ``rate_grids.vessel_id`` / ``bl_fee`` /
``booking_fee`` / ``brackets_json``, suppression de ``pol_locode`` /
``pod_locode`` / ``base_rate_per_palette`` de l'en-tête). ``0054`` est un
ancêtre linéaire du head courant (``20260702_0088``) : le schéma cible est donc
déjà en place.

Cette révision existe pour matérialiser l'étape « grilles multi-routes » du lot
P11 et fournir un point d'ancrage stable au chaînage des comptes-ancres
(``20260702_0093_comptes_ancres`` ← ``20260702_0091``). Elle est
**volontairement no-op** : ré-appliquer le DDL de ``0054`` échouerait (colonnes/
table déjà présentes). ``down_revision`` pointe sur le head réel ``0088`` (le
plan mentionnait ``0089``, qui n'existe pas dans cette branche).

Revision ID: 20260702_0091
Revises: 20260702_0088
Create Date: 2026-07-02
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260702_0091"
down_revision = "20260702_0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op : le schéma multi-routes est déjà appliqué par 20260619_0054.
    pass


def downgrade() -> None:
    # No-op : ne pas défaire 20260619_0054 (géré par sa propre downgrade).
    pass
