"""Retire l'override ``(marins × captain) = CM`` posé par la migration 0125.

Revue de sécurité du 2026-08-28. La migration 0125 donnait au rôle ``marins``
l'écriture sur **tout le module ``captain``** pour qu'un commandant puisse
encaisser. Or ce module couvre bien plus que la vente : SOF, décalages d'ETA,
messagerie du bord, documents cargo, saisie MRV — **39 routes d'écriture qui ne
contrôlent pas le navire**. Le cloisonnement par navire (ADR-012) n'ayant été
appliqué qu'aux deux modules audités, la permission accordée pour la caisse
conférait en réalité un droit d'écriture sur toute la flotte pour ces autres
surfaces. C'était une escalade de privilège.

La correction est structurelle et non un rustine : la vente à bord et la caisse
vivent désormais dans leur **propre module de permission** (``ventes``), où
``marins`` a ``CM`` par défaut dans la matrice codée en dur. Plus aucun override
n'est nécessaire, et ``(marins, captain)`` revient à sa valeur de consultation.

Ne supprime que la ligne exacte posée par 0125 (``updated_by = 'migration-0125'``)
— une valeur ajustée depuis par un administrateur est sa décision, on n'y touche
pas.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260828_0135"
down_revision = "20260827_0134"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().execute(
        sa.text(
            "DELETE FROM role_permissions "
            "WHERE role = 'marins' AND module = 'captain' AND updated_by = 'migration-0125'"
        )
    )


def downgrade():
    # Repose l'override tel que 0125 l'avait laissé, si la cellule est libre.
    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT level FROM role_permissions WHERE role='marins' AND module='captain'")
    ).scalar_one_or_none()
    if existing is None:
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role, module, level, updated_by) "
                "VALUES ('marins', 'captain', 'CM', 'migration-0125')"
            )
        )
