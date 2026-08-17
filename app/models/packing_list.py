"""Packing lists — internal & client portal (token-based access).

Pour chaque commande, l'expéditeur remplit en ligne sa packing list à
travers un portail public protégé par token (validité 90 jours). En
interne, l'armateur consulte, audite, verrouille et génère le Bill of
Lading + Arrival Notice.

Workflow status :
  draft → submitted → locked

Lien public : `/p/{token}` — UUID hex tronqué à 24 caractères.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from datetime import date as dt_date
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.booking import Booking

TOKEN_VALIDITY_DAYS = 90

# CARGO-02 — champs requis pour une packing list « complète » (mentions
# obligatoires du connaissement). Sert au calcul de ``completion_pct``.
_BATCH_REQUIRED_FIELDS: tuple[str, ...] = (
    "shipper_name",
    "shipper_address",
    "shipper_city",
    "shipper_country",
    "consignee_name",
    "consignee_address",
    "consignee_city",
    "consignee_country",
    "type_of_goods",
    "pallet_count",
    "weight_kg",
)


def generate_token() -> str:
    return uuid.uuid4().hex[:24]


def default_token_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=TOKEN_VALIDITY_DAYS)


class PackingList(Base):
    __tablename__ = "packing_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Une packing list provient soit d'une commande (rail A, remplissage
    # opérateur), soit d'un booking client (rail B, remplissage via portail).
    # Exactement l'une des deux FK est renseignée (cf. CheckConstraint XOR).
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("commercial_orders.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # COM-11 — leg d'origine épinglé à la création. Stabilise la résolution
    # PL/BL : une commande ventilée multi-legs (dont ``order.leg_id`` peut
    # basculer après réaffectation partielle) garde sa PL rattachée à son leg.
    # NULL (lignes héritées) ⇒ repli dynamique sur ``order/booking.leg_id``.
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"), index=True)
    token: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, default=generate_token, index=True
    )
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=default_token_expiry
    )
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # Date de chargement prévue (= ETD du leg). Alimente la cascade de dates
    # (cf. services/date_cascade) quand l'ETD du leg est décalé.
    loading_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    batches: Mapped[list[PackingListBatch]] = relationship(
        back_populates="packing_list",
        cascade="all, delete-orphan",
        order_by="PackingListBatch.id",
    )
    # Rail B : booking source de la packing list. Relation unidirectionnelle
    # (le modèle Booking n'a pas de back-reference) ; lazy par défaut, à
    # eager-loader explicitement (selectinload) dans un contexte async.
    booking: Mapped[Booking | None] = relationship("Booking")

    __table_args__ = (
        # XOR : exactement une des deux origines est renseignée. Garantit
        # l'invariant « une PL appartient à une commande OU à un booking ».
        CheckConstraint(
            "(order_id IS NULL) <> (booking_id IS NULL)",
            name="ck_packing_lists_order_xor_booking",
        ),
    )

    @property
    def is_locked(self) -> bool:
        return self.status == "locked"

    @property
    def batch_count(self) -> int:
        return len(self.batches) if self.batches else 0

    @property
    def completion_pct(self) -> int:
        # CARGO-02 — complétude documentaire douanière : moyenne du taux de
        # remplissage des champs requis du connaissement sur tous les batches.
        if not self.batches:
            return 0
        total = len(self.batches) * len(_BATCH_REQUIRED_FIELDS)
        filled = sum(
            1
            for b in self.batches
            for f in _BATCH_REQUIRED_FIELDS
            if (v := getattr(b, f, None)) is not None and str(v).strip()
        )
        return round(100 * filled / total) if total else 0


class PackingListBatch(Base):
    __tablename__ = "packing_list_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    packing_list_id: Mapped[int] = mapped_column(
        ForeignKey("packing_lists.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    batch_number: Mapped[int | None] = mapped_column(Integer)
    pallet_format: Mapped[str] = mapped_column(String(20), default="EPAL", nullable=False)
    pallet_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    hs_code: Mapped[str | None] = mapped_column(String(20))
    weight_kg: Mapped[float | None] = mapped_column(Float)
    cubage_m3: Mapped[float | None] = mapped_column(Float)
    length_cm: Mapped[float | None] = mapped_column(Float)
    width_cm: Mapped[float | None] = mapped_column(Float)
    height_cm: Mapped[float | None] = mapped_column(Float)
    hazardous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    imdg_class: Mapped[str | None] = mapped_column(String(20))
    un_number: Mapped[str | None] = mapped_column(String(10))
    stackable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    marks_and_numbers: Mapped[str | None] = mapped_column(Text)

    # CARGO-02 — parties du connaissement (mentions obligatoires du BL).
    shipper_name: Mapped[str | None] = mapped_column(String(200))
    shipper_address: Mapped[str | None] = mapped_column(Text)
    shipper_postal: Mapped[str | None] = mapped_column(String(20))
    shipper_city: Mapped[str | None] = mapped_column(String(100))
    shipper_country: Mapped[str | None] = mapped_column(String(100))
    notify_name: Mapped[str | None] = mapped_column(String(200))
    notify_address: Mapped[str | None] = mapped_column(Text)
    notify_postal: Mapped[str | None] = mapped_column(String(20))
    notify_city: Mapped[str | None] = mapped_column(String(100))
    notify_country: Mapped[str | None] = mapped_column(String(100))
    consignee_name: Mapped[str | None] = mapped_column(String(200))
    consignee_address: Mapped[str | None] = mapped_column(Text)
    consignee_postal: Mapped[str | None] = mapped_column(String(20))
    consignee_city: Mapped[str | None] = mapped_column(String(100))
    consignee_country: Mapped[str | None] = mapped_column(String(100))

    # CARGO-02 — marchandise (BL / douane).
    type_of_goods: Mapped[str | None] = mapped_column(String(200))
    description_of_goods: Mapped[str | None] = mapped_column(Text)
    # CARGO-13 — champs goods riches (douane / valeur déclarée).
    cases_quantity: Mapped[int | None] = mapped_column(Integer)
    units_per_case: Mapped[int | None] = mapped_column(Integer)
    cargo_value_usd: Mapped[float | None] = mapped_column(Float)

    # CARGO-01 — numérotation Bill of Lading persistante (ex. TUAW_1CFRBR6_001).
    # Unique : interdit deux BL au même numéro (anti-doublon au niveau base).
    bl_number: Mapped[str | None] = mapped_column(String(50), unique=True, index=True)
    bl_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ─── Workflow BL (cf. docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md) ───────
    #
    # Cycle : draft → client_validated → master_signed → final.
    # `NULL` = aucun BL n'a encore été généré pour ce lot.
    #
    # Le point de gel est la SIGNATURE DU COMMANDANT, pas l'émission : avant
    # signature un connaissement n'engage personne, après il engage le
    # transporteur. C'est ce qui permet à l'expéditeur de corriger sa packing
    # list au stade draft — exigence explicite de la demande métier.
    bl_state: Mapped[str | None] = mapped_column(String(20), index=True)

    # Génération du draft — `bl_issued_by_*` comble un trou d'audit : l'émission
    # actuelle ne laisse qu'un horodatage anonyme, sans jamais dire QUI a émis.
    bl_draft_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bl_issued_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    bl_issued_by_name: Mapped[str | None] = mapped_column(String(200))

    # Validation du draft. Deux FK MUTUELLEMENT EXCLUSIVES :
    #  - `bl_client_validated_by_id` : le client titulaire du booking valide
    #    depuis /me (cas normal) ;
    #  - `bl_validated_on_behalf_by_id` : repli quand le booking n'a pas de compte
    #    client (`Booking.client_account_id` est nullable) — un membre du staff
    #    valide POUR SON COMPTE, et c'est tracé comme tel.
    # ⚠️ Jamais de validation silencieuse présentée comme venant du client : la
    # contrainte ci-dessous interdit d'en renseigner les deux à la fois.
    bl_client_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bl_client_validated_by_id: Mapped[int | None] = mapped_column(ForeignKey("client_accounts.id"))
    bl_validated_on_behalf_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # Nom figé à la validation : survit à un renommage ultérieur du compte.
    bl_client_validated_by: Mapped[str | None] = mapped_column(String(200))

    # Signature du commandant — patron décalqué de `SofEvent` (déjà éprouvé dans
    # le dépôt) : le hash SHA-256 du contenu signé détecte toute altération
    # postérieure. Ne pas réinventer un mécanisme de signature.
    bl_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bl_signed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    bl_signed_by_name: Mapped[str | None] = mapped_column(String(200))
    bl_signature_hash: Mapped[str | None] = mapped_column(String(64))

    # Après signature, une correction ne passe plus par l'édition mais par une
    # RÉVISION NUMÉROTÉE qui annule explicitement la précédente — les deux
    # restant tracées, comme l'exige un registre opposable.
    bl_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False, server_default="1")
    bl_superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("packing_list_batches.id"))

    # ── Date de mise à bord (« shipped on board ») — §5.0 ────────────────────
    # La date effective est **dérivée** du dernier jour des opérations RÉELLES de
    # l'escale : elle n'est donc PAS stockée. Ces colonnes ne portent que
    # l'**override** des Opérations, et sa justification.
    #
    # ⚠️ Ne jamais recopier ici la valeur dérivée : la présence de `bl_sob_date`
    # est précisément ce qui distingue « corrigé volontairement » de « pas
    # corrigé ». Une valeur figée sans raison de l'être devient fausse en silence
    # dès que la timeline d'escale bouge.
    #
    # Enjeu : un connaissement antidaté est une fraude documentaire et une
    # exclusion de garantie. D'où la justification exigée EN BASE, pas seulement
    # dans le formulaire.
    bl_sob_date: Mapped[dt_date | None] = mapped_column(Date)
    bl_sob_reason: Mapped[str | None] = mapped_column(Text)
    bl_sob_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    bl_sob_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Le validateur du draft est SOIT le client, SOIT le staff pour son
        # compte — jamais les deux. Contrainte posée EN BASE et non seulement
        # dans le formulaire : c'est ce qui garantit qu'aucune validation ne peut
        # être présentée comme venant du client alors qu'elle vient du staff.
        # Les deux NULL restent permis (aucune validation encore intervenue).
        CheckConstraint(
            "bl_client_validated_by_id IS NULL OR bl_validated_on_behalf_by_id IS NULL",
            name="ck_bl_validator_client_xor_staff",
        ),
        # Une révision commence à 1 et ne décroît jamais.
        CheckConstraint("bl_revision >= 1", name="ck_bl_revision_positive"),
        # §5.0 — « sous justification ». Une date de mise à bord corrigée sans
        # motif est INSTOCKABLE : la contrainte est en base pour qu'aucun chemin
        # d'écriture, présent ou futur, ne puisse contourner l'exigence. Le
        # journal demandé « en cas de contrôle » n'a de valeur que si le motif
        # existe toujours.
        CheckConstraint(
            "bl_sob_date IS NULL OR bl_sob_reason IS NOT NULL",
            name="ck_bl_sob_override_needs_reason",
        ),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    packing_list: Mapped[PackingList] = relationship(back_populates="batches")

    # CARGO-13 — dimensions dérivées (calculées, jamais stockées) : surface au
    # sol, volume et densité surfacique. Formules reprises de la V2.
    @property
    def surface_m2(self) -> float | None:
        if self.length_cm and self.width_cm:
            return round(self.length_cm * self.width_cm / 10_000, 4)
        return None

    @property
    def volume_m3(self) -> float | None:
        if self.length_cm and self.width_cm and self.height_cm:
            return round(self.length_cm * self.width_cm * self.height_cm / 1_000_000, 4)
        return None

    @property
    def density(self) -> float | None:
        surface = self.surface_m2
        if self.weight_kg and surface and surface > 0:
            return round((self.weight_kg / 1000) / surface, 3)
        return None


class PackingListAudit(Base):
    """Trace field-by-field des modifications sur les batches/PL."""

    __tablename__ = "packing_list_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    packing_list_id: Mapped[int] = mapped_column(
        ForeignKey("packing_lists.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    batch_id: Mapped[int | None] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(40), nullable=False)  # 'client' | 'staff'
    actor_name: Mapped[str | None] = mapped_column(String(200))
    field: Mapped[str] = mapped_column(String(60), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class PackingListDocument(Base):
    """Document attaché à une packing list (BL, Arrival Notice, autres pièces)."""

    __tablename__ = "packing_list_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Un document peut être rattaché à une packing list (portail expéditeur)
    # OU directement à un booking (upload client depuis l'espace /me).
    packing_list_id: Mapped[int | None] = mapped_column(
        ForeignKey("packing_lists.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # 'bl' | 'arrival_notice' | 'invoice' | 'customs' | 'msds' | 'other'
    label: Mapped[str | None] = mapped_column(String(200))
    file_path: Mapped[str | None] = mapped_column(String(500))
    file_mime: Mapped[str | None] = mapped_column(String(80))
    uploaded_by: Mapped[str | None] = mapped_column(String(200))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PortalAccessLog(Base):
    """Audit des accès au portail public (token tronqué, jamais en clair)."""

    __tablename__ = "portal_access_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portal_type: Mapped[str] = mapped_column(String(40), default="cargo", nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    packing_list_id: Mapped[int | None] = mapped_column(Integer)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    path: Mapped[str | None] = mapped_column(String(200))
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class PortalMessage(Base):
    """Messagerie bidirectionnelle entre l'armateur et le client cargo."""

    __tablename__ = "portal_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    packing_list_id: Mapped[int] = mapped_column(
        ForeignKey("packing_lists.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender: Mapped[str] = mapped_column(String(20), nullable=False)  # 'client' | 'staff'
    sender_name: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BlDeliveryReceipt(Base):
    """Registre de remise des originaux du connaissement — §5.1.

    > « Normalement, les BLs devraient être téléchargeables dans la plateforme
    > client. L'idéal serait de tracker le timestamp de cette action ou ajouter une
    > case de confirmation de réception côté client. Cette case devrait aussi
    > apparaître pour l'équipe opérations, en mode backup. Si les BLs sont envoyés
    > en papier par exemple, l'équipe opérations pourra confirmer la réception côté
    > client en ajoutant la date et heure de confirmation et moyen (téléphone,
    > mail, etc.) + PJ possible. »

    C'est **exactement** le dispositif dont l'absence exclut la *misdelivery* de la
    couverture P&I : sans registre, le transporteur ne peut pas établir à qui, quand
    et comment il a remis les originaux.

    ## Trois canaux, trois valeurs probantes DIFFÉRENTES

    Elles ne doivent jamais être confondues, et c'est la raison d'être de `channel` :

    - ``download`` — le client a **téléchargé** le document. Preuve d'**accès**, pas
      de réception : un préchargement de lien ou un antivirus de messagerie peut la
      produire. La plus faible.
    - ``client_confirmed`` — le client a **coché** la confirmation de réception.
      Déclaration du client lui-même : la plus forte.
    - ``ops_confirmed`` — les Opérations attestent d'une remise **hors plateforme**
      (papier, coursier…). C'est un **repli**, tracé comme tel, jamais présenté
      comme une déclaration du client — même principe que
      ``bl_validated_on_behalf_by_id``.

    Table **append-only** : on n'écrase pas un événement de remise, on en ajoute un.
    Un registre qui se réécrit ne prouve rien.
    """

    __tablename__ = "bl_delivery_receipts"

    #: Preuve d'accès seulement — jamais de réception.
    CHANNEL_DOWNLOAD = "download"
    #: Déclaration du client : la plus forte.
    CHANNEL_CLIENT = "client_confirmed"
    #: Repli Opérations pour une remise hors plateforme.
    CHANNEL_OPS = "ops_confirmed"

    CHANNELS = (CHANNEL_DOWNLOAD, CHANNEL_CLIENT, CHANNEL_OPS)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("packing_list_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    #: Instant de l'événement. Pour le repli Opérations c'est la date **déclarée**
    #: de la remise réelle, qui peut précéder la saisie — d'où un champ distinct de
    #: `created_at`.
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Moyen de remise (téléphone, mail, coursier, remise en main propre…).
    #: **Obligatoire pour le repli Opérations** : sans le moyen, l'attestation
    #: n'établit rien.
    means: Mapped[str | None] = mapped_column(String(60))
    confirmed_by_client_id: Mapped[int | None] = mapped_column(ForeignKey("client_accounts.id"))
    confirmed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    #: Nom figé au moment de l'événement (survit à un renommage de compte).
    confirmed_by_name: Mapped[str | None] = mapped_column(String(200))
    attachment_path: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "channel IN ('download', 'client_confirmed', 'ops_confirmed')",
            name="ck_bl_receipt_channel",
        ),
        # Le confirmateur est SOIT le client, SOIT le staff — jamais les deux.
        # Posé en base pour qu'une attestation du staff ne puisse jamais être
        # présentée comme une déclaration du client.
        CheckConstraint(
            "confirmed_by_client_id IS NULL OR confirmed_by_user_id IS NULL",
            name="ck_bl_receipt_confirmer_client_xor_staff",
        ),
        # Le repli Opérations sans moyen de remise n'établit rien : il est
        # instockable. C'est la seule contrainte qui donne sa valeur au registre
        # face à un assureur.
        CheckConstraint(
            "channel <> 'ops_confirmed' OR means IS NOT NULL",
            name="ck_bl_receipt_ops_needs_means",
        ),
    )


class BlNumberSequence(Base):
    """Séquence de numéros de connaissement, **par voyage** et strictement croissante.

    ## Le défaut que cette table corrige

    Le numéro était calculé comme *nombre de BL déjà émis sur le leg + 1*. Deux
    conséquences, toutes deux graves sur un registre opposable :

    1. **recyclage** — supprimer un lot fait baisser le compteur, et le numéro
       suivant réattribue un numéro **déjà consommé**. Deux documents différents
       peuvent alors porter le même numéro à des moments différents de l'histoire ;
    2. **blocage** — si le lot supprimé n'était pas le dernier (numéros 001, 002, 003
       avec 002 supprimé), le compteur vaut 2, le code retente 003, entre en collision
       avec l'unicité et **échoue après 5 tentatives**. L'émission devient impossible.

    ## La règle

    ``last_seq`` ne **décroît jamais** — aucun chemin d'écriture ne le décrémente.
    Les trous dans la numérotation sont donc **normaux et attendus** : ils sont la
    trace d'un numéro consommé puis abandonné, ce qui est exactement ce qu'un
    registre doit conserver.

    À la création de la ligne, le compteur est amorcé sur le **plus grand suffixe
    déjà émis** pour ce voyage, et non sur leur nombre : c'est ce qui évite de
    recycler dès la première émission sur un voyage historique.
    """

    __tablename__ = "bl_number_sequences"

    #: Un voyage = une séquence. Le `leg_code` est le préfixe du numéro, donc deux
    #: voyages ne peuvent pas se marcher dessus.
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id", ondelete="CASCADE"), primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        # Un compteur négatif n'a aucun sens et signalerait une décrémentation —
        # précisément ce que cette table interdit.
        CheckConstraint("last_seq >= 0", name="ck_bl_sequence_non_negative"),
    )
