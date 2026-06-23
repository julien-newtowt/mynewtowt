"""FIN-07 — vue KPI consolidée (data analyst / direction).

Agrège en lecture seule les indicateurs des différentes sources V3 (Commerce,
Flotte/Ops, Environnement, Exploitation, Finance) pour offrir une **page
d'entrée unifiée** au data analyst — en remplacement du tableau de bord V2 à 5
onglets disparu. Chaque section pointe vers la vue détaillée correspondante.

Lecture seule : aucune écriture (n'auto-alimente pas les ``LegKPI`` — la page
``/kpi`` s'en charge ; on agrège ici les KPI déjà calculés de l'année).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import LegKPI
from app.models.leg import Leg
from app.services.co2 import co2_equivalences
from app.services.commercial_dashboard import commercial_totals
from app.services.dashboard_kpis import ca_previsionnel, fleet_kpis
from app.services.emissions import get_emission_factors
from app.services.exploitation import exploitation_summary
from app.services.insurance_kpi import claims_exposure
from app.services.kpi import aggregate_emissions


async def consolidated_kpis(
    db: AsyncSession, *, year: int | None = None, now: datetime | None = None
) -> dict:
    """Synthèse KPI consolidée (toutes sources).

    Renvoie un dict de sections : ``commerce``, ``fleet`` (+ ``ca_forecast_eur``),
    ``env``, ``exploitation``, plus ``on_time_pct`` et les compteurs de périmètre.

    **Portée** : ``env`` et ``exploitation`` sont **réalisés sur ``year``**
    (legs/KPI de l'année). ``commerce`` (carnet) et ``fleet`` / ``ca_forecast``
    (prévisionnel à venir) sont des indicateurs **globaux / instantanés**, non
    bornés à l'année — la page les présente comme tels.
    """
    now = now or datetime.now(UTC)
    year = year or now.year

    legs = [
        lg
        for lg in (await db.execute(select(Leg))).scalars().all()
        if lg.etd and lg.etd.year == year
    ]
    leg_ids = {lg.id for lg in legs}
    kpis = [k for k in (await db.execute(select(LegKPI))).scalars().all() if k.leg_id in leg_ids]

    # ── Commerce (CA réalisé, conversion offres) ──
    commerce = await commercial_totals(db)

    # ── Flotte / Ops (remplissage à venir + CO₂ évité prévisionnel) + CA prév. ──
    fleet = await fleet_kpis(db, now)
    ca_forecast = await ca_previsionnel(db)

    # ── Environnement (réalisé sur l'année, depuis les LegKPI) ──
    # Source unique de l'agrégat (partagée avec /kpi) pour éviter toute dérive
    # des chiffres réglementaires entre les deux pages.
    em_factors = await get_emission_factors(db)
    env, _by_leg = aggregate_emissions(kpis, em_factors)
    env["equiv"] = co2_equivalences(env["co2_avoided_kg"])

    # ── Exploitation (écart planning, durée mer, vitesse, ponctualité) ──
    exploitation = exploitation_summary(legs, kpis)
    on_time_pct = round(sum(1 for k in kpis if k.on_time) / len(kpis) * 100, 1) if kpis else 0.0

    # ── Exposition assurance / sinistres (FIN-06) — global, instantané ──
    insurance = await claims_exposure(db)

    return {
        "year": year,
        "leg_count": len(legs),
        "kpi_count": len(kpis),
        "commerce": commerce,
        "fleet": fleet,
        "ca_forecast_eur": ca_forecast,
        "env": env,
        "exploitation": exploitation,
        "on_time_pct": on_time_pct,
        "insurance": insurance,
    }
