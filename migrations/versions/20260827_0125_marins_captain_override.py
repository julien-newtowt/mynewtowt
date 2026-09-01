"""Vente à bord — pose l'override de permission ``(marins × captain) = CM``.

Le module « Vente à bord » (`/captain/ventes`) et la « Caisse de bord »
(`/cashbox`) sont gardés par le module de permission ``captain`` : lecture en
``C``, **toute écriture en ``M``**. Or la matrice codée en dur donne
``("marins", "captain"): "C"`` — consultation seule — alors que le commandant
est précisément l'utilisateur cible du module.

La documentation (CLAUDE.md, en-tête de ``onboard_sales_router``) annonçait
« marins → CM via override ». Cet override n'était posé **nulle part** : la
table ``role_permissions`` (ARC-04) est créée vide par la migration 0026 et
aucun seed ne l'alimentait. Conséquence en exploitation : le commandant voit le
menu et ouvre les écrans (la barre latérale se contente du niveau ``C``), puis
se heurte à un **403 au premier bouton**, sans explication — mode d'échec
constaté lors d'un test à bord.

On matérialise donc la configuration documentée, pour qu'elle ne dépende plus
d'une manipulation manuelle dans ``/admin/permissions``. La cellule reste
modifiable par un administrateur : cette migration pose une valeur par défaut,
elle ne verrouille rien.

Idempotente : n'insère que si la cellule n'existe pas déjà (une valeur posée à
la main par un administrateur est **conservée**, jamais écrasée).
"""

import sqlalchemy as sa
from alembic import op

revision = "20260827_0125"
down_revision = "20260826_0124"
branch_labels = None
depends_on = None

_ROLE = "marins"
_MODULE = "captain"
_LEVEL = "CM"


def upgrade():
    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT level FROM role_permissions WHERE role = :r AND module = :m"),
        {"r": _ROLE, "m": _MODULE},
    ).scalar_one_or_none()
    if existing is not None:
        # Un administrateur a déjà tranché pour cette cellule : on n'y touche pas.
        return
    bind.execute(
        sa.text(
            "INSERT INTO role_permissions (role, module, level, updated_by) "
            "VALUES (:r, :m, :lvl, :by)"
        ),
        {"r": _ROLE, "m": _MODULE, "lvl": _LEVEL, "by": "migration-0125"},
    )


def downgrade():
    # Ne retire que l'override exact posé ici : si un administrateur l'a modifié
    # depuis, sa décision prime et la ligne est laissée en place.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions "
            "WHERE role = :r AND module = :m AND level = :lvl AND updated_by = :by"
        ),
        {"r": _ROLE, "m": _MODULE, "lvl": _LEVEL, "by": "migration-0125"},
    )
