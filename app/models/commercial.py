"""Commercial — clients, rate grids, offers, orders.

Reprises de la V3.0.0 ("charte Nouvelle Étoile") pour rouvrir le pipeline
commercial complet : grilles tarifaires dégressives par bracket de
volume, génération d'offres, conversion en commandes, intégration
Pipedrive (pipedrive_org_id, pipedrive_deal_id).

Identifiants de référence :
- Client       — id auto
- RateGrid     — `RG-YYYY-NNNN`
- RateOffer    — `RO-YYYY-NNNN`
- Order        — `ORD-YYYY-NNNN`

Brackets dégressifs (DEFAULT_BRACKETS_SHIPPER) :
  lt50 (×1.10), 100 (×1.00), 200 (×0.80), 300 (×0.80), 400 (×0.80),
  500 (×0.70), full ship 978 (×0.60).
"""

from __future__ import annotations

import json
from datetime import date as _date
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.user import User

CLIENT_TYPES = ("freight_forwarder", "shipper")
RATE_GRID_STATUSES = ("draft", "active", "expired", "superseded")

# Comptes-ancres (P11) — statut de co-branding d'un partenariat stratégique.
# none    — pas de co-branding ;
# pending — co-branding en discussion / à valider ;
# active  — co-branding actif (assets co-signés, page kit, etc.).
CO_BRANDING_STATUSES = ("none", "pending", "active")
CO_BRANDING_STATUS_LABELS: dict[str, str] = {
    "none": "Aucun",
    "pending": "En discussion",
    "active": "Actif",
}
# Rang de priorité capacité (allocation en cale des comptes-ancres) : 0 =
# standard (aucune priorité) ; plus le rang est élevé, plus le compte est
# servi en priorité lorsque la cale est contrainte.
CAPACITY_PRIORITY_LABELS: dict[int, str] = {
    0: "Standard",
    1: "Prioritaire",
    2: "Stratégique",
}


def capacity_priority_label(rank: int | None) -> str:
    """Libellé du rang de priorité capacité (repli « Rang N » hors barème)."""
    if rank is None:
        return CAPACITY_PRIORITY_LABELS[0]
    return CAPACITY_PRIORITY_LABELS.get(int(rank), f"Rang {int(rank)}")


RATE_OFFER_STATUSES = ("draft", "sent", "accepted", "declined", "expired")
ORDER_STATUSES = ("draft", "confirmed", "loaded", "delivered", "cancelled")

# Conditions de règlement d'une grille — déclencheurs métier (cf.
# ``RateGridPaymentTerm``). Purement déclaratifs : la facturation du fret est
# hors plateforme (virement bancaire), ils décrivent le contrat, ils ne
# déclenchent aucune écriture comptable.
PAYMENT_TRIGGERS = ("before_loading", "before_discharge", "days_before_etd")
PAYMENT_TRIGGER_LABELS: dict[str, str] = {
    "before_loading": "Avant le chargement",
    "before_discharge": "Avant le déchargement",
    "days_before_etd": "X jours avant le départ du navire",
}
# Une grille prévoit d'un à trois règlements.
MAX_PAYMENT_TERMS = 3

# Unités de tarification des options de grille.
# per_palette      — appliqué × nombre de palettes (ex. manutention)
# per_tonne        — appliqué × tonnage chargé
# per_booking      — forfait par commande / réservation
# per_bl           — forfait par connaissement (BL) émis
# per_booking_note — forfait par booking note émise (frais documentaires)
RATE_OPTION_UNITS = (
    "per_palette",
    "per_tonne",
    "per_booking",
    "per_bl",
    "per_booking_note",
)

RATE_OPTION_UNIT_LABELS: dict[str, str] = {
    "per_palette": "par palette",
    "per_tonne": "par tonne chargée",
    "per_booking": "à la commande",
    "per_bl": "au connaissement (BL)",
    "per_booking_note": "par booking note",
}


# Brackets dégressifs (volume → coefficient).
#
# Barème métier NEWTOWT : les paliers sont **inclusifs des deux côtés**
# (« de 50 à 100 » couvre 50 et 100). ``max_qty`` porte la borne haute incluse ;
# la borne basse est implicitement ``max_qty`` du palier précédent + 1.
# Les coefficients sont paramétrables par grille (``brackets_json``) — ceux-ci
# ne sont que la proposition par défaut à la création.
DEFAULT_BRACKETS_SHIPPER: list[dict] = [
    {"key": "lt50", "label": "Moins de 50 palettes", "max_qty": 49, "coeff": 1.10},
    {"key": "50_100", "label": "De 50 à 100 palettes", "max_qty": 100, "coeff": 1.00},
    {"key": "100_300", "label": "De 100 à 300 palettes", "max_qty": 300, "coeff": 0.90},
    {"key": "300_500", "label": "De 300 à 500 palettes", "max_qty": 500, "coeff": 0.80},
    {"key": "500_800", "label": "De 500 à 800 palettes", "max_qty": 800, "coeff": 0.70},
    {"key": "full", "label": "Navire complet", "max_qty": None, "coeff": 0.60},
]

DEFAULT_BRACKETS_FF: list[dict] = [
    {"key": "flat", "label": "Tarif unique", "max_qty": 850, "coeff": 1.00},
]

PALETTE_COEFFICIENTS: dict[str, float] = {
    "EPAL": 1.00,
    "USPAL": 1.20,
    "PORTPAL": 1.20,
    "IBC": 1.30,
    "BIGBAG": 1.25,
    "BARRIQUE120": 1.50,
    "BARRIQUE140": 2.00,
}


class Client(Base):
    """Commercial customer — Freight Forwarder or direct Shipper."""

    __tablename__ = "commercial_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    client_type: Mapped[str] = mapped_column(String(30), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(200))
    contact_phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(2))
    vat_number: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # ── Compte-ancre (P11) ────────────────────────────────────────────────
    # Un « compte-ancre » est un partenaire stratégique qui sécurise le
    # remplissage : il s'engage sur un volume annuel, bénéficie d'une priorité
    # d'allocation de cale et peut faire l'objet d'un co-branding. Ces attributs
    # sont portés par le référentiel commercial (Client) — la grille tarifaire
    # négociée s'y rattache déjà, tout comme l'engagement de volume par commande.
    is_anchor: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True, server_default="false"
    )
    # Engagement de volume annuel (palettes/an) — NULL = non renseigné.
    annual_volume_commitment: Mapped[int | None] = mapped_column(Integer)
    # Rang de priorité capacité (0 = standard, cf. CAPACITY_PRIORITY_LABELS).
    capacity_priority: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    # Statut de co-branding (cf. CO_BRANDING_STATUSES).
    co_branding_status: Mapped[str] = mapped_column(
        String(20), default="none", nullable=False, server_default="none"
    )
    pipedrive_org_id: Mapped[int | None] = mapped_column(Integer)
    # ── Commercial attitré ────────────────────────────────────────────────
    # Chaque client a un commercial référent : c'est lui qu'on notifie d'une
    # estimation, et lui qui porte la relation. Renseigné à l'import Pipedrive
    # (``owner_id`` de l'organisation, rapproché par e-mail sur les comptes
    # staff) **ou** à la main depuis la fiche client ; la saisie manuelle fait
    # foi — un import ultérieur ne l'écrase jamais (cf. ``pipedrive_sync``).
    assigned_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    # Propriétaire côté Pipedrive, conservé pour le rapprochement (jamais
    # utilisé seul comme identité : un owner Pipedrive n'est pas un compte staff).
    pipedrive_owner_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @property
    def capacity_priority_display(self) -> str:
        """Libellé lisible du rang de priorité capacité."""
        return capacity_priority_label(self.capacity_priority)

    @property
    def co_branding_label(self) -> str:
        """Libellé lisible du statut de co-branding."""
        return CO_BRANDING_STATUS_LABELS.get(self.co_branding_status, self.co_branding_status)

    assigned_user: Mapped[User | None] = relationship(
        "User", foreign_keys=[assigned_user_id], lazy="selectin"
    )
    rate_grids: Mapped[list[RateGrid]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    rate_offers: Mapped[list[RateOffer]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    orders: Mapped[list[Order]] = relationship(back_populates="client")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Client {self.name} ({self.client_type})>"


class RateGrid(Base):
    """Grille tarifaire = 1 client (ou défaut) + 1 période + N routes.

    Modèle multi-routes (Module 6) :
    - l'**en-tête** porte le client (ou ``is_default``), la période, l'index
      d'ajustement, les forfaits documentaires (BL / booking), le paramétrage
      fin (IMDG, minimum de facturation, engagement de volume) et les
      **brackets de volume** (``brackets_json`` — coefficients dégressifs au
      niveau grille, remplace les anciennes lignes-brackets) ;
    - chaque **route** (``RateGridLine``) porte POL/POD, sa distance, son OPEX
      jour et son ``base_rate`` (OPEX × jours de mer / 978) ;
    - ``vessel_id`` sert au lookup de l'OPEX jour par navire au recalcul.

    Deux familles : grille **client** (``client_id`` renseigné) ou grille
    **par défaut** (``client_id`` NULL, ``is_default=True``) — repli pour tout
    demandeur inconnu, une seule active, multi-routes.
    """

    __tablename__ = "rate_grids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("commercial_clients.id"), nullable=True, index=True
    )
    # Navire de référence — sert au lookup de l'OPEX jour lors du recalcul des
    # routes (sinon paramètre global opex_daily_sea, sinon repli).
    vessel_id: Mapped[int | None] = mapped_column(
        ForeignKey("vessels.id"), nullable=True, index=True
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    valid_from: Mapped[_date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[_date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    adjustment_index: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), default=Decimal("1.0000"), nullable=False
    )
    # Forfaits documentaires (sucre au-dessus des options) — repris au devis.
    bl_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    booking_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # Paramétrage fin (NULL = défaut global / pas de minimum) :
    # - surcharge marchandises dangereuses (IMDG) en points de % (ex. 25.00) ;
    # - minimum de facturation appliqué au total du devis.
    hazardous_surcharge_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    min_charge_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # Engagement minimum de volume (palettes/commande) — grilles shipper (Module 6).
    volume_commitment: Mapped[int | None] = mapped_column(Integer)
    # Brackets de volume (coefficients dégressifs) au niveau grille — JSON
    # liste de {key,label,max_qty,coeff}. NULL = défaut shipper (cf. brackets).
    brackets_json: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    client: Mapped[Client | None] = relationship(back_populates="rate_grids")
    lines: Mapped[list[RateGridLine]] = relationship(
        back_populates="grid", cascade="all, delete-orphan", order_by="RateGridLine.id"
    )
    options: Mapped[list[RateGridOption]] = relationship(
        back_populates="grid", cascade="all, delete-orphan", order_by="RateGridOption.id"
    )
    payment_terms: Mapped[list[RateGridPaymentTerm]] = relationship(
        back_populates="grid",
        cascade="all, delete-orphan",
        order_by="RateGridPaymentTerm.position",
    )

    @property
    def brackets(self) -> list[dict]:
        """Brackets de volume (depuis ``brackets_json``) — défaut shipper si absent."""
        if self.brackets_json:
            try:
                data = json.loads(self.brackets_json)
            except (ValueError, TypeError):
                data = None
            if isinstance(data, list) and data:
                return data
        return list(DEFAULT_BRACKETS_SHIPPER)


class RateGridLine(Base):
    """Une route POL→POD d'une grille (distance / OPEX jour / base_rate).

    ``nav_days = distance_nm / (8 nœuds × 24 h)`` et
    ``base_rate = opex_daily × nav_days / 978``. ``is_manual`` gèle le
    ``base_rate`` (surcharge manuelle) : le recalcul OPEX ne le réécrit pas.
    """

    __tablename__ = "rate_grid_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grid_id: Mapped[int] = mapped_column(
        ForeignKey("rate_grids.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pol_locode: Mapped[str] = mapped_column(String(5), nullable=False, index=True)
    pod_locode: Mapped[str] = mapped_column(String(5), nullable=False, index=True)
    # Route rattachée à un leg type (optionnel — distance reprise du leg).
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"), nullable=True)
    distance_nm: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    nav_days: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    opex_daily: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    base_rate: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Référence tarifaire codifiée « P-MMAA-MMAA-XX-YY » (cf.
    # ``services.commercial.route_tariff_reference``). Portée par la **route** et
    # non par l'en-tête : la codification encode une paire POL/POD, alors qu'une
    # grille en couvre plusieurs. Recalculée quand la période ou la route change.
    tariff_reference: Mapped[str | None] = mapped_column(String(32), index=True)
    # Grille par défaut du client **sur cette route** : celle retenue quand
    # plusieurs grilles actives couvrent la même paire POL/POD à la date d'ETD.
    is_route_default: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

    grid: Mapped[RateGrid] = relationship(back_populates="lines")


class RateGridOption(Base):
    """Option tarifaire d'une grille (coûts annexes au fret).

    Exemples : manutention par palette, contribution à la tonne chargée,
    forfait par réservation, frais de booking note. Les options actives
    sont reprises dans tout devis généré sur la grille.
    """

    __tablename__ = "rate_grid_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grid_id: Mapped[int] = mapped_column(
        ForeignKey("rate_grids.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)  # RATE_OPTION_UNITS
    amount_eur: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    grid: Mapped[RateGrid] = relationship(back_populates="options")


class RateGridPaymentTerm(Base):
    """Échéance de règlement d'une grille tarifaire (1 à 3 par grille).

    Les conditions de règlement sont **déclaratives** : elles décrivent ce qui
    a été négocié et sont reprises telles quelles sur la booking note. Elles ne
    déclenchent aucune facturation — la facturation du fret est hors plateforme
    (virement bancaire, comptabilité Pennylane), décision d'arbitrage A5.

    Trois déclencheurs métier (``PAYMENT_TRIGGERS``) :

    * ``before_loading``   — avant le chargement ;
    * ``before_discharge`` — avant le déchargement ;
    * ``days_before_etd``  — X jours avant le départ du navire (``offset_days``).
    """

    __tablename__ = "rate_grid_payment_terms"
    __table_args__ = (
        UniqueConstraint("grid_id", "position", name="uq_grid_payment_term_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grid_id: Mapped[int] = mapped_column(
        ForeignKey("rate_grids.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Rang d'affichage / d'échelonnement (1 à MAX_PAYMENT_TERMS).
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(String(30), nullable=False)  # PAYMENT_TRIGGERS
    # Nombre de jours avant l'ETD — requis pour ``days_before_etd`` uniquement.
    offset_days: Mapped[int | None] = mapped_column(Integer)
    # Part de la facture à régler à cette échéance (0 < pct <= 100).
    percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120))

    grid: Mapped[RateGrid] = relationship(back_populates="payment_terms")

    @property
    def trigger_label(self) -> str:
        """Libellé lisible de l'échéance (avec le nombre de jours si applicable)."""
        if self.trigger == "days_before_etd":
            days = self.offset_days if self.offset_days is not None else "X"
            return f"{days} jours avant le départ du navire"
        return PAYMENT_TRIGGER_LABELS.get(self.trigger, self.trigger)


class RateOffer(Base):
    """Offre commerciale envoyée à un client (DOCX gen. possible)."""

    __tablename__ = "rate_offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("commercial_clients.id"), nullable=False, index=True
    )
    grid_id: Mapped[int | None] = mapped_column(ForeignKey("rate_grids.id"))
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    estimated_palettes: Mapped[int | None] = mapped_column(Integer)
    proposed_rate_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    total_eur: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    valid_until: Mapped[_date | None] = mapped_column(Date)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    pipedrive_deal_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    client: Mapped[Client] = relationship(back_populates="rate_offers")


class RateOfferRevision(Base):
    """Historique **append-only** d'une offre commerciale.

    Toute modification d'une offre est tracée ici : c'est la pièce mobilisable si
    un client conteste un prix. Trois niveaux d'information, complémentaires :

    * ``changes_json`` — le **diff champ par champ** (ancienne et nouvelle
      valeur), qui répond à « qu'est-ce qui a changé exactement ? » ;
    * ``snapshot_json`` — l'**état complet** de l'offre après la modification, qui
      permet de rejouer ce qui a été proposé sans reconstituer le diff ;
    * ``content_hash`` / ``previous_hash`` — un **chaînage SHA-256** : altérer ou
      retirer une révision casse la chaîne des suivantes, ce qui rend la
      falsification détectable au lieu d'être indécelable.

    Cette table complète ``activity_logs`` sans le remplacer : le journal
    d'activité dit « qui a agi », l'historique dit « ce que valait l'offre ».
    Elle est exclue de la purge administrative (``RETENTION_ONLY_PURGE_TABLES``
    ne suffirait pas : elle n'est pas purgeable du tout).

    Aucune route ne modifie ni ne supprime une révision — l'insertion est le seul
    mode d'écriture.
    """

    __tablename__ = "rate_offer_revisions"
    __table_args__ = (
        UniqueConstraint("offer_id", "sequence", name="uq_rate_offer_revision_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    offer_id: Mapped[int] = mapped_column(
        ForeignKey("rate_offers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Rang de la révision pour cette offre (1 = création). Contraint unique :
    # deux révisions ne peuvent pas revendiquer la même place dans la chaîne.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    actor_name: Mapped[str | None] = mapped_column(String(200))
    actor_role: Mapped[str | None] = mapped_column(String(40))
    # Diff : liste JSON de {field, old, new}. Vide pour une création.
    changes_json: Mapped[str | None] = mapped_column(Text)
    # État complet de l'offre après la modification (JSON canonique).
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    @property
    def changes(self) -> list[dict]:
        """Diff décodé (liste vide si absent ou illisible)."""
        if not self.changes_json:
            return []
        try:
            data = json.loads(self.changes_json)
        except (ValueError, TypeError):
            return []
        return data if isinstance(data, list) else []

    @property
    def snapshot(self) -> dict:
        """État complet décodé (dict vide si illisible)."""
        try:
            data = json.loads(self.snapshot_json or "{}")
        except (ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}


class Order(Base):
    """Commande ferme (issue d'une offre acceptée ou créée directement)."""

    __tablename__ = "commercial_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("commercial_clients.id"), nullable=False, index=True
    )
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("rate_offers.id"))
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"), index=True)
    # Back-link de reprise (B2.2) : quand renseigné, la commande héritée du
    # rail A a été migrée en réservation (rail B). Sert d'idempotence au
    # script de reprise et de dé-doublonnage capacité (la palette est alors
    # comptée via le booking, plus via la commande).
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    booked_palettes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rate_per_palette_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    total_eur: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # COM-02 — caractéristiques riches de la commande (reprise V2).
    palette_format: Mapped[str | None] = mapped_column(String(20))
    weight_per_palette_kg: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    thc_included: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    booking_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    documentation_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # Route souhaitée + fenêtre de livraison — pilotent l'affectation au leg (COM-01).
    departure_locode: Mapped[str | None] = mapped_column(String(5))
    arrival_locode: Mapped[str | None] = mapped_column(String(5))
    delivery_date_start: Mapped[_date | None] = mapped_column(Date)
    delivery_date_end: Mapped[_date | None] = mapped_column(Date)
    # Traçabilité de la grille appliquée (fiche grille → « commandes liées »).
    rate_grid_id: Mapped[int | None] = mapped_column(ForeignKey("rate_grids.id"), index=True)
    rate_grid_line_id: Mapped[int | None] = mapped_column(ForeignKey("rate_grid_lines.id"))
    cargo_description: Mapped[str | None] = mapped_column(Text)
    description_of_goods: Mapped[str | None] = mapped_column(Text)
    # Adresses structurées
    shipper_name: Mapped[str | None] = mapped_column(String(200))
    shipper_address: Mapped[str | None] = mapped_column(Text)
    consignee_name: Mapped[str | None] = mapped_column(String(200))
    consignee_address: Mapped[str | None] = mapped_column(Text)
    notify_name: Mapped[str | None] = mapped_column(String(200))
    notify_address: Mapped[str | None] = mapped_column(Text)
    pipedrive_deal_id: Mapped[int | None] = mapped_column(Integer)
    # COM-04 — pièce jointe (bon de commande / contrat signé). Stockage hors
    # base via services.safe_files ; une seule PJ (remplacée au ré-upload).
    attachment_path: Mapped[str | None] = mapped_column(String(500))
    attachment_filename: Mapped[str | None] = mapped_column(String(255))
    attachment_mime: Mapped[str | None] = mapped_column(String(80))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    client: Mapped[Client] = relationship(back_populates="orders")
    assignments: Mapped[list[OrderAssignment]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderAssignment(Base):
    """Affectation d'une commande à un leg (pour ventiler une ord. sur plusieurs voyages)."""

    __tablename__ = "order_assignments"
    # Une commande ne peut être affectée deux fois au même leg — garde-fou base
    # contre les doublons (concurrence) ; l'unicité applicative ne suffit pas.
    __table_args__ = (UniqueConstraint("order_id", "leg_id", name="uq_order_assignment_order_leg"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("commercial_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, index=True)
    palettes_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pallet_format: Mapped[str] = mapped_column(String(20), default="EPAL", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    order: Mapped[Order] = relationship(back_populates="assignments")
