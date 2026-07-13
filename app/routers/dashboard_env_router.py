"""Dashboard Performance Environnementale — LOT 11 (pages 1 & 4) + LOT 12 (2 & 3).

Expose :
    GET  /dashboard-env                          page 1 — Vue flotte (perm kpi:C)
    GET  /dashboard-env/vessels/{vessel_id}      page 2 — Suivi opérationnel (perm kpi:C)
    GET  /dashboard-env/voyages/{leg_id}         page 2 — Détail voyage (perm mrv:C)
    GET  /dashboard-env/voyages/{leg_id}/export.pdf   synthèse voyage PDF (perm mrv:C)
    GET  /dashboard-env/voyages/{leg_id}/export.docx  synthèse voyage DOCX (perm mrv:C)
    GET  /dashboard-env/quality                  page 3 — Qualité des données (perm mrv:C)
    GET  /dashboard-env/parameters               page 4 — Administration (perm mrv:S)
    POST /dashboard-env/parameters/{id}/update   édition d'un DashboardParameter (perm mrv:S)

Les actions qualité (confirm-reset R10, acquittement) vivent dans
``mrv_router`` (LOT 8, ``/mrv/qualite/...``) : la page 3 pointe dessus (formulaire
POST direct + deep-links) et n'ajoute **aucune** route d'action.

Calcul serveur exclusivement : ce router résout les paramètres HTTP
(période/méthode/navire) + la géométrie SVG (server-rendered, pas de lib CDN)
et appelle ``services.kpi_env`` — aucune formule n'est recalculée ici ni côté
client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
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
    quality_overview,
    vessel_operational,
    voyage_detail,
)
from app.templating import brand_for_lang, templates

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


# ═══════════════════════════════════════════════════════════════════════════
# LOT 12 — géométrie SVG server-rendered (pas de lib JS de graphe)
# ═══════════════════════════════════════════════════════════════════════════
# Même patron que ``_trend_bars`` (LOT 11) : les coordonnées sont calculées ici
# (Python), le template n'émet que des <rect>/<line>/<circle>/<polyline>.

_ROB_W = 720
_ROB_H = 260
_ROB_TOP = 16
_ROB_BOTTOM = 42
_ROB_LEFT = 46
_ROB_RIGHT = 14
_PROP_W = 640
_PROP_H = 30


def _epoch(dt: datetime | None) -> float | None:
    """Datetime → epoch (s). Naïf supposé UTC (backends sans tz — SQLite tests)."""
    if dt is None:
        return None
    d = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return d.timestamp()


def _rob_timeline(rob_chain, bunkers) -> dict:
    """Géométrie de la timeline ROB (SVG server-rendered).

    Points = ROB chaîné calculé (avec marquage des points de RÉFÉRENCE
    Departure/Arrival, seule source ROB déclarée R14-v2) ; marqueurs de soutage
    superposés à leur date de livraison. x = temps, y = ROB (t)."""
    pts = []
    for p in rob_chain:
        ts = _epoch(p.datetime_utc)
        val = p.rob_calculated_t
        if ts is None or val is None:
            continue
        pts.append(
            {
                "ts": ts,
                "val": float(val),
                "type": p.event_type,
                "is_ref": p.event_type in ("departure", "arrival"),
                "declared": (float(p.rob_declared_t) if p.rob_declared_t is not None else None),
                "dt": p.datetime_utc,
            }
        )
    if not pts:
        return {"has_data": False}

    bunker_pts = [(_epoch(b.delivery_datetime_utc), b) for b in bunkers]
    bunker_pts = [(ts, b) for ts, b in bunker_pts if ts is not None]

    all_ts = [p["ts"] for p in pts] + [ts for ts, _ in bunker_pts]
    all_vals = [p["val"] for p in pts] + [p["declared"] for p in pts if p["declared"] is not None]
    tmin, tmax = min(all_ts), max(all_ts)
    vmax = max(all_vals) if all_vals else 1.0
    vmax = vmax * 1.12 if vmax > 0 else 1.0
    span = (tmax - tmin) or 1.0
    plot_w = _ROB_W - _ROB_LEFT - _ROB_RIGHT
    plot_h = _ROB_H - _ROB_TOP - _ROB_BOTTOM

    def _x(ts: float) -> float:
        return _ROB_LEFT + (ts - tmin) / span * plot_w

    def _y(v: float) -> float:
        return _ROB_TOP + (1 - v / vmax) * plot_h

    points = []
    for p in pts:
        points.append(
            {
                "x": round(_x(p["ts"]), 1),
                "y": round(_y(p["val"]), 1),
                "val": round(p["val"], 2),
                "type": p["type"],
                "is_ref": p["is_ref"],
                "dt": p["dt"],
            }
        )
    polyline = " ".join(f"{pt['x']},{pt['y']}" for pt in points)

    baseline_y = _ROB_TOP + plot_h
    bunker_markers = []
    for ts, b in bunker_pts:
        bunker_markers.append(
            {
                "x": round(_x(ts), 1),
                "baseline_y": baseline_y,
                "bdn": b.bdn_number,
                "mass": float(b.mass_t),
                "port": b.port_locode,
            }
        )

    # Repères d'axe Y (0, ½, max).
    y_ticks = [
        {"y": round(_y(0.0), 1), "label": "0"},
        {"y": round(_y(vmax / 2), 1), "label": f"{vmax / 2:.0f}"},
        {"y": round(_y(vmax * 0.99), 1), "label": f"{vmax:.0f}"},
    ]
    return {
        "has_data": True,
        "width": _ROB_W,
        "height": _ROB_H,
        "left": _ROB_LEFT,
        "right_x": _ROB_W - _ROB_RIGHT,
        "baseline_y": baseline_y,
        "points": points,
        "polyline": polyline,
        "bunker_markers": bunker_markers,
        "y_ticks": y_ticks,
        "x_start": pts[0]["dt"],
        "x_end": pts[-1]["dt"],
    }


def _propulsion_bar(profile) -> dict:
    """Barre horizontale empilée (SVG) du profil de propulsion (4 catégories)."""
    filled = profile.filled_slots
    if not filled:
        return {"has_data": False, "segments": [], "width": _PROP_W, "height": _PROP_H}
    segments = []
    x = 0.0
    for s in profile.segments:
        w = (s.count / filled) * _PROP_W
        segments.append(
            {
                "x": round(x, 1),
                "width": round(w, 1),
                "color": s.color,
                "category": s.category,
                "label_key": s.label_key,
                "count": s.count,
                "pct": (float(s.pct) if s.pct is not None else 0.0),
            }
        )
        x += w
    return {"has_data": True, "segments": segments, "width": _PROP_W, "height": _PROP_H}


def _quality_trend_bars(trend, trend_max: int) -> tuple[list[dict], dict]:
    """Barres de tendance des anomalies (12 mois) — patron ``_trend_bars``."""
    n = len(trend)
    plot_h = _CHART_HEIGHT - _CHART_TOP - _CHART_BOTTOM
    plot_w = _CHART_WIDTH - 2 * _CHART_SIDE
    bar_w = ((plot_w - _CHART_GAP * (n - 1)) / n) if n else 0.0
    max_f = float(trend_max) if trend_max else 1.0
    bars = []
    for i, point in enumerate(trend):
        bar_h = (point.count / max_f) * plot_h if max_f > 0 else 0.0
        x = _CHART_SIDE + i * (bar_w + _CHART_GAP)
        y = _CHART_TOP + (plot_h - bar_h)
        bars.append(
            {
                "x": round(x, 1),
                "y": round(y, 1),
                "width": round(max(bar_w, 0.0), 1),
                "height": round(max(bar_h, 0.0), 1),
                "label": point.label,
                "value": point.count,
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


async def _active_vessels(db: AsyncSession) -> list[Vessel]:
    return list(
        (await db.execute(select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.name)))
        .scalars()
        .all()
    )


# ═══════════════════════════════════════ Page 2 — Suivi opérationnel (kpi:C)


@router.get("/vessels/{vessel_id}", response_class=HTMLResponse)
async def dashboard_env_vessel(
    vessel_id: int,
    request: Request,
    year: int | None = None,
    method: str = "A",
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("kpi", "C")),
) -> HTMLResponse:
    """Page 2 — suivi opérationnel d'un navire : liste des voyages + KPI par voyage.

    Sélection de période (année) et de méthode EF (A/B/C, jamais mélangées),
    propagées en query string ; un changement de filtre est une requête HTMX
    qui ré-émet le fragment (``_vessel_fragment.html``)."""
    if method not in EF_METHODS:
        method = "A"
    now = datetime.now(UTC)
    period = year or now.year

    op = await vessel_operational(db, vessel_id, period=period, method=method)
    if op is None:
        raise HTTPException(status_code=404, detail="navire inconnu")

    vessels = await _active_vessels(db)
    years = sorted(set(op.years) | {period, now.year}, reverse=True)
    # Le drill-down vers le détail d'un voyage exige mrv:C — lien conditionnel.
    can_voyage_detail = await has_permission_effective(db, user.role, "mrv", "C")

    ctx = {
        "request": request,
        "user": user,
        "op": op,
        "vessels": vessels,
        "vessel_id": vessel_id,
        "method": method,
        "ef_methods": EF_METHODS,
        "year": period,
        "years": years,
        "can_voyage_detail": can_voyage_detail,
    }
    template_name = (
        "staff/dashboard_env/_vessel_fragment.html"
        if request.headers.get("hx-request")
        else "staff/dashboard_env/vessel.html"
    )
    return templates.TemplateResponse(template_name, ctx)


@router.get("/voyages/{leg_id}", response_class=HTMLResponse)
async def dashboard_env_voyage(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """Page 2 (drill-down) — détail d'un voyage : chaîne d'événements, ROB
    timeline (SVG), conso vs cible, ME/AE, écarts R14/R22, profil de propulsion,
    carte MapLibre colorée par catégorie de propulsion."""
    detail = await voyage_detail(db, leg_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="voyage inconnu")

    rob = _rob_timeline(detail.rob_chain, detail.bunkers)
    prop_bar = _propulsion_bar(detail.propulsion)
    can_act = await has_permission_effective(db, user.role, "mrv", "M")

    return templates.TemplateResponse(
        "staff/dashboard_env/voyage.html",
        {
            "request": request,
            "user": user,
            "d": detail,
            "rob": rob,
            "prop_bar": prop_bar,
            "maptiler_token": settings.maptiler_token,
            "map_points": detail.map_points,
            "map_segments": detail.map_segments,
            "propulsion_colors": {s.category: s.color for s in detail.propulsion.segments},
            "can_act": can_act,
        },
    )


# ═══════════════════════════════════════ Page 3 — Qualité des données (mrv:C)


@router.get("/quality", response_class=HTMLResponse)
async def dashboard_env_quality(
    request: Request,
    vessel_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """Page 3 — tour de contrôle qualité : anomalies par sévérité/règle,
    resets compteur en attente (action ``/mrv/qualite/.../confirm-reset`` du
    LOT 8), soutages non recoupés FLGO, complétude noon, tendance.

    Aucune route d'action ici : les formulaires POST pointent vers ``mrv_router``
    (LOT 8) et les deep-links vers ``/mrv/qualite`` filtré."""
    overview = await quality_overview(db, vessel_id=vessel_id)
    vessels = await _active_vessels(db)
    can_act = await has_permission_effective(db, user.role, "mrv", "M")
    trend_bars, trend_chart = _quality_trend_bars(overview.trend, overview.trend_max)

    return templates.TemplateResponse(
        "staff/dashboard_env/quality.html",
        {
            "request": request,
            "user": user,
            "o": overview,
            "vessels": vessels,
            "filter_vessel_id": vessel_id,
            "can_act": can_act,
            "trend_bars": trend_bars,
            "trend_chart": trend_chart,
        },
    )


# ═══════════════════════════════════════ Exports voyage — PDF / DOCX (mrv:C)


@router.get("/voyages/{leg_id}/export.pdf")
async def dashboard_env_voyage_pdf(
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
):
    """Synthèse voyage PDF (WeasyPrint) : KPI, ROB timeline simplifiée, profil
    de propulsion, anomalies. Rendu depuis les mêmes données que l'écran."""
    from weasyprint import HTML

    detail = await voyage_detail(db, leg_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="voyage inconnu")
    rob = _rob_timeline(detail.rob_chain, detail.bunkers)
    prop_bar = _propulsion_bar(detail.propulsion)

    html = templates.get_template("pdf/dashboard_voyage.html").render(
        d=detail,
        rob=rob,
        prop_bar=prop_bar,
        brand=brand_for_lang("fr"),
        site_url=settings.site_url,
        issued_at=datetime.now(UTC),
        lang="fr",
        t=templates.env.globals["t"],
    )
    pdf = HTML(string=html, base_url=settings.site_url).write_pdf()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="dashboard_voyage_{detail.leg_code}.pdf"'
        },
    )


@router.get("/voyages/{leg_id}/export.docx")
async def dashboard_env_voyage_docx(
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
):
    """Synthèse voyage DOCX (python-docx) — mêmes sections que le PDF."""
    from app.services.docx_generator import build_dashboard_voyage_docx

    detail = await voyage_detail(db, leg_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="voyage inconnu")
    out = build_dashboard_voyage_docx(detail=detail)
    return Response(
        content=out.docx,
        media_type=out.mime,
        headers={"Content-Disposition": f'attachment; filename="{out.filename}"'},
    )
