"""Date de mise à bord du connaissement — override justifié des Opérations.

Cf. ``docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md`` §5.0 :

    « Le jour de *ship on board* devrait être le dernier jour des opérations. Avec
    la possibilité d'être modifié par l'équipe opérations (sous justification) et
    journal de modification en cas de contrôle. »

⚠️ **La date effective n'est PAS stockée.** Elle est dérivée à la lecture du
dernier jour des opérations **réelles** de l'escale (``escale_operations``). Ces
colonnes ne portent que l'**override** et sa justification : la présence de
``bl_sob_date`` est précisément ce qui distingue « corrigé volontairement à cette
valeur » de « pas corrigé ». Recopier la dérivée ici la figerait, et elle
deviendrait fausse en silence dès que la timeline d'escale bouge.

La contrainte ``ck_bl_sob_override_needs_reason`` pose l'exigence de justification
**en base**, et pas seulement dans le formulaire : le journal demandé « en cas de
contrôle » n'a de valeur que si le motif existe toujours, quel que soit le chemin
d'écriture — présent ou futur.

Enjeu métier : un connaissement antidaté est une fraude documentaire et une cause
d'exclusion de garantie.

Réversible : ``downgrade`` retire la contrainte, la clé étrangère puis les
colonnes. Aucune donnée préexistante n'est touchée (toutes les colonnes sont
ajoutées nullables).

Revision ID: 20260817_0115
Revises: 20260814_0114
Create Date: 2026-08-17 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260817_0115"
down_revision = "20260814_0114"
branch_labels = None
depends_on = None


# (nom, type) — énumérés une seule fois, réutilisés par le downgrade.
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("bl_sob_date", sa.Date()),
    ("bl_sob_reason", sa.Text()),
    ("bl_sob_by_id", sa.Integer()),
    ("bl_sob_at", sa.DateTime(timezone=True)),
)


def upgrade():
    for name, type_ in _COLUMNS:
        op.add_column("packing_list_batches", sa.Column(name, type_, nullable=True))

    op.create_foreign_key(
        "fk_plb_bl_sob_by", "packing_list_batches", "users", ["bl_sob_by_id"], ["id"]
    )

    # « Sous justification » — rendu instockable autrement.
    op.create_check_constraint(
        "ck_bl_sob_override_needs_reason",
        "packing_list_batches",
        "bl_sob_date IS NULL OR bl_sob_reason IS NOT NULL",
    )


def downgrade():
    op.drop_constraint("ck_bl_sob_override_needs_reason", "packing_list_batches", type_="check")
    op.drop_constraint("fk_plb_bl_sob_by", "packing_list_batches", type_="foreignkey")
    for name, _type in reversed(_COLUMNS):
        op.drop_column("packing_list_batches", name)
