"""Modèle événementiel MRV — capture déclarative (LOT 3).

Cœur de l'architecture déclarative : le bord **déclare des événements**
(Noon / Departure / Arrival / Begin|End Anchoring) plutôt que de remplir des
formulaires. Tout le reste (distance, temps, conso par deltas de compteurs,
ROB chaîné, cargo MRV) est **dérivé** par ``services.inter_event_compute`` et
n'est jamais ressaisi.

Héritage — *joined-table inheritance* (recommandation spec dashboard §8.1,
dictionnaire §2.3) :

- ``NavEvent`` (table mère ``nav_events``) porte les champs communs + le
  discriminant ``event_type`` ;
- ``NoonEvent`` (table ``nav_event_noon``) ;
- ``PortCallEvent`` (table ``nav_event_portcall``, *polymorphic_abstract*) →
  ``DepartureEvent`` / ``ArrivalEvent`` (identités ``departure`` / ``arrival``,
  **sans table propre** — colonnes propres nullables partagées) ;
- ``AnchoringEvent`` (table ``nav_event_anchoring``, *polymorphic_abstract*) →
  ``BeginAnchoringEvent`` / ``EndAnchoringEvent``.

Chargement polymorphe : ``with_polymorphic="*"`` au niveau de la mère (une
seule requête LEFT JOIN sur les 3 tables filles — pas de N+1) ; les 4 tables
de relevés sont chargées en ``selectin`` (une requête groupée par relation).

Relevés (tables filles de l'**événement**, pas du rapport, dictionnaire §2.3) :

- ``nav_event_engine_readings`` rattachable à **tout** type d'événement
  (relevé instantané de compteur ; le delta est calculé, jamais stocké) ;
- ``nav_event_weather_readings`` / ``nav_event_sail_readings`` /
  ``nav_event_hold_readings`` rattachés au ``NoonEvent`` (clones des tables
  filles du noon report existant ; holds passe de 7 à 9 zones : + sea_water,
  + air ; ``rh_pct`` NULL pour sea_water).

Convention d'unités (plan §2.7) : masses en tonnes, volumes en m³, compteurs
carburant en **litres bruts machine**, heures en h, positions décimales,
UTC calculé. Noms de colonnes suffixés (``_t``, ``_l``, ``_h``, ``_nm``…).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# ════════════════════════════════════════════════════ Vocabulaires (documentés)

# Discriminant polymorphe (dictionnaire §2.3). ``cutoff`` (CDC v0.7, §9.2/§10.1)
# = Year-End Cut-off, requis quand un voyage est en cours au changement
# d'année civile (31/12 24:00 UTC ⇔ 01/01 00:00 UTC) — le règlement MRV impose
# une déclaration bornée par exercice.
EVENT_TYPES: tuple[str, ...] = (
    "noon",
    "departure",
    "arrival",
    "anchoring_begin",
    "anchoring_end",
    "cutoff",
)

# Machine à états déclarative (dictionnaire §2.3, CDC §9.1) — un brouillon est
# EXCLU de tout calcul inter-événements.
EVENT_STATUSES: tuple[str, ...] = ("brouillon", "finalise", "valide")

# Provenance de la position (R05) — Thalos auto ou manuelle justifiée.
POSITION_SOURCES: tuple[str, ...] = ("thalos_auto", "manuel_justifie")

# Condition du navire aux escales (Departure/Arrival). Ballast ⇒ cargo MRV = 0.
VESSEL_CONDITIONS: tuple[str, ...] = ("laden", "ballast")

# Relevés de cale : 2 périodes × 9 zones (7 zones historiques + sea_water + air).
HOLD_PERIODS: tuple[str, ...] = ("minuit", "midi")
HOLD_ZONES: tuple[str, ...] = (
    "sea_water",
    "air",
    "cellar",
    "upper_fwd",
    "middle_fwd",
    "lower_fwd",
    "upper_aft",
    "middle_aft",
    "lower_aft",
)
# Zones sans humidité relative applicable (rh_pct reste NULL).
HOLD_ZONES_WITHOUT_RH: frozenset[str] = frozenset({"sea_water"})

# Créneaux horaires (4 h) des relevés météo / voilure (repris du noon report).
NAV_TIME_SLOTS: tuple[str, ...] = ("16:00", "20:00", "00:00", "04:00", "08:00", "12:00")


# ════════════════════════════════════════════════════════════ Table mère


class NavEvent(Base):
    """Événement de navigation (table mère polymorphe ``nav_events``)."""

    __tablename__ = "nav_events"
    __table_args__ = (
        # Garde anti-doublon souple (IR01) : un seul événement d'un type donné
        # à un instant UTC donné sur un voyage. ``datetime_utc`` NULL (brouillon
        # pas encore finalisé) n'entre pas en conflit (NULLs distincts en PG).
        UniqueConstraint("leg_id", "event_type", "datetime_utc", name="uq_nav_event_leg_type_dt"),
        Index("ix_nav_events_vessel_dt", "vessel_id", "datetime_utc"),
        Index("ix_nav_events_leg", "leg_id"),
        Index("ix_nav_events_status", "status"),
        Index("ix_nav_events_author", "author_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # R02 — voyage obligatoire (= Leg existant, consommé jamais recréé).
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id", ondelete="CASCADE"), nullable=False)
    # Navire : nullable au niveau schéma ; la PRÉSENCE est imposée à la
    # finalisation par la règle R01 (bloquant) — un brouillon peut être
    # incomplet, un événement finalisé non (gate déclaratif).
    vessel_id: Mapped[int | None] = mapped_column(ForeignKey("vessels.id"), nullable=True)

    # Discriminant polymorphe (cf. EVENT_TYPES).
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # Temps : local + tz saisis, UTC calculé (non modifiable — jamais lu du
    # payload). ``datetime_local`` naïf (heure murale dans ``timezone``).
    datetime_local: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    timezone: Mapped[str | None] = mapped_column(String(40))
    datetime_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Position décimale + provenance (R05).
    lat_decimal: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    lon_decimal: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    position_source: Mapped[str | None] = mapped_column(String(20))
    position_justification: Mapped[str | None] = mapped_column(Text)

    # Cargo MRV « deadweight carried » (EU 2016/1928) : saisi directement par
    # le Master (CDC v0.7, G10).
    cargo_mrv_t: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))

    # Cycle de vie (brouillon → finalise → valide).
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="brouillon", server_default="brouillon"
    )
    author_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    # Dédoublonnage PWA offline — UUID généré côté navigateur (idempotence).
    client_uuid: Mapped[str | None] = mapped_column(String(36), unique=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relevés compteurs — rattachables à TOUT type d'événement.
    engine_readings: Mapped[list[NavEventEngineReading]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="NavEventEngineReading.id",
    )

    # ROB par carburant — rattachable à TOUT type d'événement (comme les
    # relevés moteur) ; pour l'instant saisi uniquement au CutoffEvent.
    rob_by_fuel_readings: Mapped[list[NavEventRobByFuel]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="NavEventRobByFuel.id",
    )

    __mapper_args__: ClassVar[dict] = {
        "polymorphic_on": event_type,
        # Une seule requête LEFT JOIN sur les 3 tables filles (pas de N+1).
        "with_polymorphic": "*",
    }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<NavEvent#{self.id} {self.event_type}/{self.status} leg={self.leg_id}>"


# ════════════════════════════════════════════════════════════ NoonEvent


class NoonEvent(NavEvent):
    """Événement Noon (1×/jour en mer) + ses relevés fins (météo/voilure/cales)."""

    __tablename__ = "nav_event_noon"

    id: Mapped[int] = mapped_column(
        ForeignKey("nav_events.id", ondelete="CASCADE"), primary_key=True
    )
    time_from_sosp_h: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    distance_from_sosp_nm: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    distance_to_go_nm: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    announced_eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etb: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 7 paliers ETA (7,0 → 10,0 kt) en JSON — évite 21 colonnes.
    eta_7_to_10kt: Mapped[dict | list | None] = mapped_column(JSON)
    comments: Mapped[str | None] = mapped_column(Text)

    weather_readings: Mapped[list[NavEventWeatherReading]] = relationship(
        back_populates="noon_event",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="NavEventWeatherReading.id",
    )
    sail_readings: Mapped[list[NavEventSailReading]] = relationship(
        back_populates="noon_event",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="NavEventSailReading.id",
    )
    hold_readings: Mapped[list[NavEventHoldReading]] = relationship(
        back_populates="noon_event",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="NavEventHoldReading.id",
    )

    __mapper_args__: ClassVar[dict] = {"polymorphic_identity": "noon"}


# ════════════════════════════════════════════════════════════ PortCallEvent


class PortCallEvent(NavEvent):
    """Escale (abstrait) — Departure/Arrival partagent ``nav_event_portcall``.

    ``rob_t`` (ROB de référence, hiérarchie sources R14-v2) vit ICI, jamais
    porté par le Noon (dictionnaire §2.3, plan §2.2).
    """

    __tablename__ = "nav_event_portcall"

    id: Mapped[int] = mapped_column(
        ForeignKey("nav_events.id", ondelete="CASCADE"), primary_key=True
    )
    draft_fwd_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    draft_aft_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    trim_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    vessel_condition: Mapped[str | None] = mapped_column(String(20))  # cf. VESSEL_CONDITIONS
    # ROB de référence (R14-v2) — source de vérité du ROB déclaré aux escales.
    rob_t: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    # Departure : cargaison B/L + ETD confirmé.
    cargo_bl_t: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    etd_confirmed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Arrival : ETA annoncée + ETB.
    eta_announced: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etb: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __mapper_args__: ClassVar[dict] = {"polymorphic_abstract": True}


class DepartureEvent(PortCallEvent):
    """Départ d'escale (identité ``departure``) — pas de table propre."""

    __mapper_args__: ClassVar[dict] = {"polymorphic_identity": "departure"}


class ArrivalEvent(PortCallEvent):
    """Arrivée à l'escale (identité ``arrival``) — pas de table propre."""

    __mapper_args__: ClassVar[dict] = {"polymorphic_identity": "arrival"}


# ════════════════════════════════════════════════════════════ AnchoringEvent


class AnchoringEvent(NavEvent):
    """Mouillage/dérive (abstrait) — Begin/End partagent ``nav_event_anchoring``."""

    __tablename__ = "nav_event_anchoring"

    id: Mapped[int] = mapped_column(
        ForeignKey("nav_events.id", ondelete="CASCADE"), primary_key=True
    )
    sequence_no: Mapped[int | None] = mapped_column(Integer)  # 1..4 par voyage
    reason: Mapped[str | None] = mapped_column(Text)  # begin (optionnel)
    # Appariement Begin↔End : l'End pointe vers son Begin.
    paired_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("nav_events.id", ondelete="SET NULL")
    )
    duration_h: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))  # end (calculé)

    __mapper_args__: ClassVar[dict] = {
        "polymorphic_abstract": True,
        # ``paired_event_id`` référence aussi ``nav_events.id`` → il faut
        # désambiguïser la condition de jointure d'héritage.
        "inherit_condition": id == NavEvent.id,
    }


class BeginAnchoringEvent(AnchoringEvent):
    """Début de mouillage (identité ``anchoring_begin``)."""

    __mapper_args__: ClassVar[dict] = {"polymorphic_identity": "anchoring_begin"}


class EndAnchoringEvent(AnchoringEvent):
    """Fin de mouillage (identité ``anchoring_end``) — porte ``duration_h``."""

    __mapper_args__: ClassVar[dict] = {"polymorphic_identity": "anchoring_end"}


# ════════════════════════════════════════════════════════════ CutoffEvent


class CutoffEvent(NavEvent):
    """Coupure d'exercice MRV — Year-End Cut-off (identité ``cutoff``).

    CDC v0.7 §9.2/§10.1 : requis quand un voyage est en cours au changement
    d'année civile. Aucune colonne propre — pas de table fille : la position
    et le ``datetime_utc`` communs suffisent, le ROB par carburant vit dans
    ``NavEventRobByFuel`` (rattachable à tout type d'événement, comme les
    relevés moteur). ``datetime_utc`` est **figé côté serveur** exactement à
    l'instant réglementaire (31/12 24:00 UTC ⇔ 01/01 00:00 UTC), jamais dérivé
    du local/tz saisi par le Master — cf. ``event_capture._compute_datetime_utc``,
    qui pin cette valeur pour ce type précisément parce que c'est une règle
    réglementaire fixe, pas une observation de terrain."""

    __mapper_args__: ClassVar[dict] = {"polymorphic_identity": "cutoff"}


# Résolution ``event_type`` → classe concrète (utilisée par event_capture).
EVENT_CLASS_BY_TYPE: dict[str, type[NavEvent]] = {
    "noon": NoonEvent,
    "departure": DepartureEvent,
    "arrival": ArrivalEvent,
    "anchoring_begin": BeginAnchoringEvent,
    "anchoring_end": EndAnchoringEvent,
    "cutoff": CutoffEvent,
}


# ════════════════════════════════════════════════════════════ Relevés


class NavEventEngineReading(Base):
    """Relevé instantané de compteur moteur (litres bruts + heures).

    Rattachable à tout type d'événement. Le delta (conso, heures) est
    **calculé** par ``inter_event_compute`` — jamais stocké en double.
    ``is_counter_reset`` + ``reset_confirmed_by`` tracent la réinitialisation
    légitime (R10 : remplacement/calibrage).
    """

    __tablename__ = "nav_event_engine_readings"
    __table_args__ = (
        Index("ix_nav_engine_readings_event", "event_id"),
        Index("ix_nav_engine_readings_engine", "engine_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("nav_events.id", ondelete="CASCADE"), nullable=False
    )
    engine_id: Mapped[int] = mapped_column(
        ForeignKey("vessel_engines.id", ondelete="CASCADE"), nullable=False
    )
    running_hours_counter_h: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    # Compteur carburant en LITRES bruts machine (natif, CFOTE_05).
    fuel_counter_l: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    is_counter_reset: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    reset_confirmed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reset_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    event: Mapped[NavEvent] = relationship(back_populates="engine_readings")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<NavEventEngineReading event={self.event_id} engine={self.engine_id}>"


class NavEventRobByFuel(Base):
    """ROB déclaré par type de carburant à un événement.

    Rattachable à tout type d'événement (même patron que
    ``NavEventEngineReading``), utilisé pour l'instant uniquement au
    ``CutoffEvent`` (G1) — ancrage de la chaîne ROB (R14/IR02) au même titre
    qu'un ``rob_t`` de Departure/Arrival. ``fuel_type`` reste un texte libre
    (pas d'enum), cohérent avec ``BunkerOperation.fuel_type``."""

    __tablename__ = "nav_event_rob_by_fuel_readings"
    __table_args__ = (Index("ix_nav_rob_by_fuel_event", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("nav_events.id", ondelete="CASCADE"), nullable=False
    )
    fuel_type: Mapped[str] = mapped_column(String(20), nullable=False)
    rob_t: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))

    event: Mapped[NavEvent] = relationship(back_populates="rob_by_fuel_readings")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<NavEventRobByFuel event={self.event_id} fuel={self.fuel_type}>"


class NavEventWeatherReading(Base):
    """Relevé météo (créneau 4 h) d'un NoonEvent — clone de ``noon_report_weather``."""

    __tablename__ = "nav_event_weather_readings"
    __table_args__ = (Index("ix_nav_weather_readings_event", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("nav_events.id", ondelete="CASCADE"), nullable=False
    )
    slot_time: Mapped[str | None] = mapped_column(String(5))  # cf. NAV_TIME_SLOTS
    tws_kn: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))  # True Wind Speed
    awa_deg: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))  # Apparent Wind Angle
    aws_kn: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))  # Apparent Wind Speed
    sea_state: Mapped[int | None] = mapped_column(Integer)
    sea_direction_deg: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    ship_speed_kn: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))

    noon_event: Mapped[NoonEvent] = relationship(back_populates="weather_readings")


class NavEventSailReading(Base):
    """Relevé voilure (créneau 4 h) d'un NoonEvent — clone de ``noon_report_sails``."""

    __tablename__ = "nav_event_sail_readings"
    __table_args__ = (Index("ix_nav_sail_readings_event", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("nav_events.id", ondelete="CASCADE"), nullable=False
    )
    slot_time: Mapped[str | None] = mapped_column(String(5))
    j0: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fwd_j1: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fwd_ms: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    aft_j1: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    aft_ms: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sail_boost_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    me_ps_load_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))  # ME port-side load %
    me_sb_load_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))  # ME starboard load %

    noon_event: Mapped[NoonEvent] = relationship(back_populates="sail_readings")


class NavEventHoldReading(Base):
    """Relevé température/humidité d'une cale — clone étendu de ``noon_report_holds``.

    Passe de 7 à 9 zones (ajout sea_water + air). Format « long » (une ligne
    par période × zone), aligné sur le modèle cible (dictionnaire §2.3).
    ``rh_pct`` NULL pour sea_water (non applicable).
    """

    __tablename__ = "nav_event_hold_readings"
    __table_args__ = (Index("ix_nav_hold_readings_event", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("nav_events.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[str | None] = mapped_column(String(10))  # cf. HOLD_PERIODS
    zone: Mapped[str | None] = mapped_column(String(20))  # cf. HOLD_ZONES
    temp_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    rh_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))  # NULL pour sea_water

    noon_event: Mapped[NoonEvent] = relationship(back_populates="hold_readings")
