"""Service planification provisoire (scénarios what-if).

Les hypothèses vivent dans ``planning_scenarios`` / ``scenario_legs``, isolées
de la planification réelle. **Une seule fonction de ce module écrit dans
``legs`` : ``apply_to_active_planning``** — c'est le geste par lequel un
scénario cesse d'être une hypothèse. Tout le reste est consultatif.

Cette précision n'est pas cosmétique : la docstring affirmait « aucune fonction
de ce module n'écrit dans ``legs`` » alors qu'``apply`` le faisait depuis sa
création, et elle a couvert l'absence des gardes du moteur réel sur ce chemin.

Deux régimes de validation, à ne pas confondre :

* **Dans le scénario** — validation *souple*, adaptée à l'exploration :
  dur (lève) sur ETD ≥ ETA, durée > 180 j, ports identiques ; simples
  avertissements sur chevauchement navire, rupture de continuité géographique,
  vitesse implicite invraisemblable. On explore, on ne s'engage pas.
* **À l'application au réel** — validation *du moteur réel*. ``apply`` délègue
  à ``planning.update_leg`` : mêmes contrôles durs (``validate_leg_schedule``),
  même cascade (``date_cascade.cascade_from_leg`` : legs aval, opérations
  d'escale, shifts dockers, notifications client), même refus de déplacer un
  leg déjà appareillé. Un scénario ne doit jamais pouvoir poser dans le réel ce
  que l'écran de planification refuserait.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import LEG_ORIGIN_TOWT, Leg
from app.models.planning_scenario import PlanningScenario, ScenarioLeg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.geo import leg_trade_category
from app.services.planning import (
    MAX_PLAUSIBLE_SPEED_KN,
    InvalidLegDates,
    effective_eta,
    effective_etd,
    ensure_utc,
    update_leg,
    validate_dates,
)


class ScenarioError(Exception):
    """Erreur métier scénario."""


@dataclass(frozen=True)
class ScenarioApplyResult:
    updated_legs: int
    changed_legs: int
    renumbered: list[tuple[int, str, str]]
    #: Identifiant du lot d'application — repris au journal d'activité.
    batch_id: str | None = None


#: Champs qu'une traversée provisoire peut poser sur un leg réel. Liste
#: explicite : elle sert à la fois à décider si un leg change (donc s'il faut
#: l'écrire) et à borner ce que ``apply`` transmet à ``update_leg``. Un champ
#: ajouté ici doit exister des deux côtés.
_APPLIED_FIELDS: tuple[str, ...] = (
    "vessel_id",
    "departure_port_id",
    "arrival_port_id",
    "etd",
    "eta",
    "port_stay_planned_hours",
    "transit_speed_kn",
    "elongation_coef",
)


def _by_label(legs: Sequence[ScenarioLeg], label: str) -> ScenarioLeg:
    """Traversée provisoire portant ce label (unicité déjà vérifiée)."""
    return next(li for li in legs if (li.label or "").strip() == label)


def _differs_from_scenario(real: Leg, sc_leg: ScenarioLeg) -> bool:
    """Le leg réel diffère-t-il de l'hypothèse ?

    Sert de garde d'écriture **et** de garde de refus : un leg appareillé que
    le scénario ne change pas n'a pas à bloquer l'application — il se contente
    d'être repris à l'identique.

    Les dates passent par ``ensure_utc`` avant comparaison. Les deux objets ne
    viennent pas de la même source (le leg réel est relu par un ``select``, la
    traversée provisoire est en session) et un ``DateTime(timezone=True)``
    revient **naïf** sous SQLite : comparer sans normaliser déclarait
    différentes deux dates identiques, et faisait refuser une application qui
    ne déplaçait rien.
    """
    for field in _APPLIED_FIELDS:
        left, right = getattr(real, field), getattr(sc_leg, field)
        if isinstance(left, datetime) or isinstance(right, datetime):
            left, right = ensure_utc(left), ensure_utc(right)
        if left != right:
            return True
    return False


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

    **Dates effectives, pas prévisionnelles.** Un leg déjà parti a un ATD : le
    cloner sur son ETD reproduirait le plan, pas la réalité, et le scénario
    partirait d'une image périmée exactement là où elle compte le plus — sur le
    voyage en cours. Même règle que la dérive, le Gantt et l'audit de séquence
    (``planning.effective_etd/effective_eta`` : le réel prime, le prévisionnel
    est le repli).
    """
    # Les archives TOWT (ADR-014) ne se clonent pas : un scénario ne rejoue
    # pas le passé de l'ancienne compagnie, et ``apply`` ne doit jamais le viser.
    stmt = select(Leg).where(Leg.origin != LEG_ORIGIN_TOWT).order_by(Leg.etd.asc())
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
                etd=effective_etd(leg),
                eta=effective_eta(leg),
                label=leg.leg_code,
                status=(
                    leg.status
                    if leg.status in {"planned", "in_progress", "completed", "cancelled"}
                    else "planned"
                ),
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
        status=(
            status if status in {"planned", "in_progress", "completed", "cancelled"} else "planned"
        ),
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
    cascade: bool = True,
) -> ScenarioLeg:
    new_dep = departure_port_id if departure_port_id is not None else leg.departure_port_id
    new_arr = arrival_port_id if arrival_port_id is not None else leg.arrival_port_id
    new_etd = etd or leg.etd
    new_eta = eta or leg.eta
    _validate_leg_inputs(
        departure_port_id=new_dep, arrival_port_id=new_arr, etd=new_etd, eta=new_eta
    )

    # Repères AVANT modification — frontière et delta de la cascade aval.
    old_etd = leg.etd
    dates_changed = (new_etd != leg.etd) or (new_eta != leg.eta)

    if vessel_id is not None:
        leg.vessel_id = vessel_id
    leg.departure_port_id = new_dep
    leg.arrival_port_id = new_arr
    leg.etd = new_etd
    leg.eta = new_eta
    if label is not None:
        leg.label = label.strip() or None
    if status is not None and status in {"planned", "in_progress", "completed", "cancelled"}:
        leg.status = status
    if port_stay_planned_hours is not None:
        leg.port_stay_planned_hours = port_stay_planned_hours
    if transit_speed_kn is not None:
        leg.transit_speed_kn = transit_speed_kn
    if elongation_coef is not None:
        leg.elongation_coef = elongation_coef
    if notes is not None:
        leg.notes = notes.strip() or None

    if cascade and dates_changed:
        await _cascade_downstream(db, leg, old_etd=old_etd)
    await db.flush()
    return leg


async def _cascade_downstream(
    db: AsyncSession, leg: ScenarioLeg, *, old_etd: datetime
) -> list[int]:
    """Recalcule les legs aval du même navire pour éviter les chevauchements.

    Deux passes :
      1. **Décalage rigide** : si l'ETD a bougé, on translate tous les legs aval
         (même navire, même scénario, ETD > ancien ETD) du même delta — la
         planification relative est préservée (comme le moteur réel).
      2. **Résolution des chevauchements** : on parcourt ensuite les legs aval
         par ETD croissant et on repousse vers l'avant tout leg qui démarrerait
         avant la fin (ETA) du leg précédent — en conservant sa durée. Couvre
         aussi l'allongement d'une escale (ETA étendue) sans décalage d'ETD.

    Renvoie la liste des ids de legs aval effectivement déplacés.
    """
    delta = leg.etd - old_etd
    downstream = list(
        (
            await db.execute(
                select(ScenarioLeg)
                .where(ScenarioLeg.scenario_id == leg.scenario_id)
                .where(ScenarioLeg.vessel_id == leg.vessel_id)
                .where(ScenarioLeg.id != leg.id)
                .where(ScenarioLeg.etd > old_etd)
                .order_by(ScenarioLeg.etd.asc())
            )
        )
        .scalars()
        .all()
    )
    moved: set[int] = set()

    # 1. Décalage rigide du delta (préserve les intervalles entre legs).
    if delta:
        for dl in downstream:
            dl.etd = dl.etd + delta
            dl.eta = dl.eta + delta
            moved.add(dl.id)

    # 2. Résolution des chevauchements résiduels (jamais vers le passé) — un
    #    leg démarre au plus tôt à la DISPONIBILITÉ du précédent : ETA + escale
    #    planifiée (même règle que ``planning.plan_downstream_shifts``).
    from datetime import timedelta as _td

    from app.services.planning import DEFAULT_PORT_STAY_HOURS

    def _ready(x):
        return x.eta + _td(hours=x.port_stay_planned_hours or DEFAULT_PORT_STAY_HOURS)

    prev_ready = _ready(leg)
    for dl in sorted(downstream, key=lambda x: x.etd):
        if dl.etd < prev_ready:
            push = prev_ready - dl.etd
            dl.etd = dl.etd + push
            dl.eta = dl.eta + push
            moved.add(dl.id)
        prev_ready = max(prev_ready, _ready(dl))

    return sorted(moved)


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
                        f"{label} → {nlabel} : rupture de continuité (arrivée ≠ départ suivant)."
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
                    "category": leg_trade_category(
                        pol.country if pol else None, pod.country if pod else None
                    ),
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
    in_window = [li for li in scenario_legs if li.eta >= window_start and li.etd <= window_end]
    return ComparisonStat(
        scenario_legs=len(in_window),
        real_legs=len(real_legs),
        scenario_sea_days=_sea_days(in_window),
        real_sea_days=_sea_days(real_legs),
    )


async def apply_to_active_planning(
    db: AsyncSession,
    scenario: PlanningScenario,
    *,
    user_id: int | None = None,
    user_name: str | None = None,
) -> ScenarioApplyResult:
    """Applique un scénario cloné au planning actif (``legs``).

    **Seul chemin d'écriture réelle du module.** Il délègue à
    ``planning.update_leg`` plutôt que d'écrire les champs à la main : c'est ce
    qui lui donne, sans les recopier, les garanties du moteur de planification —
    ``validate_leg_schedule`` (chevauchement, continuité géographique, vitesse
    plausible), le refus de déplacer un leg **déjà appareillé**, la cascade
    ``date_cascade.cascade_from_leg`` (legs aval, opérations d'escale, shifts
    dockers, **notifications client**), l'historisation dans
    ``schedule_revisions`` et la renumérotation.

    L'ancienne version posait ``real.etd``/``real.eta`` directement. Elle
    pouvait donc reculer un leg parti (son ATD devenait postérieur à son ETD),
    créer un chevauchement que l'écran de planification refuse, et surtout ne
    prévenait ni l'escale, ni les dockers, ni les clients. Un scénario ne doit
    jamais pouvoir poser dans le réel ce que la planification refuserait.

    Garde-fous propres à l'application, vérifiés **avant la première écriture** :
      - scénario non archivé ;
      - chaque ``ScenarioLeg`` porte un label correspondant à un ``leg_code``
        réel, sans doublon ;
      - aucun avertissement de cohérence sur le scénario complet ;
      - **aucun leg visé n'a appareillé** (ATD posé) — cf. ci-dessous ;
      - dates et ports valides.
    """
    if scenario.status == "archived":
        raise ScenarioError("Un scénario archivé ne peut pas être appliqué au planning actif.")

    legs = await list_scenario_legs(db, scenario.id)
    if not legs:
        raise ScenarioError("Le scénario ne contient aucune traversée à appliquer.")

    labels = [(leg.label or "").strip() for leg in legs]
    if any(not label for label in labels):
        raise ScenarioError("Chaque traversée doit porter le code du leg réel à mettre à jour.")
    duplicates = {label for label in labels if labels.count(label) > 1}
    if duplicates:
        raise ScenarioError("Labels dupliqués dans le scénario : " + ", ".join(sorted(duplicates)))

    real_legs = {
        leg.leg_code: leg
        for leg in (await db.execute(select(Leg).where(Leg.leg_code.in_(labels)))).scalars().all()
    }
    missing = sorted(set(labels) - set(real_legs))
    if missing:
        raise ScenarioError(
            "Application impossible : aucun leg actif ne correspond à " + ", ".join(missing)
        )

    port_ids = {leg.departure_port_id for leg in legs} | {leg.arrival_port_id for leg in legs}
    ports = (
        {
            p.id: p
            for p in (await db.execute(select(Port).where(Port.id.in_(port_ids)))).scalars().all()
        }
        if port_ids
        else {}
    )
    warnings = scenario_warnings(legs, ports)
    if warnings:
        raise ScenarioError(
            "Le scénario contient encore des incohérences : " + " | ".join(warnings[:5])
        )

    # Prévalidation dure : dates et ports identiques.
    for sc_leg in legs:
        _validate_leg_inputs(
            departure_port_id=sc_leg.departure_port_id,
            arrival_port_id=sc_leg.arrival_port_id,
            etd=sc_leg.etd,
            eta=sc_leg.eta,
        )

    # ── Legs déjà appareillés : refus AVANT toute écriture ────────────────
    #
    # Un ATD est un fait, pas une hypothèse. Le moteur réel ne déplace jamais un
    # leg parti (``plan_downstream_shifts`` lève ``LegOverlap``) ; l'application
    # d'un scénario ne doit pas être la porte dérobée qui le fait. On refuse ici,
    # en bloc et en nommant les legs, plutôt que de laisser ``update_leg`` s'en
    # apercevoir au milieu du lot : la prévalidation complète avant la première
    # écriture est la promesse de cette fonction.
    sailed = [
        (label, real_legs[label])
        for label in labels
        if real_legs[label].atd is not None
        and _differs_from_scenario(real_legs[label], _by_label(legs, label))
    ]
    if sailed:
        codes = ", ".join(sorted(label for label, _ in sailed))
        raise ScenarioError(
            f"Application impossible : {codes} a déjà appareillé (ATD posé). "
            "Un départ réel ne se replanifie pas — retirez ces traversées du "
            "scénario, ou corrigez-les depuis la fiche du voyage."
        )

    batch_id = uuid.uuid4().hex[:12]
    changed = 0
    renumbered: list[tuple[int, str, str]] = []
    for sc_leg in sorted(legs, key=lambda li: li.etd):
        label = (sc_leg.label or "").strip()
        real = real_legs[label]
        if not _differs_from_scenario(real, sc_leg):
            continue
        # ``update_leg`` porte l'intégralité des contrôles et de la propagation :
        # archive TOWT, validation dure, cascade aval, escale, dockers,
        # notifications, historisation, renumérotation. On ne réécrit rien de
        # tout cela ici — le dupliquer, c'est le laisser diverger.
        report = await update_leg(
            db,
            real,
            vessel_id=sc_leg.vessel_id,
            etd=sc_leg.etd,
            eta=sc_leg.eta,
            departure_port_id=sc_leg.departure_port_id,
            arrival_port_id=sc_leg.arrival_port_id,
            transit_speed_kn=sc_leg.transit_speed_kn,
            elongation_coef=sc_leg.elongation_coef,
            port_stay_planned_hours=sc_leg.port_stay_planned_hours,
            source="scenario_apply",
            actor_id=user_id,
            actor_name=user_name,
        )
        if report is not None:
            renumbered += report.renumbered
        changed += 1

    await db.flush()
    # ``batch_id`` reste exposé pour le journal d'activité de la route : il
    # identifie l'application comme un lot, même si l'historisation fine est
    # désormais posée leg par leg par ``update_leg``.
    scenario.updated_at = datetime.now(UTC)
    return ScenarioApplyResult(
        updated_legs=len(legs),
        changed_legs=changed,
        renumbered=renumbered,
        batch_id=batch_id,
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
