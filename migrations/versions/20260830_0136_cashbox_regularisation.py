"""Régularisation d'un écart de caisse — geste du siège (ADR-014).

Le contrôle de caisse constate un écart, mais rien n'encadrait sa **suite**. Un
commandant pouvait faire disparaître un manquant par un simple « Autre
encaissement », indiscernable d'une écriture ordinaire : le contrôle constatait
alors un écart que celui qui en répond pouvait solder lui-même.

Deux ajouts, tous deux nécessaires à la décision :

* deux catégories dédiées — absentes des listes sélectionnables du bord, donc
  atteignables uniquement par la route ``finance:M`` ;
* ``settles_cash_count_id``, qui adosse la régularisation à l'écart qu'elle
  solde. Distinct de ``cash_count_id``, qui dit « gelé **par** ce contrôle » :
  confondre les deux ferait passer une régularisation pour un mouvement
  verrouillé.

La contrainte ``ck_cashbox_mov_category`` (posée en 0126) est reconstruite pour
accepter les deux nouveaux codes. La reconstruction est **PostgreSQL seulement**
— SQLite ne sait pas supprimer une contrainte CHECK sans réécrire la table, et
les tests ne passent pas par Alembic (le schéma y est construit depuis les
modèles). La production est PostgreSQL.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260830_0136"
down_revision = "20260828_0135"
branch_labels = None
depends_on = None

_INCOME = (
    "vente_a_bord",
    "depot_recharge",
    "remboursement",
    "autre_encaissement",
)
_EXPENSE = (
    "avance_equipage",
    "avitaillement",
    "transport_terrestre",
    "urgence_medicale",
    "petit_entretien",
    "representation",
    "frais_portuaire",
    "douane",
    "carburant_annexe",
    "autre",
)
_REGULARISATION = ("regularisation_excedent", "regularisation_manquant")


def _in(values) -> str:
    return f"category IN ({', '.join(repr(v) for v in values)})"


def upgrade():
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    op.add_column(
        "cashbox_movements", sa.Column("settles_cash_count_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_cashbox_movements_settles_cash_count",
        "cashbox_movements",
        "cash_counts",
        ["settles_cash_count_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_cashbox_movements_settles_cash_count",
        "cashbox_movements",
        ["settles_cash_count_id"],
    )

    if is_pg:
        op.drop_constraint("ck_cashbox_mov_category", "cashbox_movements", type_="check")
        op.create_check_constraint(
            "ck_cashbox_mov_category",
            "cashbox_movements",
            _in(_INCOME + _EXPENSE + _REGULARISATION),
        )


def downgrade():
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        # Une régularisation déjà passée violerait la contrainte d'origine : on
        # refuse plutôt que de la laisser échouer à mi-parcours, ou pire, de
        # supprimer des écritures d'un grand livre append-only.
        n = bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM cashbox_movements WHERE NOT ("
                + _in(_INCOME + _EXPENSE)
                + ")"
            )
        ).scalar_one()
        if n:
            raise RuntimeError(
                f"Retour arrière 0136 impossible : {n} mouvement(s) de régularisation en base. "
                "Contre-passez-les avant de retirer la contrainte élargie."
            )
        op.drop_constraint("ck_cashbox_mov_category", "cashbox_movements", type_="check")
        op.create_check_constraint(
            "ck_cashbox_mov_category", "cashbox_movements", _in(_INCOME + _EXPENSE)
        )

    op.drop_index("ix_cashbox_movements_settles_cash_count", table_name="cashbox_movements")
    op.drop_constraint(
        "fk_cashbox_movements_settles_cash_count", "cashbox_movements", type_="foreignkey"
    )
    op.drop_column("cashbox_movements", "settles_cash_count_id")
