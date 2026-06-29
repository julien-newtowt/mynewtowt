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

from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim import VesselPosition
from app.models.leg import Leg
from app.models.port import Port
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

    @property
    def real_elongation(self) -> float | None:
        """TRK-03 — ratio d'allongement réel = distance GPS / orthodromique.

        > 1 = route plus longue que l'orthodromie (cap au vent, contournements).
        ``None`` si l'orthodromique est inconnue ou nulle.
        """
        if self.theoretical_nm and self.theoretical_nm > 0:
            return round(self.actual_nm / self.theoretical_nm, 3)
        return None


def leg_window(leg: Leg, *, now: datetime | None = None) -> tuple[datetime, datetime, bool]:
    """(start, end, is_active) pour un leg.

    - start = ATD si disponible, sinon ETD (départ planifié) ;
    - end   = ATA si arrivé, sinon ``now`` (leg en cours) ;
    - is_active = leg **réellement parti** (ATD posée) et **pas encore arrivé**
      (ATA absente). Un leg futur (sans ATD) n'est donc PAS « en mer ».
    """
    now = now or datetime.now(UTC)
    start = leg.atd or leg.etd
    is_active = leg.atd is not None and leg.ata is None
    end = leg.ata or now
    # Garde-fou : si l'horloge serveur est en amont du départ planifié.
    if end < start:
        end = start
    return start, end, is_active


async def positions_in_window(
    db: AsyncSession,
    *,
    vessel_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[VesselPosition]:
    """Positions d'un navire dans une fenêtre [start, end], triées chronologiquement."""
    stmt = select(VesselPosition).where(VesselPosition.vessel_id == vessel_id)
    if start is not None:
        stmt = stmt.where(VesselPosition.recorded_at >= start)
    if end is not None:
        stmt = stmt.where(VesselPosition.recorded_at <= end)
    stmt = stmt.order_by(VesselPosition.recorded_at.asc())
    return list((await db.execute(stmt)).scalars().all())


async def positions_for_leg(
    db: AsyncSession, leg: Leg, *, now: datetime | None = None
) -> list[VesselPosition]:
    """Positions satcom rattachées à un leg (même navire, fenêtre départ→arrivée)."""
    start, end, _ = leg_window(leg, now=now)
    return await positions_in_window(db, vessel_id=leg.vessel_id, start=start, end=end)


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


def compute_metrics(
    positions: list[VesselPosition],
    leg: Leg,
    *,
    arr_port: Port | None = None,
    now: datetime | None = None,
) -> TrackMetrics:
    """Métriques de navigation d'un leg à partir de ses positions réelles."""
    now = now or datetime.now(UTC)
    start, _end, is_active = leg_window(leg, now=now)

    # Filtre anti-saut actif sur la métrique consommée par l'UI (SEC-05).
    actual = actual_distance_nm(positions, max_speed_kn=MAX_PLAUSIBLE_SPEED_KN)

    theoretical: float | None = float(leg.distance_nm) if leg.distance_nm is not None else None

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
    end_ref = leg.ata or (positions[-1].recorded_at if positions else now)
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
        positions = await positions_for_leg(db, leg, now=now)
        if not positions:
            continue
        arr = await db.get(Port, leg.arrival_port_id)
        metrics = compute_metrics(positions, leg, arr_port=arr, now=now)
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
        arr = await db.get(Port, leg.arrival_port_id) if leg.arrival_port_id else None
        m = compute_metrics(positions, leg, arr_port=arr, now=now)
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
