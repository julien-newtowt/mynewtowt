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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import Leg
from app.models.vessel import Vessel


async def build_leg_filter(
    db: AsyncSession,
    *,
    vessel: str | None = None,
    year: int | None = None,
    leg_id: int | None = None,
) -> dict:
    """Construit le contexte du filtre navire × année × leg (lecture seule)."""
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
