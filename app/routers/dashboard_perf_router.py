"""Dashboard Performance Environnementale v2 — reconstruction NC-01/NC-04.

Remplace progressivement ``dashboard_env_router.py`` (décommissionnement
prévu, action 6 du plan d'audit — non encore engagé, les deux coexistent
pour l'instant sans se toucher). Deux différences structurantes avec
l'ancien dashboard :

- ``kpi_env.fleet_summary`` est appelé avec ``strict=True`` : les voyages
  dont la donnée n'est pas ``source="events"`` (repli ``legacy_noon``/
  ``legacy_kpi``/``none``) sont exclus des totaux, jamais mélangés en
  silence. Leur nombre (``legs_excluded_non_event``) est affiché
  explicitement plutôt qu'omis (NC-04).
- Les signatures/dataclasses consommées (``FleetSummary``, ``VesselKpiBlock``,
  etc.) sont celles figées par le contrat d'interface NC-01
  (``tests/regression/test_dashboard_contract.py``,
  ``kpi_env.DASHBOARD_CONTRACT_VERSION``).

Expose pour l'instant uniquement :
    GET /dashboard-perf, /dashboard-perf/   page 1 — Vue flotte (perm kpi:C)

Les pages suivantes (suivi opérationnel, détail voyage, qualité,
administration) suivront dans des commits ultérieurs, sur le même modèle.

Calcul serveur exclusivement (même posture que ``dashboard_env_router``) :
ce routeur résout les paramètres HTTP (période/méthode/navire) + la
géométrie SVG de la tendance (server-rendered, pas de lib CDN) et appelle
``services.kpi_env`` — aucune formule n'est recalculée ici ni côté client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.kpi_env import EF_METHODS, PROVISIONAL_DASHBOARD_PARAMS, TrendPoint, fleet_summary
from app.templating import templates

router = APIRouter(prefix="/dashboard-perf", tags=["dashboard-perf"])

# Géométrie SVG identique à celle de dashboard_env_router._trend_bars — même
# convention (server-rendered, pas de lib CDN), dupliquée volontairement ici
# plutôt qu'importée depuis un module voué au décommissionnement (NC-05).
_CHART_WIDTH = 700
_CHART_HEIGHT = 190
_CHART_TOP = 12
_CHART_BOTTOM = 26
_CHART_SIDE = 8
_CHART_GAP = 8


def _trend_bars(trend: list[TrendPoint], trend_max_t: Decimal) -> tuple[list[dict], dict]:
    """Coordonnées des barres du graphe tendance 12 mois — SVG server-rendered."""
    n = len(trend)
    plot_h = _CHART_HEIGHT - _CHART_TOP - _CHART_BOTTOM
    plot_w = _CHART_WIDTH - 2 * _CHART_SIDE
    bar_w = ((plot_w - _CHART_GAP * (n - 1)) / n) if n else 0.0
    max_f = float(trend_max_t) if trend_max_t else 1.0

    bars: list[dict] = []
    for i, point in enumerate(trend):
        value_f = float(point.co2_emitted_t)
        bar_h = (value_f / max_f) * plot_h if max_f > 0 else 0.0
        x = _CHART_SIDE + i * (bar_w + _CHART_GAP)
        y = _CHART_TOP + (plot_h - bar_h)
        bars.append(
            {
                "x": round(x, 1),
                "y": round(y, 1),
                "width": round(max(bar_w, 0.0), 1),
                "height": round(max(bar_h, 0.0), 1),
                "label": point.label,
                "value": point.co2_emitted_t,
                "label_x": round(x + bar_w / 2, 1),
            }
        )
    meta = {
        "width": _CHART_WIDTH,
        "height": _CHART_HEIGHT,
        "baseline_y": _CHART_TOP + plot_h,
        "label_y": _CHART_HEIGHT - 8,
    }
    return bars, meta


# ═══════════════════════════════════════════════ Page 1 — Vue flotte (kpi:C)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard_perf_fleet(
    request: Request,
    year: int | None = None,
    method: str = "A",
    vessel: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("kpi", "C")),
) -> HTMLResponse:
    """Page 1 — Vue flotte, exclusivement event-driven (NC-04 : ``strict=True``).

    Filtres période/navire/méthode propagés en query string (même convention
    HTMX que l'ancien dashboard) ; renvoie le fragment
    (``_fleet_fragment.html``) quand ``HX-Request`` est présent, sinon la
    page complète.
    """
    if method not in EF_METHODS:
        method = "A"

    now = datetime.now(UTC)
    period = year or now.year

    vessels = list(
        (await db.execute(select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.name)))
        .scalars()
        .all()
    )
    selected_vessel = None
    if vessel:
        selected_vessel = next((v for v in vessels if v.code == vessel), None)

    summary = await fleet_summary(
        db,
        period=period,
        method=method,
        vessel_id=selected_vessel.id if selected_vessel else None,
        now=now,
        strict=True,
    )

    years = list(range(now.year - 3, now.year + 1))
    trend_bars, trend_chart = _trend_bars(summary.trend, summary.trend_max_t)

    ctx = {
        "request": request,
        "user": user,
        "summary": summary,
        "vessels": vessels,
        "selected_vessel_code": vessel or "",
        "method": method,
        "ef_methods": EF_METHODS,
        "year": period,
        "years": years,
        "provisional_params": PROVISIONAL_DASHBOARD_PARAMS,
        "trend_bars": trend_bars,
        "trend_chart": trend_chart,
    }
    template_name = (
        "staff/dashboard_perf/_fleet_fragment.html"
        if request.headers.get("hx-request")
        else "staff/dashboard_perf/index.html"
    )
    return templates.TemplateResponse(template_name, ctx)
