"""Vente à bord & caisse — contraintes d'intégrité sur des registres probants.

Les tables ``onboard_products``, ``onboard_sales``, ``onboard_sale_lines``,
``onboard_stock_movements`` et ``cashbox_movements`` ne portaient **aucune**
contrainte : ni vocabulaire (statuts, devises, catégories), ni borne de signe,
ni unicité du verrou d'idempotence. Tout le contrôle vivait en Python, alors
que le registre douanier de vente détaxée et le grand livre de caisse ont la
même valeur probante que le registre BL — qui porte, lui, 12 ``CheckConstraint``
(``models/packing_list.py``). Aucune route ne permettant de supprimer un
mouvement, une ligne aberrante y est définitive.

Deux familles de contraintes sont posées :

* **portables** (SQLite en test, PostgreSQL en production) : vocabulaires,
  bornes de signe, unicité de ``onboard_sales.cashbox_movement_id`` ;
* **finitude**, PostgreSQL uniquement : ``NaN``/``±Infinity`` sont acceptés par
  le type ``numeric``, et l'ordre PostgreSQL place ``NaN`` **au-dessus** de
  toutes les autres valeurs — un simple ``>= 0`` ne l'écarte donc pas. On borne
  explicitement entre ``-Infinity`` et ``Infinity`` exclus, ce qui exclut aussi
  ``NaN``.

La migration **refuse de s'appliquer** si des données existantes violent l'une
des contraintes, en nommant la table et le nombre de lignes : mieux vaut un
échec explicite au déploiement qu'une contrainte silencieusement absente.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260827_0126"
down_revision = "20260827_0125"
branch_labels = None
depends_on = None

_SALE_STATUSES = ("draft", "pending_payment", "paid", "cancelled", "refunded")
_PAYMENT_METHODS = ("cash", "card")
_CURRENCIES = ("EUR", "USD", "VND")
_PRODUCT_KINDS = ("bien", "service")
_STOCK_REASONS = ("avitaillement", "vente", "ajustement", "inventaire", "retour")
_INCOME_CATEGORIES = (
    "vente_a_bord",
    "depot_recharge",
    "remboursement",
    "autre_encaissement",
)
_EXPENSE_CATEGORIES = (
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


def _in(col: str, values) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


# (table, nom de contrainte, expression SQL portable)
_PORTABLE = [
    ("onboard_products", "ck_onboard_products_unit_price_non_negative", "unit_price >= 0"),
    ("onboard_products", "ck_onboard_products_currency", _in("currency", _CURRENCIES)),
    ("onboard_products", "ck_onboard_products_kind", _in("kind", _PRODUCT_KINDS)),
    ("onboard_stock_movements", "ck_onboard_stock_qty_non_zero", "qty <> 0"),
    ("onboard_stock_movements", "ck_onboard_stock_reason", _in("reason", _STOCK_REASONS)),
    ("onboard_sales", "ck_onboard_sales_total_non_negative", "total >= 0"),
    ("onboard_sales", "ck_onboard_sales_status", _in("status", _SALE_STATUSES)),
    (
        "onboard_sales",
        "ck_onboard_sales_payment_method",
        f"payment_method IS NULL OR {_in('payment_method', _PAYMENT_METHODS)}",
    ),
    ("onboard_sales", "ck_onboard_sales_currency", _in("currency", _CURRENCIES)),
    ("onboard_sale_lines", "ck_onboard_sale_line_qty_positive", "qty > 0"),
    (
        "onboard_sale_lines",
        "ck_onboard_sale_line_amounts_non_negative",
        "unit_price >= 0 AND line_total >= 0",
    ),
    ("cashbox_movements", "ck_cashbox_mov_amount_non_zero", "amount <> 0"),
    ("cashbox_movements", "ck_cashbox_mov_currency", _in("currency", _CURRENCIES)),
    (
        "cashbox_movements",
        "ck_cashbox_mov_category",
        _in("category", _INCOME_CATEGORIES + _EXPENSE_CATEGORIES),
    ),
]

# Bornes de finitude (PostgreSQL) : exclut ±Infinity, et NaN par la borne haute
# (PostgreSQL ordonne NaN au-dessus de toutes les valeurs, Infinity comprise).
_FINITE = [
    ("onboard_products", "ck_onboard_products_unit_price_finite", "unit_price"),
    ("onboard_stock_movements", "ck_onboard_stock_qty_finite", "qty"),
    ("onboard_sales", "ck_onboard_sales_total_finite", "total"),
    ("onboard_sale_lines", "ck_onboard_sale_line_qty_finite", "qty"),
    ("onboard_sale_lines", "ck_onboard_sale_line_total_finite", "line_total"),
    ("onboard_sale_lines", "ck_onboard_sale_line_unit_price_finite", "unit_price"),
    ("cashbox_movements", "ck_cashbox_mov_amount_finite", "amount"),
]


def _finite_expr(col: str) -> str:
    return f"{col} > '-Infinity'::numeric AND {col} < 'Infinity'::numeric"


def _assert_clean(bind, table: str, expr: str) -> None:
    """Refuse la migration si des lignes existantes violent la contrainte."""
    n = bind.execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE NOT ({expr})")).scalar_one()
    if n:
        raise RuntimeError(
            f"Migration 0126 interrompue : {n} ligne(s) de « {table} » violent « {expr} ». "
            "Corrigez ces données (ou retirez la contrainte concernée) avant de rejouer."
        )


def upgrade():
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ── Verrou d'idempotence du règlement : unicité en base ──────────────────
    dup = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM (SELECT cashbox_movement_id FROM onboard_sales "
            "WHERE cashbox_movement_id IS NOT NULL "
            "GROUP BY cashbox_movement_id HAVING COUNT(*) > 1) d"
        )
    ).scalar_one()
    if dup:
        raise RuntimeError(
            f"Migration 0126 interrompue : {dup} mouvement(s) de caisse rattaché(s) à plusieurs "
            "ventes — trace d'un double règlement. Traitez ces cas avant de poser l'unicité."
        )
    op.create_unique_constraint(
        "uq_onboard_sale_cashbox_movement", "onboard_sales", ["cashbox_movement_id"]
    )

    # ── Contraintes portables ────────────────────────────────────────────────
    for table, name, expr in _PORTABLE:
        _assert_clean(bind, table, expr)
        op.create_check_constraint(name, table, expr)

    # ── Finitude (PostgreSQL) ────────────────────────────────────────────────
    if is_pg:
        for table, name, col in _FINITE:
            expr = _finite_expr(col)
            _assert_clean(bind, table, expr)
            op.create_check_constraint(name, table, expr)

        # ── Alignement modèle ↔ base : ces colonnes sont `nullable=False` dans
        # les modèles mais avaient été créées nullables (migration 0005).
        for table, col in (
            ("onboard_cashboxes", "opened_at"),
            ("cashbox_movements", "recorded_at"),
            ("cashbox_closures", "closed_at"),
        ):
            bind.execute(sa.text(f"UPDATE {table} SET {col} = now() WHERE {col} IS NULL"))
            op.alter_column(table, col, nullable=False)


def downgrade():
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        for table, col in (
            ("onboard_cashboxes", "opened_at"),
            ("cashbox_movements", "recorded_at"),
            ("cashbox_closures", "closed_at"),
        ):
            op.alter_column(table, col, nullable=True)
        for table, name, _col in _FINITE:
            op.drop_constraint(name, table, type_="check")

    for table, name, _expr in reversed(_PORTABLE):
        op.drop_constraint(name, table, type_="check")
    op.drop_constraint("uq_onboard_sale_cashbox_movement", "onboard_sales", type_="unique")
