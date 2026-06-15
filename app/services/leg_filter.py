"""Module de filtrage leg standard — navire × année × leg.

Composant transverse : toute page qui doit filtrer par navire / année / leg
(escale, KPI, plan de chargement, MRV…) construit son contexte via
``build_leg_filter`` et le rend avec la macro ``staff/_leg_filter.html``.

Contexte retourné (clés stables, consommées par la macro) ::

    {
      "vessels": [Vessel],        # tous les navires (onglets)
      "selected_vessel": str,     # code navire sélectionné (ou None)
      "years": [int],             # plage d'années (sélecteur)
      "current_year": int,        # année sélectionnée
      "legs": [Leg],              # legs du navire pour l'année (chips)
      "leg_id": int | None,       # leg sélectionné
      "selected_leg": Leg | None, # objet leg sélectionné
    }
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import Leg
from app.models.vessel import Vessel

if TYPE_CHECKING:
    from fastapi import Request, Response

# Cookie de persistance de la sélection (navire|année|leg) — permet aux pages
# du module opérations d'« hériter » du leg choisi sur /onboard sans repasser
# les query-params.
LEG_FILTER_COOKIE = "towt_leg_filter"
_COOKIE_MAX_AGE = 60 * 60 * 12  # 12 h


def _from_cookie(request: Request | None) -> tuple[str | None, int | None, int | None]:
    """Décode ``vessel|year|leg_id`` du cookie de filtre (tolérant)."""
    if request is None:
        return (None, None, None)
    raw = request.cookies.get(LEG_FILTER_COOKIE)
    if not raw:
        return (None, None, None)
    parts = [*raw.split("|"), "", "", ""][:3]
    vessel = parts[0] or None
    try:
        year = int(parts[1]) if parts[1] else None
    except ValueError:
        year = None
    try:
        leg_id = int(parts[2]) if parts[2] else None
    except ValueError:
        leg_id = None
    return (vessel, year, leg_id)


def set_leg_filter_cookie(response: Response, f: dict) -> None:
    """Persiste la sélection courante (à appeler sur la réponse d'une page)."""
    value = "|".join(
        [
            str(f.get("selected_vessel") or ""),
            str(f.get("current_year") or ""),
            str(f.get("leg_id") or ""),
        ]
    )
    response.set_cookie(
        LEG_FILTER_COOKIE,
        value,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )


async def build_leg_filter(
    db: AsyncSession,
    *,
    vessel: str | None = None,
    year: int | None = None,
    leg_id: int | None = None,
    request: Request | None = None,
) -> dict:
    """Construit le contexte du filtre navire × année × leg (lecture seule).

    Les paramètres absents (None) sont complétés depuis le cookie de filtre
    (``request``), pour que les pages du module opérations héritent du leg
    sélectionné ailleurs.
    """
    c_vessel, c_year, c_leg = _from_cookie(request)
    vessel = vessel or c_vessel
    year = year or c_year
    leg_id = leg_id if leg_id is not None else c_leg

    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    selected_vessel = vessel or (vessels[0].code if vessels else None)
    current_year = year or datetime.now(UTC).year
    years = list(range(current_year - 1, current_year + 3))

    stmt = select(Leg).order_by(Leg.etd.asc())
    v = next((x for x in vessels if x.code == selected_vessel), None) if selected_vessel else None
    if v is not None:
        stmt = stmt.where(Leg.vessel_id == v.id)
    legs = [
        lg
        for lg in (await db.execute(stmt)).scalars().all()
        if lg.etd and lg.etd.year == current_year
    ]

    selected_leg = await db.get(Leg, leg_id) if leg_id else None
    return {
        "vessels": vessels,
        "selected_vessel": selected_vessel,
        "years": years,
        "current_year": current_year,
        "legs": legs,
        "leg_id": leg_id,
        "selected_leg": selected_leg,
    }
