"""Remboursement d'une vente à bord — geste du siège, par contre-passation.

ADR-013, décision 1. Le statut ``refunded`` était déclaré dans le modèle, lu
par la garde de ``settle_sale``, promis aux Opérations par la notice
commandant… et écrit par **aucun chemin de code**. Une vente encaissée par
erreur était définitive : la seule voie de correction était un accès SSH à la
production. C'était le manque le plus structurant du module.

Le remboursement est réservé au siège — le bord encaisse, il ne défait pas un
encaissement — et se fait par **contre-passation** : un mouvement de caisse
négatif dans la même catégorie et le même support que l'encaissement d'origine,
plus des retours en stock. Jamais par suppression : les deux registres restent
append-only, c'est ce qui fait leur valeur.

``refund_cashbox_movement_id`` est le verrou d'idempotence du remboursement,
symétrique de ``cashbox_movement_id`` pour l'encaissement — d'où son unicité.
``refund_requested_at`` porte la demande émise depuis le bord : sans elle, la
règle « seul le siège rembourse » se contournerait par téléphone et la trace
se perdrait.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260827_0131"
down_revision = "20260827_0130"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("refund_cashbox_movement_id", sa.Integer()),
    ("stripe_refund_id", sa.String(length=255)),
    ("refund_reason", sa.Text()),
    ("refunded_by_id", sa.Integer()),
    ("refund_requested_at", sa.DateTime(timezone=True)),
    ("refund_request_note", sa.Text()),
    ("refunded_at", sa.DateTime(timezone=True)),
)


def upgrade():
    for name, coltype in _COLUMNS:
        op.add_column("onboard_sales", sa.Column(name, coltype, nullable=True))
    op.create_foreign_key(
        "fk_onboard_sales_refund_movement",
        "onboard_sales",
        "cashbox_movements",
        ["refund_cashbox_movement_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_onboard_sales_refunded_by",
        "onboard_sales",
        "users",
        ["refunded_by_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_onboard_sale_refund_movement", "onboard_sales", ["refund_cashbox_movement_id"]
    )


def downgrade():
    op.drop_constraint("uq_onboard_sale_refund_movement", "onboard_sales", type_="unique")
    op.drop_constraint("fk_onboard_sales_refunded_by", "onboard_sales", type_="foreignkey")
    op.drop_constraint("fk_onboard_sales_refund_movement", "onboard_sales", type_="foreignkey")
    for name, _coltype in reversed(_COLUMNS):
        op.drop_column("onboard_sales", name)
