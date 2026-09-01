"""État de caisse déclaré par le commandant — opération de contrôle.

À chaque **fin d'embarquement** et à chaque **fin de mois**, le commandant
sortant déclare l'état complet de sa caisse : le détail du comptage **coupure
par coupure**, pour chaque devise détenue à bord.

Pourquoi une entité dédiée plutôt qu'un champ de plus sur la clôture mensuelle :

* la clôture ne connaissait qu'un **solde compté global par devise**, saisi
  d'un bloc. Un total non détaillé n'est pas vérifiable : il ne dit ni comment
  il a été obtenu, ni où se situe un écart ;
* elle est **mensuelle**, alors que la responsabilité du cash change à la
  **relève** — un écart découvert un mois plus tard l'est après le débarquement
  de celui qui tenait la caisse ;
* la caisse n'avait, jusqu'ici, **aucun détenteur identifié**. L'état déclaré
  nomme le commandant sortant et, le cas échéant, l'entrant : c'est ce qui rend
  un écart imputable.

L'écart (`variance`) est calculé et **historisé** au moment de la déclaration,
avec le solde théorique figé : un mouvement saisi après coup ne réécrit jamais
un contrôle déjà rendu.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# ── Vocabulaire ──────────────────────────────────────────────────────────────

# Ce qui déclenche le contrôle. Les deux cas sont demandés par l'exploitation ;
# `controle` couvre une vérification ponctuelle (inspection, doute, passation
# anticipée) sans forcer à mentir sur le motif.
CASH_COUNT_TRIGGERS: tuple[str, ...] = ("fin_embarquement", "fin_de_mois", "controle")
CASH_COUNT_TRIGGER_LABELS: dict[str, str] = {
    "fin_embarquement": "Fin d'embarquement (relève)",
    "fin_de_mois": "Fin de mois",
    "controle": "Contrôle ponctuel",
}

CASH_COUNT_STATUSES: tuple[str, ...] = ("declare", "valide", "conteste")
CASH_COUNT_STATUS_LABELS: dict[str, str] = {
    "declare": "Déclaré",
    "valide": "Validé par le siège",
    "conteste": "Contesté",
}

DENOMINATION_KINDS: tuple[str, ...] = ("billet", "piece")


class CashCount(Base):
    """Un état de caisse déclaré à une date, par un commandant nommé."""

    __tablename__ = "cash_counts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cashbox_id: Mapped[int] = mapped_column(
        ForeignKey("onboard_cashboxes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    # Date du comptage — une date, pas un instant (cf. `cashbox.as_movement_date`).
    counted_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # Détenteur sortant. Le nom est figé en clair : un commandant débarque, son
    # compte peut être désactivé, l'état doit rester lisible des années après.
    declared_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    declared_by_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Détenteur entrant, quand l'état accompagne une relève.
    handover_to_name: Mapped[str | None] = mapped_column(String(200))

    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"), index=True)
    # Rattachement facultatif à la clôture mensuelle qui a suivi.
    closure_id: Mapped[int | None] = mapped_column(
        ForeignKey("cashbox_closures.id", ondelete="SET NULL"), index=True
    )

    status: Mapped[str] = mapped_column(String(20), default="declare", nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    review_comment: Mapped[str | None] = mapped_column(Text)

    # `selectin` plutôt que le chargement paresseux : un état de caisse ne se
    # lit jamais sans ses devises (écran, historique, journal d'activité), et un
    # accès paresseux en contexte async lève `MissingGreenlet`. Les collections
    # sont minuscules — au plus 3 devises — donc la requête supplémentaire est
    # sans effet mesurable.
    currencies: Mapped[list[CashCountCurrency]] = relationship(
        back_populates="count",
        cascade="all, delete-orphan",
        order_by="CashCountCurrency.currency",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "trigger IN ('fin_embarquement', 'fin_de_mois', 'controle')",
            name="ck_cash_counts_trigger",
        ),
        CheckConstraint(
            "status IN ('declare', 'valide', 'conteste')", name="ck_cash_counts_status"
        ),
        Index("ix_cash_counts_box_date", "cashbox_id", "counted_on"),
    )

    @property
    def trigger_label(self) -> str:
        return CASH_COUNT_TRIGGER_LABELS.get(self.trigger, self.trigger)

    @property
    def status_label(self) -> str:
        return CASH_COUNT_STATUS_LABELS.get(self.status, self.status)

    @property
    def has_variance(self) -> bool:
        return any(c.variance != 0 for c in self.currencies)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CashCount {self.counted_on} {self.trigger} box={self.cashbox_id}>"


class CashCountCurrency(Base):
    """Le bloc d'une devise dans un état de caisse : compté, théorique, écart."""

    __tablename__ = "cash_count_currencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cash_count_id: Mapped[int] = mapped_column(
        ForeignKey("cash_counts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)

    # Pièces non détaillées — reprend la ligne « Pièces » du document type, que
    # le commandant renseigne d'un montant global quand il ne trie pas la
    # ferraille (usage constaté sur les devises secondaires).
    bulk_coins_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), nullable=False
    )
    # Σ(lignes) + vrac. Recalculé côté serveur, jamais repris du formulaire.
    counted_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # Solde théorique **figé** à l'instant de la déclaration : un mouvement
    # saisi après coup ne doit pas réécrire un contrôle déjà rendu.
    computed_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # compté − théorique. Historisé tel quel : c'est l'objet du contrôle.
    variance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    variance_reason: Mapped[str | None] = mapped_column(Text)

    count: Mapped[CashCount] = relationship(back_populates="currencies")
    lines: Mapped[list[CashCountLine]] = relationship(
        back_populates="block",
        cascade="all, delete-orphan",
        order_by="CashCountLine.denomination.desc()",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("cash_count_id", "currency", name="uq_cash_count_currency"),
        CheckConstraint("bulk_coins_amount >= 0", name="ck_cash_count_bulk_non_negative"),
        CheckConstraint("counted_total >= 0", name="ck_cash_count_total_non_negative"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CashCountCurrency {self.currency} {self.counted_total} écart={self.variance}>"


class CashCountLine(Base):
    """Une coupure comptée : valeur faciale × nombre."""

    __tablename__ = "cash_count_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cash_count_currency_id: Mapped[int] = mapped_column(
        ForeignKey("cash_count_currencies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    denomination: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    block: Mapped[CashCountCurrency] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint(
            "cash_count_currency_id", "denomination", name="uq_cash_count_line_denomination"
        ),
        CheckConstraint("quantity >= 0", name="ck_cash_count_line_qty_non_negative"),
        CheckConstraint("denomination > 0", name="ck_cash_count_line_denomination_positive"),
        CheckConstraint("kind IN ('billet', 'piece')", name="ck_cash_count_line_kind"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CashCountLine {self.denomination}×{self.quantity}>"
