"""Trace réellement parcourue — agrégation des positions satcom par leg / période.

Les positions (`vessel_positions`) ne portent pas de ``leg_id`` : on associe
une position à un leg par **fenêtre temporelle** (même navire + ``recorded_at``
entre le départ et l'arrivée du leg). Ce module centralise :

- la résolution de la fenêtre d'un leg (``leg_window``) ;
- la récupération des positions d'un leg ou d'une période arbitraire ;
- les métriques de navigation (distance réelle, distance théorique, distance
  restante, durée depuis le départ) ;
- le sous-échantillonnage à 30 min pour la météo (1 appel Windy / point).

Lecture seule : aucune écriture en base.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim import VesselPosition
from app.models.leg import Leg
from app.models.port import Port
from app.services.planning import ensure_utc
from app.services.ports import haversine_nm

# Pas d'échantillon météo plus rapproché que cet intervalle (minutes).
WEATHER_SAMPLE_MINUTES = 30


@dataclass(frozen=True)
class TrackMetrics:
    """Résumé chiffré d'une trace réelle (toutes distances en milles nautiques)."""

    point_count: int
    actual_nm: float  # distance réellement parcourue (somme des segments)
    theoretical_nm: float | None  # distance orthodromique POL→POD
    remaining_nm: float | None  # distance restante (dernier point → POD)
    duration_hours: float | None  # durée écoulée depuis le départ
    avg_speed_kn: float | None  # vitesse moyenne réelle
    is_active: bool  # leg en cours (pas encore arrivé)
    # True quand la théorique n'est pas celle persistée sur le leg mais un
    # repli calculé au rendu depuis les coordonnées des ports (legs anciens
    # ou créés avant que la distance ne soit calculée à l'enregistrement).
    theoretical_is_fallback: bool = False

    @property
    def real_elongation(self) -> float | None:
        """TRK-03 — ratio d'allongement réel = distance GPS / orthodromique.

        > 1 = route plus longue que l'orthodromie (cap au vent, contournements).
        ``None`` si l'orthodromique est inconnue ou nulle.

        ``None`` **aussi tant que le leg est en cours** : sur un voyage non
        arrivé, ``actual_nm`` est un trajet PARTIEL et l'orthodromie couvre le
        voyage ENTIER. Le rapport n'est alors pas un allongement mais un taux
        d'avancement — constaté en production le 2026-09-03 sur 1CFRBR6, qui
        affichait « allongement ×0.07 » (400 NM parcourus sur 5799). Un
        allongement est par définition ≥ 1 : une route réelle est plus longue
        que l'orthodromie, jamais quatorze fois plus courte. Cf.
        ``progress_ratio`` pour la grandeur qui a un sens en cours de route.
        """
        if self.is_active:
            return None
        if self.theoretical_nm and self.theoretical_nm > 0:
            return round(self.actual_nm / self.theoretical_nm, 3)
        return None

    @property
    def progress_ratio(self) -> float | None:
        """Part du voyage parcourue (0 → 1), la grandeur qui a un sens en cours.

        C'est ce que valait l'ancien « allongement » sur un leg en mer, sous un
        nom qui affirmait autre chose.
        """
        if self.theoretical_nm and self.theoretical_nm > 0:
            return round(min(self.actual_nm / self.theoretical_nm, 1.0), 3)
        return None

    @property
    def deviation_nm(self) -> float | None:
        """Écart réel − théorique, **seulement sur un voyage arrivé**.

        Sur un leg en cours, cette soustraction ne mesure rien : elle renvoie
        l'opposé du reste à parcourir (« −5399 NM » sur 1CFRBR6), ce que la
        colonne « restante » dit déjà, et correctement.
        """
        if self.is_active or self.theoretical_nm is None:
            return None
        return round(self.actual_nm - self.theoretical_nm, 2)


def leg_window(leg: Leg, *, now: datetime | None = None) -> tuple[datetime, datetime, bool]:
    """(start, end, is_active) pour un leg.

    - start = ATD si disponible, sinon ETD (départ planifié) ;
    - end   = ATA si arrivé, sinon ``now`` (leg en cours) ;
    - is_active = leg **réellement parti** (ATD posée) et **pas encore arrivé**
      (ATA absente). Un leg futur (sans ATD) n'est donc PAS « en mer ».
    """
    now = ensure_utc(now) or datetime.now(UTC)
    start = ensure_utc(leg.atd or leg.etd)
    is_active = leg.atd is not None and leg.ata is None
    end = ensure_utc(leg.ata) or now
    # Garde-fou : si l'horloge serveur est en amont du départ planifié.
    # ``start`` est garanti non-None (``Leg.etd`` est NOT NULL et ``ensure_utc``
    # préserve la nullité) — inutile de le tester.
    if end < start:
        end = start
    return start, end, is_active


async def positions_in_window(
    db: AsyncSession,
    *,
    vessel_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
    light: bool = False,
) -> list[VesselPosition]:
    """Positions d'un navire dans une fenêtre [start, end], triées chronologiquement.

    ``light=True`` renvoie des lignes allégées (``recorded_at``, ``latitude``,
    ``longitude``, ``sog_kn``, ``cog_deg`` — mêmes attributs, pas d'instance ORM
    ni d'identity map) : indispensable pour les traces d'archive au pas de 5 min
    (≈ 6 000 points par leg, ≈ 105 000 par navire et par an, ADR-014).
    """
    if light:
        stmt = select(
            VesselPosition.recorded_at,
            VesselPosition.latitude,
            VesselPosition.longitude,
            VesselPosition.sog_kn,
            VesselPosition.cog_deg,
        ).where(VesselPosition.vessel_id == vessel_id)
    else:
        stmt = select(VesselPosition).where(VesselPosition.vessel_id == vessel_id)
    if start is not None:
        stmt = stmt.where(VesselPosition.recorded_at >= start)
    if end is not None:
        stmt = stmt.where(VesselPosition.recorded_at <= end)
    stmt = stmt.order_by(VesselPosition.recorded_at.asc())
    result = await db.execute(stmt)
    return list(result.all()) if light else list(result.scalars().all())


async def positions_for_leg(
    db: AsyncSession, leg: Leg, *, now: datetime | None = None, light: bool = False
) -> list[VesselPosition]:
    """Positions satcom rattachées à un leg (même navire, fenêtre départ→arrivée)."""
    start, end, _ = leg_window(leg, now=now)
    return await positions_in_window(db, vessel_id=leg.vessel_id, start=start, end=end, light=light)


# SEC-05 — au-delà de cette vitesse implicite, un segment est considéré comme
# un saut satcom aberrant (point GPS corrompu) et exclu du cumul. Seuil
# généreux (très au-dessus de la vitesse d'un voilier-cargo) : on ne filtre que
# l'impossible physique. Approche vitesse plutôt que seuil NM fixe (V2 = 50 NM,
# valable seulement à cadence horaire) car robuste aux écarts de cadence.
MAX_PLAUSIBLE_SPEED_KN = 30.0


def actual_distance_nm(
    positions: list[VesselPosition],
    *,
    max_speed_kn: float | None = None,
) -> float:
    """Distance réellement parcourue = somme des sauts haversine entre points.

    Si ``max_speed_kn`` est fourni, les segments dont la vitesse implicite
    (distance / durée) dépasse ce seuil — ou dont la durée est nulle alors que
    la distance ne l'est pas — sont exclus (filtre anti-saut satcom, SEC-05).
    """
    total = 0.0
    for a, b in pairwise(positions):
        seg = haversine_nm(a.latitude, a.longitude, b.latitude, b.longitude)
        if max_speed_kn is not None and seg > 0:
            hours = (b.recorded_at - a.recorded_at).total_seconds() / 3600.0
            if hours <= 0 or (seg / hours) > max_speed_kn:
                continue  # saut aberrant — ignoré
        total += seg
    return total


def theoretical_distance_nm(
    leg: Leg,
    *,
    dep_port: Port | None = None,
    arr_port: Port | None = None,
) -> tuple[float | None, bool]:
    """(distance théorique, repli ?) du leg.

    Priorité à ``leg.distance_nm`` (persistée au create/update). À défaut, on
    la **recalcule au rendu** depuis les coordonnées des ports — sinon les
    colonnes THÉORIQUE / ÉCART / ALLONG. restent vides pour tous les legs dont
    la distance n'a jamais été persistée, sans que rien ne l'explique.

    Renvoie ``(None, False)`` quand même le repli est impossible (port sans
    coordonnées) : c'est le seul cas où l'UI doit afficher « — »
    (``audit_planning_sequence`` nomme alors le port fautif).
    """
    if leg.distance_nm is not None:
        return float(leg.distance_nm), False
    if (
        dep_port is None
        or arr_port is None
        or dep_port.latitude is None
        or dep_port.longitude is None
        or arr_port.latitude is None
        or arr_port.longitude is None
    ):
        return None, False
    gc = haversine_nm(dep_port.latitude, dep_port.longitude, arr_port.latitude, arr_port.longitude)
    # Même convention que ``planning.compute_effective_distance_nm`` :
    # orthodromie × coefficient d'élongation du leg (1.0 s'il est absent —
    # le défaut navire n'est pas accessible sans requête DB).
    return round(gc * (leg.elongation_coef or 1.0), 2), True


def compute_metrics(
    positions: list[VesselPosition],
    leg: Leg,
    *,
    dep_port: Port | None = None,
    arr_port: Port | None = None,
    now: datetime | None = None,
) -> TrackMetrics:
    """Métriques de navigation d'un leg à partir de ses positions réelles."""
    now = now or datetime.now(UTC)
    start, _end, is_active = leg_window(leg, now=now)

    # Filtre anti-saut actif sur la métrique consommée par l'UI (SEC-05).
    actual = actual_distance_nm(positions, max_speed_kn=MAX_PLAUSIBLE_SPEED_KN)

    theoretical, theoretical_is_fallback = theoretical_distance_nm(
        leg, dep_port=dep_port, arr_port=arr_port
    )

    # Distance restante : dernier point connu → port d'arrivée (0 si arrivé).
    remaining: float | None = None
    if leg.ata is not None:
        remaining = 0.0
    elif (
        positions and arr_port and arr_port.latitude is not None and arr_port.longitude is not None
    ):
        last = positions[-1]
        remaining = haversine_nm(
            last.latitude, last.longitude, arr_port.latitude, arr_port.longitude
        )

    # Durée depuis le départ : ATD/ETD → ATA si arrivé, sinon dernier point / maintenant.
    # ``ensure_utc`` obligatoire : ``leg.ata`` et ``recorded_at`` reviennent
    # naïfs sous SQLite alors que ``start`` (issu de ``leg_window``) est aware.
    end_ref = ensure_utc(leg.ata) or ensure_utc(positions[-1].recorded_at if positions else now)
    duration_hours: float | None = None
    if end_ref and start:
        duration_hours = max((end_ref - start).total_seconds() / 3600.0, 0.0)

    avg_speed: float | None = None
    if duration_hours and duration_hours > 0 and actual > 0:
        avg_speed = actual / duration_hours

    return TrackMetrics(
        point_count=len(positions),
        actual_nm=actual,
        theoretical_nm=theoretical,
        remaining_nm=remaining,
        duration_hours=duration_hours,
        avg_speed_kn=avg_speed,
        is_active=is_active,
        theoretical_is_fallback=theoretical_is_fallback,
    )


@dataclass(frozen=True)
class NavigationKpiRow:
    """Ligne agrégée de la vue KPI navigation annuelle (TRK-02).

    Un leg de l'année qui porte au moins un point GPS : ses métriques de
    navigation (``metrics``) plus les statistiques de vitesse fond relevée
    (``avg_sog_kn`` / ``max_sog_kn``, issues du champ ``sog_kn`` des positions,
    distinctes de ``metrics.avg_speed_kn`` qui est distance/durée).
    """

    leg: Leg
    metrics: TrackMetrics
    avg_sog_kn: float | None  # moyenne des SOG relevés (instantané satcom)
    max_sog_kn: float | None  # SOG max relevé


def sog_stats(positions: list[VesselPosition]) -> tuple[float | None, float | None]:
    """(moyenne, max) des vitesses fond relevées (``sog_kn``), ``(None, None)``
    si aucune position ne porte de SOG."""
    sogs = [float(p.sog_kn) for p in positions if p.sog_kn is not None]
    if not sogs:
        return (None, None)
    return (round(sum(sogs) / len(sogs), 2), round(max(sogs), 2))


async def annual_navigation_kpis(
    db: AsyncSession,
    year: int,
    *,
    vessel_id: int | None = None,
    now: datetime | None = None,
) -> list[NavigationKpiRow]:
    """KPI navigation agrégés — tous les legs à GPS d'une année (TRK-02).

    Restaure la vue V2 « tous les legs à positions GPS de l'année » : pour
    chaque leg dont l'ETD tombe dans ``year`` (optionnellement restreint à un
    navire via ``vessel_id``) et qui porte **au moins un point GPS**, calcule
    point_count, distance réelle/théorique, allongement, vitesse moyenne (= la
    métrique distance/durée) et les statistiques de SOG relevé (moyenne/max).

    Les legs sans aucune position GPS sont exclus (vue « performance réelle »).
    Tri par ETD croissant. Lecture seule.
    """
    now = now or datetime.now(UTC)
    stmt = select(Leg).order_by(Leg.etd.asc())
    if vessel_id is not None:
        stmt = stmt.where(Leg.vessel_id == vessel_id)
    # Filtre d'année côté Python (cohérent avec build_leg_filter), robuste aux
    # différences de dialecte sur extract('year', ...) entre Postgres et SQLite.
    legs = [lg for lg in (await db.execute(stmt)).scalars().all() if lg.etd and lg.etd.year == year]

    rows: list[NavigationKpiRow] = []
    for leg in legs:
        positions = await positions_for_leg(db, leg, now=now, light=True)
        if not positions:
            continue
        dep = await db.get(Port, leg.departure_port_id)
        arr = await db.get(Port, leg.arrival_port_id)
        metrics = compute_metrics(positions, leg, dep_port=dep, arr_port=arr, now=now)
        avg_sog, max_sog = sog_stats(positions)
        rows.append(
            NavigationKpiRow(
                leg=leg,
                metrics=metrics,
                avg_sog_kn=avg_sog,
                max_sog_kn=max_sog,
            )
        )
    return rows


async def navigation_aggregate(db: AsyncSession, legs, *, now: datetime | None = None) -> dict:
    """EVO-08 — agrégat des métriques de navigation sur un périmètre de legs.

    Pour les legs porteurs d'au moins un point GPS : distance réelle cumulée,
    allongement réel moyen, SOG moyen. Alimente la section Exploitation des KPI."""
    total_real = 0.0
    elongations: list[float] = []
    sogs: list[float] = []
    legs_with_gps = 0
    for leg in legs:
        positions = await positions_for_leg(db, leg, now=now)
        if not positions:
            continue
        legs_with_gps += 1
        dep = await db.get(Port, leg.departure_port_id) if leg.departure_port_id else None
        arr = await db.get(Port, leg.arrival_port_id) if leg.arrival_port_id else None
        m = compute_metrics(positions, leg, dep_port=dep, arr_port=arr, now=now)
        total_real += m.actual_nm
        if m.real_elongation is not None:
            elongations.append(m.real_elongation)
        avg_sog, _ = sog_stats(positions)
        if avg_sog is not None:
            sogs.append(avg_sog)
    return {
        "legs_with_gps": legs_with_gps,
        "total_real_nm": round(total_real, 1),
        "avg_elongation": round(sum(elongations) / len(elongations), 3) if elongations else None,
        "avg_sog_kn": round(sum(sogs) / len(sogs), 1) if sogs else None,
    }


def downsample_for_weather(
    positions: list[VesselPosition], *, minutes: int = WEATHER_SAMPLE_MINUTES
) -> list[VesselPosition]:
    """Garde au plus un point par intervalle de ``minutes`` (défaut 30 min).

    Le 1er et le dernier point sont toujours conservés. Sert à borner le nombre
    d'appels météo (1 appel Windy par point échantillonné).
    """
    if not positions:
        return []
    kept: list[VesselPosition] = [positions[0]]
    last_kept = positions[0].recorded_at
    threshold = minutes * 60
    for p in positions[1:]:
        if (p.recorded_at - last_kept).total_seconds() >= threshold:
            kept.append(p)
            last_kept = p.recorded_at
    if positions[-1] is not kept[-1]:
        kept.append(positions[-1])
    return kept


# Plafond de points sérialisés vers la carte d'historique. Les archives TOWT
# (ADR-014) sont au pas de 5 min : ~105 000 points par navire et par an — un
# JSON de plusieurs Mo dans la page. Le calcul de distance (``actual_distance_nm``)
# garde toujours la résolution complète ; seul l'AFFICHAGE est décimé.
MAX_TRACK_POINTS_HISTORY = 4000


def downsample(positions: Sequence[VesselPosition], *, max_points: int) -> list[VesselPosition]:
    """Décime une trace par pas régulier en conservant le premier et le dernier point.

    Ne modifie pas l'ordre ; ``max_points <= 0`` ou trace déjà courte → inchangé.
    """
    pts = list(positions)
    if max_points <= 0 or len(pts) <= max_points:
        return pts
    if max_points == 1:
        return [pts[0], pts[-1]]
    step = (len(pts) - 1) / (max_points - 1)
    keep = [pts[round(i * step)] for i in range(max_points - 1)]
    keep.append(pts[-1])
    return keep


def positions_payload(positions: list[VesselPosition]) -> list[dict]:
    """Sérialise des positions pour la carte (lat/lon/temps/SOG/COG)."""
    return [
        {
            "lat": p.latitude,
            "lon": p.longitude,
            "t": p.recorded_at.isoformat(),
            "sog": float(p.sog_kn) if p.sog_kn is not None else None,
            "cog": float(p.cog_deg) if p.cog_deg is not None else None,
        }
        for p in positions
    ]
