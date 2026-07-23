"""Add ports.mrv_scope — périmètre MRV UE du référentiel Ports (G14).

Colonne booléenne additive (server_default ``false`` → ports existants = hors
périmètre par défaut), éditable en admin (toggle, comme ``is_active``/
``is_shortcut``) — jamais dérivée d'une liste de préfixes pays codée en dur
dans le calcul (le périmètre réglementaire évolue, ex. extension RUP 2024).

Seed initial minimal (architecture §7.3/§9.1) : bascule à ``True`` les ports
déjà en base dont le pays (ISO2) est France ou Portugal. Les Régions
Ultrapériphériques françaises (Guadeloupe/Martinique/Réunion/Guyane/Mayotte)
sont couvertes par ce même filtre si leur ``country`` est bien codé ``FR``
(pas de code ISO2 distinct pour les DROM) ; sinon, à confirmer/basculer
manuellement via l'écran ``/admin/ports`` — c'est précisément le point que
cet attribut éditable est censé permettre sans nouveau déploiement.

Revision ID: 20260715_0109
Revises: 20260715_0108
Create Date: 2026-07-15

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260715_0109"
down_revision = "20260715_0108"
branch_labels = None
depends_on = None

_SEED_MRV_SCOPE_COUNTRIES = ("FR", "PT")


def upgrade():
    op.add_column(
        "ports",
        sa.Column("mrv_scope", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    ports = sa.table("ports", sa.column("country", sa.String), sa.column("mrv_scope", sa.Boolean))
    op.execute(
        ports.update().where(ports.c.country.in_(_SEED_MRV_SCOPE_COUNTRIES)).values(mrv_scope=True)
    )


def downgrade():
    op.drop_column("ports", "mrv_scope")
