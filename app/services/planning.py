"""Planning service — create / update legs + cascade across the same vessel.

When an upstream leg's ETD or ETA shifts, all downstream legs that haven't
sailed yet (ATD null) are shifted by the same delta. This is the conservative
behaviour: it preserves the relative scheduling humans set, doesn't try to
guess transit times. Recompute-from-distance can come in V3.1.

Bookings of impacted legs do not need date updates themselves — they FK to
the leg, so reading ``booking.leg.etd`` reflects the new value. Notifications
to impacted clients are emitted by NotificationService (V3.1).
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import Leg
from app.models.planning_share import PlanningShare


class PlanningError(Exception):
    """Base planning error."""


class InvalidLegDates(PlanningError):
    pass


class LegOverlap(PlanningError):
    """Le navire a déjà un leg qui chevauche cette plage horaire."""


class LegContinuityError(PlanningError):
    """Rupture de continuité géographique (POD du leg précédent ≠ POL)."""


class LegSpeedIncoherent(PlanningError):
    """Durée incohérente avec la distance et la vitesse plausible."""


# Vitesse max physiquement plausible pour un voilier-cargo NEWTOWT (kn).
# Au-delà, la durée saisie est forcément une erreur (typo de date).
MAX_PLAUSIBLE_SPEED_KN = 18.0


@dataclass(frozen=True)
class CascadeReport:
    leg_id: int
    delta: timedelta
    impacted_leg_ids: list[int]

    @property
    def delta_hours(self) -> float:
        return self.delta.total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_dates(etd: datetime, eta: datetime) -> None:
    if etd >= eta:
        raise InvalidLegDates("ETD must be strictly before ETA")
    if (eta - etd) > timedelta(days=180):
        raise InvalidLegDates("Leg duration cannot exceed 180 days")


async def validate_leg_schedule(
    db: AsyncSession,
    *,
    vessel_id: int,
    departure_port_id: int,
    arrival_port_id: int,
    etd: datetime,
    eta: datetime,
    transit_speed_kn: float | None = None,
    elongation_coef: float | None = None,
    exclude_leg_id: int | None = None,
) -> list[str]:
    """Contrôles d'intégrité d'un leg avant create/update.

    Lève PlanningError (sous-classe) sur violation dure :
      - **Chevauchement navire** : deux legs du même navire ne peuvent
        pas se chevaucher dans le temps (un navire = un seul leg à la fois).
      - **Continuité géographique** : le POD du leg précédent (même navire)
        doit être le POL de ce leg, et le POL du leg suivant doit être ce POD.
      - **Cohérence vitesse/distance** : la durée ne peut impliquer une
        vitesse > MAX_PLAUSIBLE_SPEED_KN, ni > 1.5× la vitesse planifiée.

    Renvoie une liste d'**avertissements** non bloquants (ex. continuité
    douteuse mais tolérée). Les violations dures lèvent.
    """
    from app.models.port import Port
    from app.services.ports import haversine_nm

    warnings: list[str] = []

    # ── 1. Chevauchement temporel sur le même navire ──────────────────
    overlap_stmt = (
        select(Leg).where(Leg.vessel_id == vessel_id).where(Leg.etd < eta).where(Leg.eta > etd)
    )
    if exclude_leg_id is not None:
        overlap_stmt = overlap_stmt.where(Leg.id != exclude_leg_id)
    clash = (await db.execute(overlap_stmt.limit(1))).scalar_one_or_none()
    if clash is not None:
        raise LegOverlap(
            f"Chevauchement : le navire a déjà le leg {clash.leg_code} "
            f"({clash.etd:%Y-%m-%d %H:%M} → {clash.eta:%Y-%m-%d %H:%M}). "
            f"Un navire ne peut pas effectuer deux legs simultanément."
        )

    # ── 2. Continuité géographique (leg précédent / suivant) ──────────
    prev_stmt = (
        select(Leg)
        .where(Leg.vessel_id == vessel_id)
        .where(Leg.etd < etd)
        .order_by(Leg.etd.desc())
        .limit(1)
    )
    if exclude_leg_id is not None:
        prev_stmt = prev_stmt.where(Leg.id != exclude_leg_id)
    prev = (await db.execute(prev_stmt)).scalar_one_or_none()
    if prev is not None and prev.arrival_port_id != departure_port_id:
        raise LegContinuityError(
            f"Rupture de continuité : le leg précédent {prev.leg_code} arrive "
            f"à un autre port que le port de départ de ce leg. Le navire doit "
            f"partir d'où il est arrivé."
        )

    next_stmt = (
        select(Leg)
        .where(Leg.vessel_id == vessel_id)
        .where(Leg.etd > etd)
        .order_by(Leg.etd.asc())
        .limit(1)
    )
    if exclude_leg_id is not None:
        next_stmt = next_stmt.where(Leg.id != exclude_leg_id)
    nxt = (await db.execute(next_stmt)).scalar_one_or_none()
    if nxt is not None and nxt.departure_port_id != arrival_port_id:
        raise LegContinuityError(
            f"Rupture de continuité : le leg suivant {nxt.leg_code} part d'un "
            f"autre port que le port d'arrivée de ce leg."
        )

    # ── 3. Cohérence durée / distance / vitesse ───────────────────────
    pol = await db.get(Port, departure_port_id)
    pod = await db.get(Port, arrival_port_id)
    duration_h = (eta - etd).total_seconds() / 3600.0
    if (
        pol
        and pod
        and duration_h > 0
        and pol.latitude is not None
        and pol.longitude is not None
        and pod.latitude is not None
        and pod.longitude is not None
    ):
        gc_nm = haversine_nm(pol.latitude, pol.longitude, pod.latitude, pod.longitude)
        distance_nm = gc_nm * (elongation_coef or 1.0)
        implied_kn = distance_nm / duration_h
        if implied_kn > MAX_PLAUSIBLE_SPEED_KN:
            raise LegSpeedIncoherent(
                f"Durée incohérente : {distance_nm:.0f} NM en {duration_h:.0f} h "
                f"implique {implied_kn:.1f} kn (> {MAX_PLAUSIBLE_SPEED_KN:.0f} kn "
                f"physiquement impossible). Vérifiez l'ETD/ETA."
            )
        if transit_speed_kn and implied_kn > transit_speed_kn * 1.5:
            warnings.append(
                f"La durée implique {implied_kn:.1f} kn, soit bien plus que la "
                f"vitesse planifiée ({transit_speed_kn:.1f} kn)."
            )
    return warnings


def _leg_code_for(
    vessel_code: str,
    pol_country: str,
    pod_country: str,
    etd: datetime,
    sequence: int = 1,
) -> str:
    """Génère le leg_code : ``{seq}{vessel_code}{POL}{POD}{year_digit}``

    ``seq`` est la position numérique du leg dans l'année pour ce navire
    (1 = 1er leg de l'année, 2 = 2ème, …). ``year_digit`` est le dernier
    chiffre de l'année de l'ETD.
    Ex. ``1CFRBR6`` (1er leg du navire C en 2026, FR→BR).
    """
    year_last_digit = str(etd.year)[-1]
    return (
        f"{sequence}"
        f"{vessel_code}"
        f"{pol_country.upper()[:2]}"
        f"{pod_country.upper()[:2]}"
        f"{year_last_digit}"
    )


# ---------------------------------------------------------------------------
# Create / Update / Delete
# ---------------------------------------------------------------------------


BOOKING_CLOSE_LEAD_HOURS = 48
"""Auto-cloture des réservations : ETD - 48h si le staff ne précise rien."""


async def create_leg(
    db: AsyncSession,
    *,
    vessel_id: int,
    departure_port_id: int,
    arrival_port_id: int,
    etd: datetime,
    eta: datetime,
    is_bookable: bool = False,
    public_capacity_palettes: int | None = None,
    public_price_per_palette_eur: Decimal | None = None,
    booking_close_at: datetime | None = None,
    leg_code: str | None = None,
    transit_speed_kn: float | None = None,
    elongation_coef: float | None = None,
    port_stay_planned_hours: int | None = None,
) -> Leg:
    validate_dates(etd, eta)
    if departure_port_id == arrival_port_id:
        raise InvalidLegDates("Departure and arrival ports must differ")

    # Contrôles d'intégrité : chevauchement navire, continuité ports,
    # cohérence vitesse/distance (lève PlanningError si violation dure).
    await validate_leg_schedule(
        db,
        vessel_id=vessel_id,
        departure_port_id=departure_port_id,
        arrival_port_id=arrival_port_id,
        etd=etd,
        eta=eta,
        transit_speed_kn=transit_speed_kn,
        elongation_coef=elongation_coef,
    )

    # Auto-cloture des réservations à ETD - 48h si non précisé par le staff.
    if booking_close_at is None:
        booking_close_at = etd - timedelta(hours=BOOKING_CLOSE_LEAD_HOURS)

    # Garde : un leg réservable dont la clôture est déjà passée n'est
    # jamais réservable → on refuse plutôt que de créer un leg trompeur.
    if is_bookable and booking_close_at <= datetime.now(UTC):
        raise InvalidLegDates(
            "ETD trop proche : la clôture des réservations (ETD − 48 h) est "
            "déjà passée. Décalez l'ETD ou désactivez l'ouverture à la réservation."
        )

    # If leg_code not supplied, derive one (best-effort; admin can edit).
    if leg_code is None:
        from sqlalchemy import func

        from app.models.port import Port
        from app.models.vessel import Vessel

        vessel = await db.get(Vessel, vessel_id)
        pol = await db.get(Port, departure_port_id)
        pod = await db.get(Port, arrival_port_id)
        if not (vessel and pol and pod):
            raise PlanningError("Invalid vessel/port references")

        # Séquence = nombre de legs déjà planifiés pour ce navire dans l'année + 1.
        year_start = datetime(etd.year, 1, 1, tzinfo=UTC)
        year_end = datetime(etd.year, 12, 31, 23, 59, tzinfo=UTC)
        existing_count = (
            await db.scalar(
                select(func.count(Leg.id)).where(
                    Leg.vessel_id == vessel_id,
                    Leg.etd >= year_start,
                    Leg.etd <= year_end,
                )
            )
            or 0
        )
        start_seq = existing_count + 1

        # Cherche un code libre à partir de la séquence calculée.
        leg_code = _leg_code_for(vessel.code, pol.country, pod.country, etd, start_seq)
        for seq in range(start_seq, start_seq + 26):
            candidate = _leg_code_for(vessel.code, pol.country, pod.country, etd, seq)
            existing = (
                await db.execute(select(Leg).where(Leg.leg_code == candidate))
            ).scalar_one_or_none()
            if not existing:
                leg_code = candidate
                break

    leg = Leg(
        leg_code=leg_code,
        vessel_id=vessel_id,
        departure_port_id=departure_port_id,
        arrival_port_id=arrival_port_id,
        etd_ref=etd,
        eta_ref=eta,
        etd=etd,
        eta=eta,
        status="planned",
        is_bookable=is_bookable,
        public_capacity_palettes=public_capacity_palettes,
        public_price_per_palette_eur=public_price_per_palette_eur,
        booking_close_at=booking_close_at,
        transit_speed_kn=transit_speed_kn,
        elongation_coef=elongation_coef,
        port_stay_planned_hours=port_stay_planned_hours,
    )
    db.add(leg)
    await db.flush()
    return leg


async def update_leg(
    db: AsyncSession,
    leg: Leg,
    *,
    vessel_id: int | None = None,
    etd: datetime | None = None,
    eta: datetime | None = None,
    departure_port_id: int | None = None,
    arrival_port_id: int | None = None,
    is_bookable: bool | None = None,
    public_capacity_palettes: int | None = None,
    public_price_per_palette_eur: Decimal | None = None,
    booking_close_at: datetime | None = None,
    transit_speed_kn: float | None = None,
    elongation_coef: float | None = None,
    port_stay_planned_hours: int | None = None,
    cascade: bool = True,
) -> CascadeReport | None:
    """Update a leg in place. If etd shifts and cascade=True, propagate the
    delta to all downstream legs of the same vessel that haven't sailed yet.

    When vessel_id, departure_port_id, arrival_port_id ou etd's year change,
    the leg_code is recomputed par ``_leg_code_for`` — format canonique
    ``{seq}{vessel_code}{POL}{POD}{year_digit}`` (ex. ``1CFRBR6``).

    Returns the CascadeReport, ou None si aucune cascade n'a été effectuée.
    """
    from app.models.port import Port
    from app.models.vessel import Vessel

    new_etd = etd or leg.etd
    new_eta = eta or leg.eta
    validate_dates(new_etd, new_eta)

    delta = new_etd - leg.etd
    # Capture old reference points BEFORE applying changes. La frontière de
    # cascade (ancien ETD) est reconstituée dans date_cascade via leg.etd-delta.
    old_vessel_id = leg.vessel_id
    old_pol_id = leg.departure_port_id
    old_pod_id = leg.arrival_port_id
    old_year_digit = str(leg.etd.year)[-1]

    leg.etd = new_etd
    leg.eta = new_eta

    if vessel_id is not None:
        leg.vessel_id = vessel_id
    if departure_port_id is not None:
        leg.departure_port_id = departure_port_id
    if arrival_port_id is not None:
        leg.arrival_port_id = arrival_port_id
    if leg.departure_port_id == leg.arrival_port_id:
        raise InvalidLegDates("Departure and arrival ports must differ")

    # Contrôles d'intégrité sur la nouvelle planification (exclut le leg
    # courant des comparaisons chevauchement/continuité).
    await validate_leg_schedule(
        db,
        vessel_id=leg.vessel_id,
        departure_port_id=leg.departure_port_id,
        arrival_port_id=leg.arrival_port_id,
        etd=new_etd,
        eta=new_eta,
        transit_speed_kn=(
            transit_speed_kn if transit_speed_kn is not None else leg.transit_speed_kn
        ),
        elongation_coef=(elongation_coef if elongation_coef is not None else leg.elongation_coef),
        exclude_leg_id=leg.id,
    )

    if is_bookable is not None:
        leg.is_bookable = is_bookable
    if public_capacity_palettes is not None:
        leg.public_capacity_palettes = public_capacity_palettes
    if public_price_per_palette_eur is not None:
        leg.public_price_per_palette_eur = public_price_per_palette_eur
    if booking_close_at is not None:
        leg.booking_close_at = booking_close_at
    elif etd is not None and leg.booking_close_at is None:
        # ETD modifiée + pas de clôture définie -> auto ETD-48h
        leg.booking_close_at = new_etd - timedelta(hours=BOOKING_CLOSE_LEAD_HOURS)
    if transit_speed_kn is not None:
        leg.transit_speed_kn = transit_speed_kn
    if elongation_coef is not None:
        leg.elongation_coef = elongation_coef
    if port_stay_planned_hours is not None:
        leg.port_stay_planned_hours = port_stay_planned_hours

    # Recompute leg_code si l'une de ses entrées a changé
    if (
        leg.vessel_id != old_vessel_id
        or leg.departure_port_id != old_pol_id
        or leg.arrival_port_id != old_pod_id
        or str(leg.etd.year)[-1] != old_year_digit
    ):
        vessel = await db.get(Vessel, leg.vessel_id)
        pol = await db.get(Port, leg.departure_port_id)
        pod = await db.get(Port, leg.arrival_port_id)
        if vessel and pol and pod:
            for seq in range(1, 100):
                candidate = _leg_code_for(
                    vessel.code,
                    pol.country,
                    pod.country,
                    leg.etd,
                    seq,
                )
                if candidate == leg.leg_code:
                    break  # Déjà unique avec cette séquence — on garde
                existing = (
                    await db.execute(
                        select(Leg).where(Leg.leg_code == candidate).where(Leg.id != leg.id)
                    )
                ).scalar_one_or_none()
                if not existing:
                    leg.leg_code = candidate
                    break

    if not cascade or delta == timedelta(0):
        await db.flush()
        return None

    # Cascade complète (UC-03) : legs aval du même navire + opérations escale
    # + shifts dockers + notification des clients impactés. La logique vit
    # dans date_cascade.cascade_from_leg (best-effort, isolé par bloc) afin
    # d'être appelable aussi depuis l'ETA-shift capitaine. La frontière des
    # legs aval (ancien ETD) est reconstituée côté service via leg.etd - delta.
    from app.services import date_cascade

    summary = await date_cascade.cascade_from_leg(db, leg, delta=delta)
    await db.flush()
    # CascadeReport.impacted_leg_ids = legs AVAL décalés (hors leg source),
    # pour préserver le contrat historique (le détail audit compte les legs
    # propagés). summary["impacted_leg_ids"] inclut le leg source en tête.
    downstream_ids = [lid for lid in summary.get("impacted_leg_ids", []) if lid != leg.id]
    return CascadeReport(leg_id=leg.id, delta=delta, impacted_leg_ids=downstream_ids)


async def delete_leg(db: AsyncSession, leg: Leg) -> None:
    """Delete a leg.

    Refuse si des données dépendantes existent — la plupart des FK
    enfants n'ont pas ``ondelete="CASCADE"`` (volontaire : intégrité
    réglementaire MRV, SOF, finance…). On scanne explicitement et on
    rend une erreur lisible listant ce qui bloque, plutôt que de
    laisser remonter un IntegrityError opaque.
    """
    from sqlalchemy import func

    from app.models.booking import Booking
    from app.models.commercial import OrderAssignment, RateOffer
    from app.models.crew import CrewAssignment
    from app.models.escale import DockerShift, EscaleOperation
    from app.models.finance import LegFinance, LegKPI
    from app.models.mrv import MRVEvent
    from app.models.noon_report import NoonReport
    from app.models.watch_log import OnboardChecklist, VisitorLog, WatchLog

    # (modèle, label humain) — uniquement les tables avec FK NOT NULL ou
    # qui contiennent de la donnée audit/réglementaire qui ne doit pas
    # disparaître silencieusement. Les FK nullable nettoyées par la DB
    # (claims, tickets, certificats CO₂…) ne bloquent pas la suppression
    # car on les set à NULL avant le delete (cf. _nullify_optional_fks).
    BLOCKING = [
        (Booking, "réservations"),
        (NoonReport, "noon reports"),
        (LegFinance, "fiche finance"),
        (LegKPI, "KPI"),
        (EscaleOperation, "opérations escale"),
        (DockerShift, "shifts dockers"),
        (WatchLog, "entrées de quart"),
        (OnboardChecklist, "check-lists onboard"),
        (VisitorLog, "registre visiteurs ISPS"),
        (MRVEvent, "événements MRV"),
        (CrewAssignment, "affectations équipage"),
        (OrderAssignment, "assignations commande"),
        (RateOffer, "offres tarifaires"),
    ]

    blocks: list[str] = []
    for model, label in BLOCKING:
        count = await db.scalar(
            select(func.count()).select_from(model).where(model.leg_id == leg.id)
        )
        if count:
            blocks.append(f"{count} {label}")

    if blocks:
        raise PlanningError(
            f"Impossible de supprimer le leg {leg.leg_code} — dépendances : "
            + ", ".join(blocks)
            + ". Nettoyez ces enregistrements avant suppression."
        )

    # FK nullables qu'on délie proprement avant suppression (la DB
    # refuserait sinon avec un IntegrityError opaque).
    await _nullify_optional_fks(db, leg.id)

    await db.delete(leg)
    await db.flush()


async def _nullify_optional_fks(db: AsyncSession, leg_id: int) -> None:
    """Set leg_id=NULL sur les FK nullables pointant vers ce leg.

    Ces tables conservent la donnée historique mais perdent le lien
    vers le leg supprimé. Couvre : claims, tickets, onboard_cashboxes,
    crew_tickets, co2_certificates, commercial orders.

    RateGridLine n'est PAS dans la liste — bien que commercial.py le
    suggère par voisinage, ce modèle n'a pas de FK leg_id (les lignes
    de grille tarifaire ne sont pas liées à un leg spécifique).
    """
    from sqlalchemy import update

    from app.models.anemos_certificate import AnemosCertificate
    from app.models.claim import Claim
    from app.models.commercial import Order
    from app.models.crew_ticket import CrewTicket
    from app.models.onboard_cashbox import CashboxMovement
    from app.models.ticket import Ticket

    # CashboxMovement (pas OnboardCashbox) porte le leg_id : un mouvement
    # cash est rattaché à un leg, le coffre lui-même non.
    for model in (Claim, Ticket, CashboxMovement, CrewTicket, AnemosCertificate, Order):
        await db.execute(update(model).where(model.leg_id == leg_id).values(leg_id=None))


# ---------------------------------------------------------------------------
# Queries for Gantt views
# ---------------------------------------------------------------------------


async def list_legs_in_window(
    db: AsyncSession,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    vessel_id: int | None = None,
    status: str | None = None,
) -> list[Leg]:
    stmt = select(Leg).order_by(Leg.etd.asc())
    if date_from is not None:
        stmt = stmt.where(Leg.eta >= date_from)
    if date_to is not None:
        stmt = stmt.where(Leg.etd <= date_to)
    if vessel_id is not None:
        stmt = stmt.where(Leg.vessel_id == vessel_id)
    if status:
        stmt = stmt.where(Leg.status == status)
    return list((await db.execute(stmt)).scalars().all())


DEFAULT_PORT_STAY_HOURS = 24
"""Durée d'escale supposée si ``port_stay_planned_hours`` n'est pas renseigné."""


# ---------------------------------------------------------------------------
# Jours ouvrés portuaires (fermeture commerciale samedi/dimanche)
# ---------------------------------------------------------------------------

# Python ``datetime.weekday()`` : lundi=0 … samedi=5, dimanche=6.
_SATURDAY = 5
_SUNDAY = 6


def next_working_departure(
    arrival: datetime,
    stay_hours: float,
    closed_weekdays: set[int],
) -> datetime:
    """Date de départ après escale, en tenant compte des jours fermés au commerce.

    Règle : les opérations commerciales (chargement/déchargement) ne peuvent
    avoir lieu un jour fermé. Si le navire arrive un jour fermé, l'escale **se
    décale au prochain jour ouvré**. Tout jour fermé traversé par l'escale en
    prolonge la durée d'un jour, et le départ ne tombe jamais un jour fermé.

    Sans jour fermé (``closed_weekdays`` vide), renvoie simplement
    ``arrival + stay_hours``.
    """
    if not closed_weekdays:
        return arrival + timedelta(hours=stay_hours)

    # 1. Début effectif des opérations : prochain jour ouvré si arrivée fermée.
    start = arrival
    while start.weekday() in closed_weekdays:
        start = start + timedelta(days=1)

    # 2. Départ naïf, puis +1 jour par jour fermé traversé par l'escale.
    departure = start + timedelta(hours=stay_hours)
    cursor = start.date()
    while cursor < departure.date():
        cursor = cursor + timedelta(days=1)
        if cursor.weekday() in closed_weekdays:
            departure = departure + timedelta(days=1)

    # 3. Le départ ne peut pas tomber un jour fermé → repousse au prochain ouvré.
    while departure.weekday() in closed_weekdays:
        departure = departure + timedelta(days=1)
    return departure


async def closed_weekdays_for_port(db: AsyncSession, port_id: int | None) -> set[int]:
    """Jours fermés au commerce d'un port (depuis ``PortConfig``).

    Renvoie un sous-ensemble de ``{5, 6}`` (samedi/dimanche). Vide si le port
    n'a pas de configuration ou n'a aucune restriction.
    """
    if not port_id:
        return set()
    from app.models.finance import PortConfig

    cfg = (
        await db.execute(select(PortConfig).where(PortConfig.port_id == port_id))
    ).scalar_one_or_none()
    if cfg is None:
        return set()
    closed: set[int] = set()
    if cfg.closed_saturday:
        closed.add(_SATURDAY)
    if cfg.closed_sunday:
        closed.add(_SUNDAY)
    return closed


# ─────────────────────────── PLN-05 — détection de retard ────────────────────

DELAY_THRESHOLD_HOURS = 4.0


def _dev_hours(current: datetime | None, reference: datetime | None) -> float:
    """Écart (heures) courant − référence ; positif = en retard.

    tz-safe : normalise les deux bornes en naïf (SQLite relit naïf, Postgres
    aware) avant la soustraction.
    """
    if current is None or reference is None:
        return 0.0
    c = current.replace(tzinfo=None)
    r = reference.replace(tzinfo=None)
    return (c - r).total_seconds() / 3600.0


def leg_delay_hours(leg: Leg) -> float:
    """Pire retard (heures) du prévisionnel courant vs la référence figée.

    Compare ETD↔ETD_ref et ETA↔ETA_ref ; renvoie le plus grand écart (positif
    = retard, négatif = avance).
    """
    return max(_dev_hours(leg.etd, leg.etd_ref), _dev_hours(leg.eta, leg.eta_ref))


def is_delayed(leg: Leg, threshold_hours: float = DELAY_THRESHOLD_HOURS) -> bool:
    """True si le leg accuse un retard ≥ seuil vs sa référence (PLN-05)."""
    return leg_delay_hours(leg) >= threshold_hours


def detect_port_conflicts(
    legs: Sequence[Leg],
    *,
    default_stay_hours: int = DEFAULT_PORT_STAY_HOURS,
) -> list[tuple[int, int]]:
    """Paires (leg_id_a, leg_id_b) de navires DIFFÉRENTS présents au MÊME
    port en MÊME temps.

    On modélise l'occupation réelle d'un port par un navire comme
    l'intervalle ``[ETA, ETA + durée_escale]`` au port d'arrivée (la
    présence au port de départ est, par continuité, l'intervalle
    d'arrivée du leg précédent). Deux intervalles qui se chevauchent au
    même port pour deux navires distincts = conflit — y compris quand
    les ETA sont distantes de plus de 12h mais que les escales se
    recouvrent (cas que l'ancienne heuristique ETA±12h ratait).
    """
    intervals: list[tuple[int, int, datetime, datetime, int]] = []
    for leg in legs:
        eta = getattr(leg, "eta", None)
        if eta is None:
            continue
        stay = getattr(leg, "port_stay_planned_hours", None) or default_stay_hours
        end = eta + timedelta(hours=stay)
        intervals.append((leg.arrival_port_id, leg.vessel_id, eta, end, leg.id))

    conflicts: list[tuple[int, int]] = []
    for i in range(len(intervals)):
        port_a, ves_a, start_a, end_a, id_a = intervals[i]
        for j in range(i + 1, len(intervals)):
            port_b, ves_b, start_b, end_b, id_b = intervals[j]
            if port_a != port_b or ves_a == ves_b:
                continue
            # Chevauchement d'intervalles : start_a < end_b ET start_b < end_a
            if start_a < end_b and start_b < end_a:
                conflicts.append((id_a, id_b))
    return conflicts


async def detect_port_conflicts_view(
    db: AsyncSession,
    *,
    window_days: int = 90,
    default_stay_hours: int = DEFAULT_PORT_STAY_HOURS,
) -> list[dict]:
    """Vue enrichie des conflits de port pour l'écran dédié.

    Charge les legs de la fenêtre ``[today, today + window_days]`` (par ETA),
    réutilise la règle de chevauchement de ``detect_port_conflicts`` (deux
    navires DIFFÉRENTS présents au MÊME port sur des escales
    ``[ETA, ETA + durée]`` qui se recouvrent), puis hydrate chaque paire avec
    le port (locode/nom), les deux navires et la fenêtre de chevauchement
    exacte ``[max(eta_a, eta_b), min(fin_a, fin_b)]``.

    Renvoie une liste de dicts triée par début de chevauchement ::

        {
          "port_locode", "port_name",
          "vessel_a_code", "vessel_a_name", "leg_a_id", "leg_a_code",
          "vessel_b_code", "vessel_b_name", "leg_b_id", "leg_b_code",
          "overlap_start", "overlap_end", "overlap_hours",
        }
    """
    from app.models.port import Port
    from app.models.vessel import Vessel

    now = datetime.now(UTC)
    window_start = now
    window_end = now + timedelta(days=window_days)

    legs = list(
        (
            await db.execute(
                select(Leg)
                .where(Leg.eta >= window_start)
                .where(Leg.eta <= window_end)
                .order_by(Leg.eta.asc())
            )
        )
        .scalars()
        .all()
    )
    if not legs:
        return []

    legs_by_id = {leg.id: leg for leg in legs}
    pairs = detect_port_conflicts(legs, default_stay_hours=default_stay_hours)
    if not pairs:
        return []

    # Pré-charge ports + navires des legs en conflit (anti N+1).
    conflict_leg_ids: set[int] = {lid for pair in pairs for lid in pair}
    conflict_legs = [legs_by_id[lid] for lid in conflict_leg_ids if lid in legs_by_id]
    port_ids = {leg.arrival_port_id for leg in conflict_legs}
    vessel_ids = {leg.vessel_id for leg in conflict_legs}
    ports = (
        {
            p.id: p
            for p in (await db.execute(select(Port).where(Port.id.in_(port_ids)))).scalars().all()
        }
        if port_ids
        else {}
    )
    vessels = (
        {
            v.id: v
            for v in (await db.execute(select(Vessel).where(Vessel.id.in_(vessel_ids))))
            .scalars()
            .all()
        }
        if vessel_ids
        else {}
    )

    rows: list[dict] = []
    for id_a, id_b in pairs:
        leg_a = legs_by_id.get(id_a)
        leg_b = legs_by_id.get(id_b)
        if leg_a is None or leg_b is None:
            continue
        stay_a = leg_a.port_stay_planned_hours or default_stay_hours
        stay_b = leg_b.port_stay_planned_hours or default_stay_hours
        end_a = leg_a.eta + timedelta(hours=stay_a)
        end_b = leg_b.eta + timedelta(hours=stay_b)
        overlap_start = max(leg_a.eta, leg_b.eta)
        overlap_end = min(end_a, end_b)
        overlap_hours = max(0.0, (overlap_end - overlap_start).total_seconds() / 3600.0)
        port = ports.get(leg_a.arrival_port_id)
        ves_a = vessels.get(leg_a.vessel_id)
        ves_b = vessels.get(leg_b.vessel_id)
        rows.append(
            {
                "port_locode": port.locode if port else "?",
                "port_name": port.name if port else "—",
                "vessel_a_code": ves_a.code if ves_a else "?",
                "vessel_a_name": ves_a.name if ves_a else "—",
                "leg_a_id": leg_a.id,
                "leg_a_code": leg_a.leg_code,
                "vessel_b_code": ves_b.code if ves_b else "?",
                "vessel_b_name": ves_b.name if ves_b else "—",
                "leg_b_id": leg_b.id,
                "leg_b_code": leg_b.leg_code,
                "overlap_start": overlap_start,
                "overlap_end": overlap_end,
                "overlap_hours": round(overlap_hours, 1),
            }
        )

    rows.sort(key=lambda r: r["overlap_start"])
    return rows


# ---------------------------------------------------------------------------
# Public share
# ---------------------------------------------------------------------------


def _new_share_token() -> str:
    return secrets.token_urlsafe(24)


async def create_share(
    db: AsyncSession,
    *,
    label: str | None,
    vessel_id: int | None,
    only_bookable: bool,
    description: str | None,
    expires_at: datetime | None,
    created_by_id: int | None,
    pol_port_id: int | None = None,
    pod_port_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    recipient_name: str | None = None,
    recipient_company: str | None = None,
    recipient_email: str | None = None,
    recipient_notes: str | None = None,
    lang: str = "fr",
    legs_ids: str | None = None,
) -> PlanningShare:
    share = PlanningShare(
        token=_new_share_token(),
        label=label,
        vessel_id=vessel_id,
        pol_port_id=pol_port_id,
        pod_port_id=pod_port_id,
        only_bookable=only_bookable,
        description=description,
        date_from=date_from,
        date_to=date_to,
        expires_at=expires_at,
        created_by_id=created_by_id,
        recipient_name=recipient_name,
        recipient_company=recipient_company,
        recipient_email=recipient_email,
        recipient_notes=recipient_notes,
        lang=clamp_share_lang(lang),
        legs_ids=legs_ids,
        is_active=True,
    )
    db.add(share)
    await db.flush()
    return share


# PLN-04 — langues supportées pour le partage public (le template public ne gère
# que FR/EN ; toute autre valeur retombe sur FR). Source unique du clamp.
SHARE_LANGS = ("fr", "en")


def clamp_share_lang(lang: str | None) -> str:
    """Borne une langue de partage à ``SHARE_LANGS`` (défaut ``fr``)."""
    return lang if lang in SHARE_LANGS else "fr"


def legs_ids_list(raw: str | None) -> list[int]:
    """Parse une saisie de sélection leg-à-leg en liste d'IDs entiers triés/uniques.

    Accepte des IDs séparés par virgule / espace / point-virgule ; ignore tout
    token non entier ou ≤ 0. Source unique du parsing (écriture **et** lecture).
    """
    if not raw:
        return []
    ids: list[int] = []
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            val = int(part)
        except ValueError:
            continue
        if val > 0 and val not in ids:
            ids.append(val)
    return sorted(ids)


def parse_legs_ids(raw: str | None) -> str | None:
    """Normalise une sélection leg-à-leg en CSV d'IDs triés, ou ``None`` si vide.

    ⇒ ``None`` fait retomber le partage sur les filtres navire/port/période.
    """
    ids = legs_ids_list(raw)
    return ",".join(str(i) for i in ids) if ids else None


async def lookup_share(db: AsyncSession, token: str) -> PlanningShare | None:
    share = (
        await db.execute(select(PlanningShare).where(PlanningShare.token == token))
    ).scalar_one_or_none()
    if not share or not share.is_active:
        return None
    if share.expires_at and share.expires_at < datetime.now(UTC):
        return None
    return share


async def list_shares(db: AsyncSession) -> list[PlanningShare]:
    stmt = select(PlanningShare).order_by(PlanningShare.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


async def revoke_share(db: AsyncSession, share: PlanningShare) -> None:
    share.is_active = False
    await db.flush()
