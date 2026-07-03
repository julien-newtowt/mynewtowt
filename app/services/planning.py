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
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
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

# Taxonomie UNIQUE des statuts de leg (stockage + affichage). Historiquement
# deux graphies coexistaient (« inprogress » / « in_progress ») : la valeur
# canonique est ``in_progress`` (migration 0094 normalise l'existant).
LEG_STATUSES: tuple[str, ...] = ("planned", "in_progress", "completed", "cancelled")


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Rend un datetime aware UTC (les naïfs sont réputés UTC dans tout l'ERP).

    Les colonnes ``DateTime(timezone=True)`` reviennent aware sous Postgres
    mais naïves sous SQLite (tests) ; les saisies ``datetime-local`` sont
    naïves. Toute arithmétique de planification passe par ce helper pour ne
    jamais mélanger naïf et aware.
    """
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


def parse_form_datetime(value, allow_empty: bool = False) -> datetime | None:
    """Parse une saisie ``<input type="datetime-local">`` en datetime aware UTC.

    Source unique du parsing de dates de formulaire pour la planification
    (fiche leg, drag-drop Gantt, ETA-shift capitaine). Lève ``InvalidLegDates``
    sur valeur manquante (sauf ``allow_empty``) ou malformée.
    """
    if value is None or value == "":
        if allow_empty:
            return None
        raise InvalidLegDates("Date required")
    s = str(value).replace("T", " ")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise InvalidLegDates(f"Invalid date format: {value}") from e
    return ensure_utc(dt)


def refresh_leg_status(leg: Leg) -> str:
    """Recalcule ``leg.status`` à partir du réel — machine à états unique.

    - ``cancelled`` est sticky (décision humaine, jamais recalculée) ;
    - ``completed`` = clôture de voyage approuvée (``closure_approved_at``) ;
    - ``in_progress`` = du premier fait réel (ATD ou ATA posé) à la clôture ;
    - ``planned`` sinon.

    Tous les flux qui posent ATD/ATA ou touchent la clôture (SOF capitaine,
    statut portuaire escale, workflow closure) DOIVENT passer par ici plutôt
    que d'écrire ``leg.status`` directement.
    """
    if leg.status == "cancelled":
        return leg.status
    if leg.closure_approved_at is not None:
        leg.status = "completed"
    elif leg.atd is not None or leg.ata is not None:
        leg.status = "in_progress"
    else:
        leg.status = "planned"
    return leg.status


@dataclass(frozen=True)
class CascadeReport:
    leg_id: int
    delta: timedelta
    impacted_leg_ids: list[int]
    renumbered: list[tuple[int, str, str]] = field(default_factory=list)

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
    ignore_overlap_leg_ids: Iterable[int] = (),
) -> list[str]:
    """Contrôles d'intégrité d'un leg avant create/update.

    Lève PlanningError (sous-classe) sur violation dure :
      - **Chevauchement navire** : deux legs du même navire ne peuvent
        pas se chevaucher dans le temps (un navire = un seul leg à la fois).
      - **Continuité géographique** : le POD du leg précédent (même navire)
        doit être le POL de ce leg, et le POL du leg suivant doit être ce POD.
      - **Cohérence vitesse/distance** : la durée ne peut impliquer une
        vitesse > MAX_PLAUSIBLE_SPEED_KN, ni > 1.5× la vitesse planifiée.

    Les legs **annulés** ne comptent ni pour le chevauchement ni pour la
    continuité : annuler un leg libère son créneau.

    ``ignore_overlap_leg_ids`` = legs aval que la cascade va décaler : la
    validation d'un déplacement porte sur l'état FINAL (source déplacée +
    aval recalés), ces legs sont donc exemptés du test de chevauchement
    (leur position finale est garantie sans conflit par la simulation).
    Ils restent pris en compte pour la continuité (l'ordre est préservé).

    Renvoie une liste d'**avertissements** non bloquants (ex. continuité
    douteuse mais tolérée). Les violations dures lèvent.
    """
    from app.models.port import Port
    from app.services.ports import haversine_nm

    warnings: list[str] = []
    ignored = set(ignore_overlap_leg_ids)

    # ── 1. Chevauchement temporel sur le même navire ──────────────────
    overlap_stmt = (
        select(Leg)
        .where(Leg.vessel_id == vessel_id)
        .where(Leg.status != "cancelled")
        .where(Leg.etd < eta)
        .where(Leg.eta > etd)
    )
    if exclude_leg_id is not None:
        overlap_stmt = overlap_stmt.where(Leg.id != exclude_leg_id)
    if ignored:
        overlap_stmt = overlap_stmt.where(Leg.id.not_in(ignored))
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
        .where(Leg.status != "cancelled")
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
        .where(Leg.status != "cancelled")
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


RANK_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def rank_letter(sequence: int) -> str:
    """Rang chronologique du leg dans l'année → lettre (1=A, 2=B, … 26=Z)."""
    if not 1 <= sequence <= len(RANK_LETTERS):
        raise PlanningError(
            f"Rang de leg hors plage (1-{len(RANK_LETTERS)}) : {sequence}. "
            "Le format leg_code n'encode qu'une lettre de rang par année."
        )
    return RANK_LETTERS[sequence - 1]


def _leg_code_for(
    vessel_code: str,
    pol_country: str,
    pod_country: str,
    etd: datetime,
    sequence: int = 1,
) -> str:
    """Génère le leg_code — format officiel NEWTOWT :

    ``{code navire (1 chiffre)}{rang dans l'année (1 lettre, A=1er)}``
    ``{pays POL (2 lettres)}{pays POD (2 lettres)}{dernier chiffre année}``

    Ex. ``1CFRBR6`` = navire **1** (Anemos), **3ᵉ** voyage de 2026 (C),
    France → Brésil. Le rang est la position chronologique par ETD dans
    l'année civile pour ce navire (cf. ``renumber_vessel_year``).
    """
    year_last_digit = str(etd.year)[-1]
    return (
        f"{vessel_code}"
        f"{rank_letter(sequence)}"
        f"{pol_country.upper()[:2]}"
        f"{pod_country.upper()[:2]}"
        f"{year_last_digit}"
    )


async def renumber_vessel_year(
    db: AsyncSession, vessel_id: int, year: int
) -> list[tuple[int, str, str]]:
    """Recale les leg_codes d'un navire sur une année : rang = position ETD.

    Le rang (lettre) reflète TOUJOURS l'ordre chronologique réel des ETD de
    l'année civile — insérer, décaler ou supprimer un leg renumérote les
    voisins. Les legs **annulés** sont exclus de la numérotation (leur code
    est figé, le remplaçant récupère le rang).

    Renumérotation en deux phases (codes temporaires ``~{id}`` puis codes
    finaux) pour ne jamais violer l'unicité pendant les échanges de rang.
    Renvoie la liste des changements ``(leg_id, ancien_code, nouveau_code)``.
    """
    from app.models.port import Port
    from app.models.vessel import Vessel

    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        return []
    year_start = datetime(year, 1, 1, tzinfo=UTC)
    year_end = datetime(year + 1, 1, 1, tzinfo=UTC)
    legs = list(
        (
            await db.execute(
                select(Leg)
                .where(Leg.vessel_id == vessel_id)
                .where(Leg.etd >= year_start)
                .where(Leg.etd < year_end)
                .where(Leg.status != "cancelled")
                .order_by(Leg.etd.asc(), Leg.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if not legs:
        return []

    port_ids = {lg.departure_port_id for lg in legs} | {lg.arrival_port_id for lg in legs}
    ports = {
        p.id: p
        for p in (await db.execute(select(Port).where(Port.id.in_(port_ids)))).scalars().all()
    }

    targets: dict[int, str] = {}
    for seq, lg in enumerate(legs, start=1):
        pol = ports.get(lg.departure_port_id)
        pod = ports.get(lg.arrival_port_id)
        if not (pol and pod):
            continue
        candidate = _leg_code_for(vessel.code, pol.country, pod.country, lg.etd, seq)
        if candidate != lg.leg_code:
            targets[lg.id] = candidate
    if not targets:
        return []

    # Collision hors périmètre (autre navire, leg annulé figé, collision
    # décennale du chiffre d'année) → erreur explicite plutôt qu'un
    # IntegrityError opaque au commit.
    in_scope = {lg.id for lg in legs}
    clash = (
        await db.execute(
            select(Leg)
            .where(Leg.leg_code.in_(list(targets.values())))
            .where(Leg.id.not_in(in_scope))
            .limit(1)
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise PlanningError(
            f"Renumérotation impossible : le code {clash.leg_code} est déjà "
            f"porté par un leg hors périmètre (id={clash.id}). Résolvez la "
            "collision (leg annulé homonyme ou collision décennale) d'abord."
        )

    changes: list[tuple[int, str, str]] = []
    # Phase 1 — codes temporaires (uniques par construction).
    for lg in legs:
        if lg.id in targets:
            changes.append((lg.id, lg.leg_code, targets[lg.id]))
            lg.leg_code = f"~{lg.id}"
    await db.flush()
    # Phase 2 — codes définitifs.
    for lg in legs:
        if lg.id in targets:
            lg.leg_code = targets[lg.id]
    await db.flush()
    return changes


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

    # Si leg_code non fourni : insertion avec un code provisoire, puis
    # renumérotation de l'année du navire — le rang (lettre) reflète la
    # position CHRONOLOGIQUE par ETD, pas l'ordre de création. Insérer un
    # leg « au milieu » renumérote donc aussi les legs suivants de l'année.
    auto_code = leg_code is None
    if auto_code:
        from app.models.port import Port
        from app.models.vessel import Vessel

        vessel = await db.get(Vessel, vessel_id)
        pol = await db.get(Port, departure_port_id)
        pod = await db.get(Port, arrival_port_id)
        if not (vessel and pol and pod):
            raise PlanningError("Invalid vessel/port references")
        leg_code = f"~new{uuid.uuid4().hex[:8]}"

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
    if auto_code:
        await renumber_vessel_year(db, vessel_id, etd.year)
    return leg


async def _lane_after(
    db: AsyncSession, *, vessel_id: int, after_etd: datetime, exclude_leg_id: int
) -> list[Leg]:
    """Legs (non annulés) du navire dont l'ETD est postérieur à ``after_etd``.

    C'est la « voie » aval sur laquelle porte la cascade — y compris les
    legs déjà appareillés (ATD posé), qui sont IMMOBILES mais participent
    à la détection de conflit.
    """
    stmt = (
        select(Leg)
        .where(Leg.vessel_id == vessel_id)
        .where(Leg.id != exclude_leg_id)
        .where(Leg.status != "cancelled")
        .where(Leg.etd > after_etd)
        .order_by(Leg.etd.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


def plan_downstream_shifts(
    downstream: Sequence[Leg],
    *,
    delta: timedelta,
    source_eta: datetime,
) -> dict[int, tuple[datetime, datetime]]:
    """Positions finales (etd, eta) des legs aval après cascade — pur, sans I/O.

    Deux passes (mêmes règles que le moteur scénario, généralisées au réel) :
      1. **Décalage rigide** : les legs non appareillés (ATD null) sont
         translatés de ``delta`` — la planification relative est préservée.
      2. **Résolution des chevauchements** : parcours par ETD tentatif
         croissant ; tout leg qui démarrerait avant la fin du précédent est
         repoussé (durée conservée). Couvre l'allongement d'ETA sans
         décalage d'ETD (``delta`` nul).

    RÈGLE D'OR : un leg déjà appareillé (ATD posé) ne bouge JAMAIS. Si la
    résolution exigerait de le déplacer, lève ``LegOverlap`` — l'opérateur
    doit arbitrer manuellement.

    Renvoie ``{leg_id: (etd_final, eta_final)}`` pour TOUTE la voie aval
    (y compris les legs immobiles, à leur position d'origine).
    """
    pos: dict[int, tuple[datetime, datetime]] = {}
    for lg in downstream:
        etd0, eta0 = ensure_utc(lg.etd), ensure_utc(lg.eta)
        if lg.atd is None and delta:
            pos[lg.id] = (etd0 + delta, eta0 + delta)
        else:
            pos[lg.id] = (etd0, eta0)

    prev_eta = ensure_utc(source_eta)
    for lg in sorted(downstream, key=lambda x: pos[x.id][0]):
        petd, peta = pos[lg.id]
        if petd < prev_eta:
            if lg.atd is not None:
                raise LegOverlap(
                    f"Recalcul impossible : le leg {lg.leg_code} a déjà "
                    f"appareillé (ATD posé) et se retrouverait chevauché. "
                    f"Ajustez la planification manuellement."
                )
            push = prev_eta - petd
            petd, peta = petd + push, peta + push
            pos[lg.id] = (petd, peta)
        if peta > prev_eta:
            prev_eta = peta
    return pos


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
    source: str = "planning_edit",
    actor_id: int | None = None,
    actor_name: str | None = None,
) -> CascadeReport | None:
    """Met à jour un leg. Toute modification de dates recalcule l'aval.

    Déroulé : **valider d'abord, muter ensuite** (``get_db`` committe même
    quand la route intercepte l'erreur pour re-rendre le formulaire — on ne
    laisse donc jamais la session sale sur une validation échouée).

    1. La cascade est **simulée** (``plan_downstream_shifts``) : la
       validation de chevauchement porte sur l'état FINAL — un décalage
       supérieur à l'interstice avec le leg suivant est accepté puisque
       l'aval sera repoussé d'autant.
    2. Les mutations sont appliquées, ``booking_close_at`` suit l'ETD.
    3. ``date_cascade.cascade_from_leg`` propage (legs aval, opérations
       escale, shifts dockers, notifications clients, révisions).
    4. Les leg_codes des années/navires touchés sont renumérotés (le rang
       est chronologique — cf. ``renumber_vessel_year``).

    La cascade ne s'applique que si le navire ne change pas : déplacer un
    leg vers un autre navire n'a pas de « delta » transposable.

    ``source`` qualifie l'origine du recalcul dans ``schedule_revisions``
    (``planning_edit`` | ``gantt_move`` | ``eta_shift``).
    """
    new_etd = ensure_utc(etd) or ensure_utc(leg.etd)
    new_eta = ensure_utc(eta) or ensure_utc(leg.eta)
    validate_dates(new_etd, new_eta)

    new_vessel_id = vessel_id if vessel_id is not None else leg.vessel_id
    new_pol_id = departure_port_id if departure_port_id is not None else leg.departure_port_id
    new_pod_id = arrival_port_id if arrival_port_id is not None else leg.arrival_port_id
    if new_pol_id == new_pod_id:
        raise InvalidLegDates("Departure and arrival ports must differ")

    old_etd = ensure_utc(leg.etd)
    old_eta = ensure_utc(leg.eta)
    old_vessel_id = leg.vessel_id
    old_year = leg.etd.year
    delta = new_etd - old_etd
    dates_changed = (new_etd != old_etd) or (new_eta != old_eta)
    vessel_changed = new_vessel_id != old_vessel_id
    do_cascade = cascade and dates_changed and not vessel_changed

    # ── 1. Simulation de la cascade (état final) ──────────────────────
    moved_ids: set[int] = set()
    if do_cascade:
        lane = await _lane_after(
            db, vessel_id=new_vessel_id, after_etd=old_etd, exclude_leg_id=leg.id
        )
        planned = plan_downstream_shifts(lane, delta=delta, source_eta=new_eta)
        moved_ids = {
            lg.id for lg in lane if planned[lg.id] != (ensure_utc(lg.etd), ensure_utc(lg.eta))
        }

    # ── 2. Validation sur l'état FINAL (aval recalé exempté du test) ──
    await validate_leg_schedule(
        db,
        vessel_id=new_vessel_id,
        departure_port_id=new_pol_id,
        arrival_port_id=new_pod_id,
        etd=new_etd,
        eta=new_eta,
        transit_speed_kn=(
            transit_speed_kn if transit_speed_kn is not None else leg.transit_speed_kn
        ),
        elongation_coef=(elongation_coef if elongation_coef is not None else leg.elongation_coef),
        exclude_leg_id=leg.id,
        ignore_overlap_leg_ids=moved_ids,
    )

    # ── 3. Mutations ──────────────────────────────────────────────────
    leg.etd = new_etd
    leg.eta = new_eta
    leg.vessel_id = new_vessel_id
    leg.departure_port_id = new_pol_id
    leg.arrival_port_id = new_pod_id

    if is_bookable is not None:
        leg.is_bookable = is_bookable
    if public_capacity_palettes is not None:
        leg.public_capacity_palettes = public_capacity_palettes
    if public_price_per_palette_eur is not None:
        leg.public_price_per_palette_eur = public_price_per_palette_eur
    if booking_close_at is not None:
        leg.booking_close_at = booking_close_at
    elif delta and leg.booking_close_at is not None:
        # La clôture booking reste ancrée à l'ETD : elle suit le décalage.
        leg.booking_close_at = ensure_utc(leg.booking_close_at) + delta
    elif etd is not None and leg.booking_close_at is None:
        # ETD modifiée + pas de clôture définie -> auto ETD-48h
        leg.booking_close_at = new_etd - timedelta(hours=BOOKING_CLOSE_LEAD_HOURS)
    if transit_speed_kn is not None:
        leg.transit_speed_kn = transit_speed_kn
    if elongation_coef is not None:
        leg.elongation_coef = elongation_coef
    if port_stay_planned_hours is not None:
        leg.port_stay_planned_hours = port_stay_planned_hours
    await db.flush()

    # ── 4. Historisation + cascade effective ──────────────────────────
    batch_id = uuid.uuid4().hex[:12]
    summary: dict = {}
    if dates_changed:
        from app.services import schedule_history

        await schedule_history.record(
            db,
            leg=leg,
            old_etd=old_etd,
            new_etd=new_etd,
            old_eta=old_eta,
            new_eta=new_eta,
            source=source,
            batch_id=batch_id,
            user_id=actor_id,
            user_name=actor_name,
        )
    if do_cascade:
        from app.services import date_cascade

        summary = await date_cascade.cascade_from_leg(
            db,
            leg,
            old_etd=old_etd,
            old_eta=old_eta,
            source=source,
            batch_id=batch_id,
            actor_id=actor_id,
            actor_name=actor_name,
        )

    # ── 5. Renumérotation des leg_codes (rang chronologique) ──────────
    renumbered: list[tuple[int, str, str]] = []
    pairs = {(old_vessel_id, old_year), (leg.vessel_id, leg.etd.year)}
    for vid, yr in pairs:
        renumbered += await renumber_vessel_year(db, vid, yr)
    renumbered += summary.get("renumbered", [])

    await db.flush()
    if not do_cascade:
        return (
            CascadeReport(leg_id=leg.id, delta=delta, impacted_leg_ids=[], renumbered=renumbered)
            if dates_changed or renumbered
            else None
        )
    # CascadeReport.impacted_leg_ids = legs AVAL décalés (hors leg source).
    downstream_ids = [lid for lid in summary.get("impacted_leg_ids", []) if lid != leg.id]
    return CascadeReport(
        leg_id=leg.id, delta=delta, impacted_leg_ids=downstream_ids, renumbered=renumbered
    )


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

    vessel_id, year = leg.vessel_id, leg.etd.year
    await db.delete(leg)
    await db.flush()
    # Le rang (lettre du leg_code) est chronologique : la suppression
    # renumérote les legs suivants de l'année.
    await renumber_vessel_year(db, vessel_id, year)


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
    from app.models.schedule_revision import ScheduleRevision
    from app.models.ticket import Ticket

    # CashboxMovement (pas OnboardCashbox) porte le leg_id : un mouvement
    # cash est rattaché à un leg, le coffre lui-même non.
    for model in (Claim, Ticket, CashboxMovement, CrewTicket, AnemosCertificate, Order):
        await db.execute(update(model).where(model.leg_id == leg_id).values(leg_id=None))
    # L'historique de recalcul survit à la suppression du leg (snapshot
    # leg_code conservé) — on délie les deux FK.
    await db.execute(
        update(ScheduleRevision).where(ScheduleRevision.leg_id == leg_id).values(leg_id=None)
    )
    await db.execute(
        update(ScheduleRevision)
        .where(ScheduleRevision.trigger_leg_id == leg_id)
        .values(trigger_leg_id=None)
    )


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


def continuity_warnings(
    legs: Sequence[Leg],
    ports: dict[int, object],
    vessels: dict[int, object] | None = None,
) -> list[str]:
    """Ruptures de continuité géographique sur le planning réel (par navire).

    Une suppression de leg intermédiaire ou un changement de navire peut
    laisser un trou POD ≠ POL sans qu'aucune validation ne se déclenche :
    cette fonction alimente le bandeau d'avertissement du Gantt réel.
    Les legs annulés sont ignorés. Ne lève jamais.
    """
    warnings: list[str] = []
    by_vessel: dict[int, list[Leg]] = {}
    for leg in legs:
        if leg.status == "cancelled":
            continue
        by_vessel.setdefault(leg.vessel_id, []).append(leg)

    from itertools import pairwise

    for vessel_id, vessel_legs in by_vessel.items():
        ordered = sorted(vessel_legs, key=lambda li: ensure_utc(li.etd))
        for cur, nxt in pairwise(ordered):
            if cur.arrival_port_id != nxt.departure_port_id:
                pod = ports.get(cur.arrival_port_id)
                pol = ports.get(nxt.departure_port_id)
                vessel = (vessels or {}).get(vessel_id)
                vname = getattr(vessel, "name", None) or f"navire #{vessel_id}"
                warnings.append(
                    f"{vname} : rupture de continuité entre {cur.leg_code} "
                    f"(arrivée {getattr(pod, 'locode', '?')}) et {nxt.leg_code} "
                    f"(départ {getattr(pol, 'locode', '?')})."
                )
    return warnings


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
