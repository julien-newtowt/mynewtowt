"""Dashboard Performance Environnementale — LOT 11 (socle + pages 1 & 4).

Expose :
    GET  /dashboard-env                        page 1 — Vue flotte (perm kpi:C)
    GET  /dashboard-env/parameters              page 4 — Administration (perm mrv:S)
    POST /dashboard-env/parameters/{id}/update  édition d'un DashboardParameter (perm mrv:S)

Pages 2 (suivi opérationnel) et 3 (qualité des données) : LOT 12 (hors
périmètre — dépendent du modèle événementiel ``nav_events`` peuplé et du
moteur de règles complet, cf. plan §3 LOT 12).

Calcul serveur exclusivement : ce router ne fait que résoudre les
paramètres HTTP (période/méthode/navire) et appeler ``services.kpi_env`` —
aucune formule n'est recalculée ici ni côté client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.activity_log import ActivityLog
from app.models.validation import DashboardParameter
from app.models.vessel import Vessel
from app.permissions import has_permission_effective, require_permission
from app.services.activity import record as activity_record
from app.services.kpi_env import (
    EF_METHODS,
    PROVISIONAL_DASHBOARD_PARAMS,
    TrendPoint,
    fleet_summary,
)
from app.templating import templates

router = APIRouter(prefix="/dashboard-env", tags=["dashboard-env"])

# Borne défensive (cohérente avec la borne des seuils MRV — mrv_router.py) :
# ``dashboard_parameters.value`` est un Numeric(15, 6).
_MAX_PARAM_VALUE = Decimal("1000000000")


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )


def _parse_param_value(raw: str) -> Decimal:
    """Coerce une saisie de paramètre en Decimal validé (sinon HTTP 400)."""
    try:
        value = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="valeur numérique invalide") from exc
    if not value.is_finite() or value < 0 or abs(value) >= _MAX_PARAM_VALUE:
        raise HTTPException(status_code=400, detail="valeur hors plage (0 ≤ x < 1e9)")
    return value


# ── Géométrie du graphe de tendance (SVG server-rendered, pas de lib CDN) ──
# Pattern des graphes KPI existants (cf. staff/stowage/_deck_svg.html,
# pdf/carnet_bord/chapitre_1_traversee.html) : coordonnées calculées ici
# (Python), le template ne fait qu'émettre des <rect>/<line> — aucun calcul
# de mise en page côté Jinja ni côté client.
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
async def dashboard_env_fleet(
    request: Request,
    year: int | None = None,
    method: str = "A",
    vessel: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("kpi", "C")),
) -> HTMLResponse:
    """Page 1 — Vue flotte : bandeau KPI, cartes par navire, tendance 12 mois.

    Filtres période/navire/méthode propagés en query string ; les
    changements de filtre sont des requêtes HTMX vers cette même route, qui
    renvoie uniquement le fragment (``_fleet_fragment.html``) quand
    ``HX-Request`` est présent — sinon la page complète.
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
    )

    # Le lien croisé vers la page Administration (mrv:S) n'est affiché que si
    # le rôle courant y a effectivement accès (matrice effective ARC-04).
    can_admin_params = await has_permission_effective(db, user.role, "mrv", "S")

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
        "can_admin_params": can_admin_params,
        "provisional_params": PROVISIONAL_DASHBOARD_PARAMS,
        "trend_bars": trend_bars,
        "trend_chart": trend_chart,
    }
    template_name = (
        "staff/dashboard_env/_fleet_fragment.html"
        if request.headers.get("hx-request")
        else "staff/dashboard_env/index.html"
    )
    return templates.TemplateResponse(template_name, ctx)


# ═══════════════════════════════════════════ Page 4 — Administration (mrv:S)


async def _history_for_params(
    db: AsyncSession, param_ids: list[int]
) -> dict[int, list[ActivityLog]]:
    """Historique des modifications par paramètre (journal ``activity_logs``).

    Pas de table dédiée (``dashboard_parameter_history``) — contrainte LOT
    11 « aucune migration » : l'audit trail append-only existant
    (``services.activity.record``, déjà écrit à chaque mise à jour) sert de
    source pour l'historique demandé par l'UX (§6.1), sans nouveau schéma.
    """
    if not param_ids:
        return {}
    rows = (
        (
            await db.execute(
                select(ActivityLog)
                .where(
                    ActivityLog.module == "dashboard_env",
                    ActivityLog.entity_type == "dashboard_parameter",
                    ActivityLog.entity_id.in_(param_ids),
                )
                .order_by(ActivityLog.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    by_param: dict[int, list[ActivityLog]] = {}
    for row in rows:
        if row.entity_id is None:
            continue
        by_param.setdefault(row.entity_id, []).append(row)
    return by_param


@router.get("/parameters", response_class=HTMLResponse)
async def dashboard_env_parameters(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "S")),
) -> HTMLResponse:
    """Page 4 — Administration : édition des ``dashboard_parameters``.

    Périmètre volontairement restreint à cette seule table (les seuils du
    moteur de règles ``ValidationRuleThreshold`` sont administrés par l'écran
    LOT 2 existant, ``/mrv/parametres`` — non dupliqué ici, seulement
    lié en croisé, de même que ``/admin/emission-factors`` pour les
    facteurs carburant, LOT 1).
    """
    params = list(
        (await db.execute(select(DashboardParameter).order_by(DashboardParameter.parameter_name)))
        .scalars()
        .all()
    )
    history_by_param = await _history_for_params(db, [p.id for p in params])
    vessels = list(
        (await db.execute(select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.name)))
        .scalars()
        .all()
    )
    vessel_by_id = {v.id: v for v in vessels}

    return templates.TemplateResponse(
        "staff/dashboard_env/parameters.html",
        {
            "request": request,
            "user": user,
            "params": params,
            "history_by_param": history_by_param,
            "vessel_by_id": vessel_by_id,
            "provisional_params": PROVISIONAL_DASHBOARD_PARAMS,
        },
    )


@router.post("/parameters/{param_id}/update")
async def dashboard_env_parameters_update(
    param_id: int,
    request: Request,
    value: str = Form(...),
    unit: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "S")),
):
    """Édite la valeur (et l'unité) d'un ``DashboardParameter`` — tracé en activity_log."""
    param = await db.get(DashboardParameter, param_id)
    if param is None:
        raise HTTPException(status_code=404, detail="paramètre inconnu")

    new_value = _parse_param_value(value)
    old_value, old_unit = param.value, param.unit
    param.value = new_value
    unit_clean = unit.strip()
    if unit_clean:
        param.unit = unit_clean[:20]
    param.updated_by = user.id
    await db.flush()

    await activity_record(
        db,
        action="dashenv_parameter_update",
        user_id=user.id,
        user_name=getattr(user, "full_name", None) or user.username,
        user_role=user.role,
        module="dashboard_env",
        entity_type="dashboard_parameter",
        entity_id=param.id,
        entity_label=param.parameter_name,
        detail=f"{old_value} {old_unit or ''} → {param.value} {param.unit or ''}".strip(),
        ip_address=_client_ip(request),
    )

    target = "/dashboard-env/parameters"
    if request.headers.get("hx-request"):
        return Response(status_code=200, headers={"HX-Redirect": target})
    return RedirectResponse(url=target, status_code=303)
