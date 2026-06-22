"""Service planification provisoire (scénarios what-if).

Tout vit dans les tables ``planning_scenarios`` / ``scenario_legs``, isolées
de la planification réelle (``legs``). Aucune fonction de ce module n'écrit
dans ``legs`` : l'outil est consultatif par conception.

Validation **souple** adaptée à l'exploration d'hypothèses :
  - dur (lève) : ETD ≥ ETA, durée > 180 j, ports identiques ;
  - souple (avertissements non bloquants) : chevauchement navire, rupture de
    continuité géographique, vitesse implicite invraisemblable.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import Leg
from app.models.planning_scenario import PlanningScenario, ScenarioLeg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.planning import (
    MAX_PLAUSIBLE_SPEED_KN,
    InvalidLegDates,
    validate_dates,
)


class ScenarioError(Exception):
    """Erreur métier scénario."""


# ---------------------------------------------------------------------------
# Scénarios (CRUD en-tête)
# ---------------------------------------------------------------------------


async def create_scenario(
    db: AsyncSession,
    *,
    name: str,
    description: str | None = None,
    created_by_id: int | None = None,
    created_by_name: str | None = None,
) -> PlanningScenario:
    name = (name or "").strip()
    if not name:
        raise ScenarioError("Le nom du scénario est obligatoire.")
    scenario = PlanningScenario(
        name=name,
        description=(description or "").strip() or None,
        status="draft",
        created_by_id=created_by_id,
        created_by_name=created_by_name,
    )
    db.add(scenario)
    await db.flush()
    return scenario


async def update_scenario(
    db: AsyncSession,
    scenario: PlanningScenario,
    *,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
) -> PlanningScenario:
    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise ScenarioError("Le nom du scénario est obligatoire.")
        scenario.name = cleaned
    if description is not None:
        scenario.description = description.strip() or None
    if status is not None and status in {"draft", "archived"}:
        scenario.status = status
    await db.flush()
    return scenario


async def list_scenarios(db: AsyncSession) -> list[PlanningScenario]:
    stmt = select(PlanningScenario).order_by(PlanningScenario.updated_at.desc())
    return list((await db.execute(stmt)).scalars().all())


async def get_scenario(db: AsyncSession, scenario_id: int) -> PlanningScenario | None:
    return await db.get(PlanningScenario, scenario_id)


async def count_legs(db: AsyncSession, scenario_id: int) -> int:
    return (
        await db.scalar(
            select(func.count(ScenarioLeg.id)).where(ScenarioLeg.scenario_id == scenario_id)
        )
        or 0
    )


async def delete_scenario(db: AsyncSession, scenario: PlanningScenario) -> None:
    # Suppression explicite des legs (au cas où la DB n'applique pas le
    # ON DELETE CASCADE — ex. SQLite de test sans PRAGMA foreign_keys).
    await db.execute(delete(ScenarioLeg).where(ScenarioLeg.scenario_id == scenario.id))
    await db.delete(scenario)
    await db.flush()


# ---------------------------------------------------------------------------
# Clonage de la planification réelle → scénario
# ---------------------------------------------------------------------------


async def clone_real_legs_into(
    db: AsyncSession,
    scenario: PlanningScenario,
    *,
    vessel_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> int:
    """Copie les legs réels (filtrés navire/période) en legs provisoires.

    Lecture seule sur ``legs`` : on ne lit que pour dupliquer. Renvoie le
    nombre de traversées clonées.
    """
    stmt = select(Leg).order_by(Leg.etd.asc())
    if vessel_id is not None:
        stmt = stmt.where(Leg.vessel_id == vessel_id)
    if date_from is not None:
        stmt = stmt.where(Leg.eta >= date_from)
    if date_to is not None:
        stmt = stmt.where(Leg.etd <= date_to)
    real_legs = list((await db.execute(stmt)).scalars().all())

    for leg in real_legs:
        db.add(
            ScenarioLeg(
                scenario_id=scenario.id,
                vessel_id=leg.vessel_id,
                departure_port_id=leg.departure_port_id,
                arrival_port_id=leg.arrival_port_id,
                etd=leg.etd,
                eta=leg.eta,
                label=leg.leg_code,
                status=leg.status if leg.status in {"planned", "inprogress", "completed", "cancelled"} else "planned",
                port_stay_planned_hours=leg.port_stay_planned_hours,
                transit_speed_kn=leg.transit_speed_kn,
                elongation_coef=leg.elongation_coef,
            )
        )
    await db.flush()
    return len(real_legs)


# ---------------------------------------------------------------------------
# Legs provisoires (CRUD)
# ---------------------------------------------------------------------------


async def list_scenario_legs(db: AsyncSession, scenario_id: int) -> list[ScenarioLeg]:
    stmt = (
        select(ScenarioLeg)
        .where(ScenarioLeg.scenario_id == scenario_id)
        .order_by(ScenarioLeg.etd.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_scenario_leg(db: AsyncSession, leg_id: int) -> ScenarioLeg | None:
    return await db.get(ScenarioLeg, leg_id)


def _validate_leg_inputs(
    *, departure_port_id: int, arrival_port_id: int, etd: datetime, eta: datetime
) -> None:
    """Contrôles **durs** uniquement (lèvent). Le reste = avertissements."""
    validate_dates(etd, eta)
    if departure_port_id == arrival_port_id:
        raise InvalidLegDates("Le port de départ et d'arrivée doivent différer.")


async def add_scenario_leg(
    db: AsyncSession,
    scenario: PlanningScenario,
    *,
    vessel_id: int,
    departure_port_id: int,
    arrival_port_id: int,
    etd: datetime,
    eta: datetime,
    label: str | None = None,
    status: str = "planned",
    port_stay_planned_hours: int | None = None,
    transit_speed_kn: float | None = None,
    elongation_coef: float | None = None,
    notes: str | None = None,
) -> ScenarioLeg:
    _validate_leg_inputs(
        departure_port_id=departure_port_id,
        arrival_port_id=arrival_port_id,
        etd=etd,
        eta=eta,
    )
    leg = ScenarioLeg(
        scenario_id=scenario.id,
        vessel_id=vessel_id,
        departure_port_id=departure_port_id,
        arrival_port_id=arrival_port_id,
        etd=etd,
        eta=eta,
        label=(label or "").strip() or None,
        status=status if status in {"planned", "inprogress", "completed", "cancelled"} else "planned",
        port_stay_planned_hours=port_stay_planned_hours,
        transit_speed_kn=transit_speed_kn,
        elongation_coef=elongation_coef,
        notes=(notes or "").strip() or None,
    )
    db.add(leg)
    # Touch le scénario pour faire remonter updated_at.
    scenario.updated_at = datetime.now(UTC)
    await db.flush()
    return leg


async def update_scenario_leg(
    db: AsyncSession,
    leg: ScenarioLeg,
    *,
    vessel_id: int | None = None,
    departure_port_id: int | None = None,
    arrival_port_id: int | None = None,
    etd: datetime | None = None,
    eta: datetime | None = None,
    label: str | None = None,
    status: str | None = None,
    port_stay_planned_hours: int | None = None,
    transit_speed_kn: float | None = None,
    elongation_coef: float | None = None,
    notes: str | None = None,
) -> ScenarioLeg:
    new_dep = departure_port_id if departure_port_id is not None else leg.departure_port_id
    new_arr = arrival_port_id if arrival_port_id is not None else leg.arrival_port_id
    new_etd = etd or leg.etd
    new_eta = eta or leg.eta
    _validate_leg_inputs(
        departure_port_id=new_dep, arrival_port_id=new_arr, etd=new_etd, eta=new_eta
    )

    if vessel_id is not None:
        leg.vessel_id = vessel_id
    leg.departure_port_id = new_dep
    leg.arrival_port_id = new_arr
    leg.etd = new_etd
    leg.eta = new_eta
    if label is not None:
        leg.label = label.strip() or None
    if status is not None and status in {"planned", "inprogress", "completed", "cancelled"}:
        leg.status = status
    if port_stay_planned_hours is not None:
        leg.port_stay_planned_hours = port_stay_planned_hours
    if transit_speed_kn is not None:
        leg.transit_speed_kn = transit_speed_kn
    if elongation_coef is not None:
        leg.elongation_coef = elongation_coef
    if notes is not None:
        leg.notes = notes.strip() or None
    await db.flush()
    return leg


async def delete_scenario_leg(db: AsyncSession, leg: ScenarioLeg) -> None:
    await db.delete(leg)
    await db.flush()


# ---------------------------------------------------------------------------
# Avertissements de cohérence (souples, non bloquants)
# ---------------------------------------------------------------------------


def scenario_warnings(
    legs: Sequence[ScenarioLeg],
    ports: dict[int, Port],
    *,
    default_stay_hours: int = 24,
) -> list[str]:
    """Détecte les incohérences douces d'un scénario (par navire trié ETD).

    Ne lève jamais : renvoie une liste de messages destinés à informer
    l'utilisateur sans bloquer son exploration.
    """
    from app.services.ports import haversine_nm

    warnings: list[str] = []
    by_vessel: dict[int, list[ScenarioLeg]] = {}
    for leg in legs:
        by_vessel.setdefault(leg.vessel_id, []).append(leg)

    for vessel_legs in by_vessel.values():
        ordered = sorted(vessel_legs, key=lambda li: li.etd)
        for idx, leg in enumerate(ordered):
            label = leg.label or f"#{leg.id}"
            # Vitesse implicite invraisemblable.
            pol = ports.get(leg.departure_port_id)
            pod = ports.get(leg.arrival_port_id)
            duration_h = (leg.eta - leg.etd).total_seconds() / 3600.0
            if (
                pol
                and pod
                and duration_h > 0
                and None not in (pol.latitude, pol.longitude, pod.latitude, pod.longitude)
            ):
                gc = haversine_nm(pol.latitude, pol.longitude, pod.latitude, pod.longitude)
                dist = gc * (leg.elongation_coef or 1.0)
                implied = dist / duration_h
                if implied > MAX_PLAUSIBLE_SPEED_KN:
                    warnings.append(
                        f"{label} : {dist:.0f} NM en {duration_h:.0f} h ⇒ "
                        f"{implied:.1f} kn (> {MAX_PLAUSIBLE_SPEED_KN:.0f} kn invraisemblable)."
                    )
            # Continuité géographique + chevauchement avec le leg suivant.
            if idx + 1 < len(ordered):
                nxt = ordered[idx + 1]
                if leg.arrival_port_id != nxt.departure_port_id:
                    nlabel = nxt.label or f"#{nxt.id}"
                    warnings.append(
                        f"{label} → {nlabel} : rupture de continuité "
                        f"(arrivée ≠ départ suivant)."
                    )
                if nxt.etd < leg.eta:
                    nlabel = nxt.label or f"#{nxt.id}"
                    warnings.append(
                        f"{label} ↔ {nlabel} : chevauchement temporel sur le même navire."
                    )
    return warnings


# ---------------------------------------------------------------------------
# Gantt + comparaison
# ---------------------------------------------------------------------------


def build_gantt_rows(
    *,
    vessels: list[Vessel],
    legs: Sequence[ScenarioLeg],
    window_start: datetime,
    window_end: datetime,
    ports: dict[int, Port],
) -> list[dict]:
    """Construit les lignes Gantt (une par navire) pour la fenêtre donnée.

    Même structure que ``planning_router._build_gantt_rows`` pour réutiliser
    le markup Gantt côté template.
    """
    total_seconds = (window_end - window_start).total_seconds()
    rows: list[dict] = []
    by_vessel: dict[int, list[ScenarioLeg]] = {}
    for leg in legs:
        by_vessel.setdefault(leg.vessel_id, []).append(leg)

    for vessel in vessels:
        bars: list[dict] = []
        for leg in by_vessel.get(vessel.id, []):
            start = max(leg.etd, window_start)
            end = min(leg.eta, window_end)
            if end <= start:
                continue
            left_pct = ((start - window_start).total_seconds() / total_seconds) * 100
            width_pct = ((end - start).total_seconds() / total_seconds) * 100
            pol = ports.get(leg.departure_port_id)
            pod = ports.get(leg.arrival_port_id)
            bars.append(
                {
                    "leg_id": leg.id,
                    "leg_code": leg.label or f"#{leg.id}",
                    "status": leg.status,
                    "left_pct": round(left_pct, 3),
                    "width_pct": round(max(width_pct, 1.0), 3),
                    "pol_locode": pol.locode if pol else "",
                    "pod_locode": pod.locode if pod else "",
                    "etd": leg.etd,
                    "eta": leg.eta,
                }
            )
        rows.append({"vessel": vessel, "bars": bars})
    return rows


@dataclass(frozen=True)
class ComparisonStat:
    scenario_legs: int
    real_legs: int
    scenario_sea_days: float
    real_sea_days: float

    @property
    def legs_delta(self) -> int:
        return self.scenario_legs - self.real_legs

    @property
    def sea_days_delta(self) -> float:
        return round(self.scenario_sea_days - self.real_sea_days, 1)


def _sea_days(legs: Sequence) -> float:
    total = 0.0
    for leg in legs:
        if leg.etd and leg.eta and leg.eta > leg.etd:
            total += (leg.eta - leg.etd).total_seconds() / 86400.0
    return round(total, 1)


async def compare_to_real(
    db: AsyncSession,
    scenario_legs: Sequence[ScenarioLeg],
    *,
    window_start: datetime,
    window_end: datetime,
    vessel_id: int | None = None,
) -> ComparisonStat:
    """Compare le scénario à la planification réelle sur la même fenêtre."""
    stmt = select(Leg).where(Leg.eta >= window_start).where(Leg.etd <= window_end)
    if vessel_id is not None:
        stmt = stmt.where(Leg.vessel_id == vessel_id)
    real_legs = list((await db.execute(stmt)).scalars().all())
    in_window = [
        li for li in scenario_legs if li.eta >= window_start and li.etd <= window_end
    ]
    return ComparisonStat(
        scenario_legs=len(in_window),
        real_legs=len(real_legs),
        scenario_sea_days=_sea_days(in_window),
        real_sea_days=_sea_days(real_legs),
    )


# ---------------------------------------------------------------------------
# Export CSV
# ---------------------------------------------------------------------------


def to_csv(
    scenario: PlanningScenario,
    legs: Sequence[ScenarioLeg],
    vessels: dict[int, Vessel],
    ports: dict[int, Port],
) -> str:
    """Sérialise un scénario en CSV (séparateur ';', décimales FR-friendly)."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        ["leg", "navire", "POL", "POD", "ETD", "ETA", "duree_jours", "escale_h", "statut"]
    )
    for leg in sorted(legs, key=lambda li: li.etd):
        vessel = vessels.get(leg.vessel_id)
        pol = ports.get(leg.departure_port_id)
        pod = ports.get(leg.arrival_port_id)
        duration_days = round((leg.eta - leg.etd).total_seconds() / 86400.0, 1)
        writer.writerow(
            [
                leg.label or f"#{leg.id}",
                vessel.code if vessel else "?",
                pol.locode if pol else "?",
                pod.locode if pod else "?",
                leg.etd.strftime("%Y-%m-%d %H:%M"),
                leg.eta.strftime("%Y-%m-%d %H:%M"),
                str(duration_days).replace(".", ","),
                leg.port_stay_planned_hours or "",
                leg.status,
            ]
        )
    return buf.getvalue()
