"""Grilles tarifaires — commercial attitré, référence codifiée, échéances de règlement.

Lot 2 de la refonte commerciale. Migration **strictement additive** : aucune
colonne existante n'est supprimée ni contrainte davantage, pour que la reprise
des grilles déjà en base se fasse sans perte et sans interruption de service.

Trois apports :

1. ``commercial_clients.assigned_user_id`` — le **commercial attitré**. Il
   n'existait aucun champ de ce genre : « notifier le commercial du client » ne
   pouvait viser que le rôle entier. ``pipedrive_owner_id`` conserve à côté le
   propriétaire côté CRM, qui sert au rapprochement mais ne fait pas identité
   (un owner Pipedrive n'est pas un compte staff).

2. ``rate_grid_lines.tariff_reference`` — référence codifiée
   ``P-MMAA-MMAA-XX-YY`` (période de validité + pays POL/POD en ISO alpha-2).
   Elle est portée par la **route** et non par l'en-tête : la codification
   encode une paire POL/POD alors qu'une grille en couvre plusieurs.
   ``is_route_default`` désigne, parmi les grilles actives d'un client couvrant
   la même route, celle qui s'applique par défaut.

3. ``rate_grid_payment_terms`` — les conditions de règlement (1 à 3 échéances).
   Elles sont **déclaratives** : elles décrivent le contrat et alimentent la
   booking note, sans déclencher aucune facturation (le fret est facturé hors
   plateforme, par virement — arbitrage A5).

Reprise de l'existant : les références codifiées des routes déjà en base sont
calculées ici même, à partir de la période de la grille et du pays des ports.
Une route dont un port est inconnu du référentiel reçoit ``??`` sur le segment
correspondant — visible, plutôt que silencieusement faux.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260826_0120"
down_revision = "20260826_0119"
branch_labels = None
depends_on = None


def _mmaa(d) -> str:
    return f"{d.month:02d}{d.year % 100:02d}" if d is not None else "----"


def _cc(code) -> str:
    clean = (code or "").strip().upper()
    return clean[:2] if len(clean) >= 2 else "??"


def upgrade():
    # ── 1. Commercial attitré ────────────────────────────────────────────
    with op.batch_alter_table("commercial_clients") as batch:
        batch.add_column(sa.Column("assigned_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("pipedrive_owner_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_commercial_clients_assigned_user",
            "users",
            ["assigned_user_id"],
            ["id"],
        )
    op.create_index(
        "ix_commercial_clients_assigned_user_id",
        "commercial_clients",
        ["assigned_user_id"],
    )

    # ── 2. Référence codifiée + défaut par route ─────────────────────────
    with op.batch_alter_table("rate_grid_lines") as batch:
        batch.add_column(sa.Column("tariff_reference", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column(
                "is_route_default",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    op.create_index(
        "ix_rate_grid_lines_tariff_reference",
        "rate_grid_lines",
        ["tariff_reference"],
    )

    # ── 3. Échéances de règlement ────────────────────────────────────────
    op.create_table(
        "rate_grid_payment_terms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("grid_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=30), nullable=False),
        sa.Column("offset_days", sa.Integer(), nullable=True),
        sa.Column("percentage", sa.Numeric(5, 2), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(["grid_id"], ["rate_grids.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("grid_id", "position", name="uq_grid_payment_term_position"),
    )
    op.create_index(
        "ix_rate_grid_payment_terms_grid_id", "rate_grid_payment_terms", ["grid_id"]
    )

    # ── Reprise : référence codifiée des routes existantes ───────────────
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT l.id           AS line_id,
                   g.valid_from   AS valid_from,
                   g.valid_to     AS valid_to,
                   pol.country    AS pol_country,
                   pod.country    AS pod_country
              FROM rate_grid_lines l
              JOIN rate_grids g ON g.id = l.grid_id
              LEFT JOIN ports pol ON pol.locode = l.pol_locode
              LEFT JOIN ports pod ON pod.locode = l.pod_locode
            """
        )
    ).mappings()

    for row in rows:
        reference = (
            f"P-{_mmaa(row['valid_from'])}-{_mmaa(row['valid_to'])}"
            f"-{_cc(row['pol_country'])}-{_cc(row['pod_country'])}"
        )
        conn.execute(
            sa.text(
                "UPDATE rate_grid_lines SET tariff_reference = :ref WHERE id = :line_id"
            ),
            {"ref": reference, "line_id": row["line_id"]},
        )


def downgrade():
    op.drop_index("ix_rate_grid_payment_terms_grid_id", table_name="rate_grid_payment_terms")
    op.drop_table("rate_grid_payment_terms")

    op.drop_index("ix_rate_grid_lines_tariff_reference", table_name="rate_grid_lines")
    with op.batch_alter_table("rate_grid_lines") as batch:
        batch.drop_column("is_route_default")
        batch.drop_column("tariff_reference")

    op.drop_index("ix_commercial_clients_assigned_user_id", table_name="commercial_clients")
    with op.batch_alter_table("commercial_clients") as batch:
        batch.drop_constraint("fk_commercial_clients_assigned_user", type_="foreignkey")
        batch.drop_column("pipedrive_owner_id")
        batch.drop_column("assigned_user_id")
