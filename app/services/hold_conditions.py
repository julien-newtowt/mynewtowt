"""Conditions de cale (température / humidité) — agrégation par leg.

Source : relevés ``noon_report_holds`` (formulaire officiel CFOTE_05 —
température °C et humidité relative % relevées à minuit et à midi, par
cale). C'est la matérialisation de la promesse commerciale « température
et humidité des cales surveillées en continu » : ce service prépare la
restitution client (``/me/bookings/{ref}``), expéditeur (``/p/{token}/voyage``),
publique (``/voyage/{ref}``) et Carnet de Bord (chapitre 4).

Les courbes sont rendues en polylignes SVG calculées côté serveur —
zéro JavaScript, compatible CSP stricte.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.noon_report import NOON_HOLD_LOCATIONS, NoonReport, NoonReportHold

# ViewBox des sparklines SVG (largeur × hauteur, marge intérieure).
SVG_WIDTH = 600
SVG_HEIGHT = 120
SVG_PAD = 6


@dataclass
class HoldSummary:
    """Synthèse min/moy/max d'une cale sur la traversée."""

    location: str
    temp_min: float | None = None
    temp_max: float | None = None
    temp_avg: float | None = None
    humidity_min: float | None = None
    humidity_max: float | None = None
    humidity_avg: float | None = None
    readings: int = 0


@dataclass
class HoldConditions:
    """Conditions de transport agrégées d'un leg (toutes cales)."""

    holds: list[HoldSummary]
    temp_avg: float | None
    temp_min: float | None
    temp_max: float | None
    humidity_avg: float | None
    humidity_min: float | None
    humidity_max: float | None
    readings: int
    first_at: datetime | None
    last_at: datetime | None
    # Séries journalières (une entrée par noon report) : moyennes toutes cales.
    series: list[dict[str, Any]]
    # Polylignes SVG prêtes à insérer dans <polyline points="…"> ("" si < 2 pts).
    temp_points: str
    humidity_points: str


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def svg_points(
    values: list[float | None],
    *,
    width: int = SVG_WIDTH,
    height: int = SVG_HEIGHT,
    pad: int = SVG_PAD,
) -> str:
    """Polyligne SVG normalisée pour une série (``None`` = point sauté).

    L'axe X répartit les points sur toute la largeur (indice dans la série),
    l'axe Y est normalisé entre min et max observés (série plate → ligne
    médiane). Renvoie ``""`` s'il y a moins de 2 points numériques.
    """
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return ""
    n = len(values)
    vmin = min(v for _, v in pts)
    vmax = max(v for _, v in pts)
    span = (vmax - vmin) or 1.0
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    coords = []
    for i, v in pts:
        x = pad + (i / (n - 1)) * inner_w
        y = pad + (1 - (v - vmin) / span) * inner_h
        coords.append(f"{x:.1f},{y:.1f}")
    return " ".join(coords)


def _row_values(row: NoonReportHold) -> tuple[list[float], list[float]]:
    temps = [v for v in (row.temp_midnight_c, row.temp_midday_c) if v is not None]
    hums = [v for v in (row.humidity_midnight_pct, row.humidity_midday_pct) if v is not None]
    return temps, hums


def _location_rank(location: str) -> tuple[int, str]:
    try:
        return (NOON_HOLD_LOCATIONS.index(location), location)
    except ValueError:
        return (len(NOON_HOLD_LOCATIONS), location)


async def for_leg(db: AsyncSession, leg_id: int) -> HoldConditions | None:
    """Agrège les relevés de cale d'un leg. ``None`` si aucun relevé exploitable."""
    reports = list(
        (
            await db.execute(
                select(NoonReport)
                .where(NoonReport.leg_id == leg_id)
                .order_by(NoonReport.recorded_at)
            )
        )
        .scalars()
        .all()
    )
    if not reports:
        return None
    report_ids = [r.id for r in reports]
    rows = list(
        (
            await db.execute(
                select(NoonReportHold).where(NoonReportHold.noon_report_id.in_(report_ids))
            )
        )
        .scalars()
        .all()
    )

    by_report: dict[int, list[NoonReportHold]] = {}
    by_location: dict[str, list[NoonReportHold]] = {}
    all_temps: list[float] = []
    all_hums: list[float] = []
    readings = 0
    for row in rows:
        temps, hums = _row_values(row)
        if not temps and not hums:
            continue
        readings += 1
        by_report.setdefault(row.noon_report_id, []).append(row)
        by_location.setdefault(row.location, []).append(row)
        all_temps.extend(temps)
        all_hums.extend(hums)

    if not readings:
        return None

    holds: list[HoldSummary] = []
    for location in sorted(by_location, key=_location_rank):
        loc_temps: list[float] = []
        loc_hums: list[float] = []
        for row in by_location[location]:
            temps, hums = _row_values(row)
            loc_temps.extend(temps)
            loc_hums.extend(hums)
        holds.append(
            HoldSummary(
                location=location,
                temp_min=min(loc_temps) if loc_temps else None,
                temp_max=max(loc_temps) if loc_temps else None,
                temp_avg=_avg(loc_temps),
                humidity_min=min(loc_hums) if loc_hums else None,
                humidity_max=max(loc_hums) if loc_hums else None,
                humidity_avg=_avg(loc_hums),
                readings=len(by_location[location]),
            )
        )

    series: list[dict[str, Any]] = []
    reported = [r for r in reports if r.id in by_report]
    for report in reported:
        day_temps: list[float] = []
        day_hums: list[float] = []
        for row in by_report[report.id]:
            temps, hums = _row_values(row)
            day_temps.extend(temps)
            day_hums.extend(hums)
        series.append(
            {
                "at": report.recorded_at,
                "temp": _avg(day_temps),
                "humidity": _avg(day_hums),
            }
        )

    return HoldConditions(
        holds=holds,
        temp_avg=_avg(all_temps),
        temp_min=min(all_temps) if all_temps else None,
        temp_max=max(all_temps) if all_temps else None,
        humidity_avg=_avg(all_hums),
        humidity_min=min(all_hums) if all_hums else None,
        humidity_max=max(all_hums) if all_hums else None,
        readings=readings,
        first_at=reported[0].recorded_at if reported else None,
        last_at=reported[-1].recorded_at if reported else None,
        series=series,
        temp_points=svg_points([p["temp"] for p in series]),
        humidity_points=svg_points([p["humidity"] for p in series]),
    )
