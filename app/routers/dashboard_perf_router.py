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

Expose :
    GET  /dashboard-perf, /dashboard-perf/              page 1 — Vue flotte (perm kpi:C)
    GET  /dashboard-perf/vessels/{vessel_id}            page 2 — Suivi opérationnel (perm kpi:C)
    GET  /dashboard-perf/voyages/{leg_id}               page 3 — Détail voyage (perm mrv:C)
    GET  /dashboard-perf/voyages/{leg_id}/export.pdf     synthèse voyage PDF (perm mrv:C)
    GET  /dashboard-perf/voyages/{leg_id}/export.docx    synthèse voyage DOCX (perm mrv:C)
    GET  /dashboard-perf/quality                        page 4 — Qualité des données (perm mrv:C)
    GET  /dashboard-perf/parameters                      page 5 — Administration (perm mrv:S)
    POST /dashboard-perf/parameters/{id}/update          édition d'un DashboardParameter (perm mrv:S)

``voyage_detail`` (contrairement à ``fleet_summary``/``vessel_operational``)
n'a pas de paramètre ``strict`` : il porte un seul voyage, pas un agrégat à
filtrer. Le détail expose directement ``source`` (``events``/``legacy_noon``)
— quand il vaut autre chose que ``events``, la page affiche un bandeau
explicite plutôt que de présenter une donnée de repli comme si elle était
événementielle (NC-04, cf. docstring de ``voyage_detail``).

``quality_overview`` n'a pas non plus de paramètre ``strict`` : il n'en a
jamais eu besoin — il ne lit que des tables exclusivement event-sourcées
(``QualityCheckResult``, ``NavEvent*``, ``BunkerOperation``), jamais
``NoonReport``/``LegKPI``. La page 4 est donc, par construction, déjà
alignée sur NC-04 sans aucun traitement particulier.

Aucune route d'action ici (page 4) : les formulaires POST pointent vers
``mrv_router`` (LOT 8, ``/mrv/qualite/...``) et les deep-links vers
``/mrv/qualite`` filtré — même principe que l'ancien dashboard.

Page 5 (Administration, ``mrv:S``) : édition des ``dashboard_parameters``
uniquement (les seuils du moteur de règles restent administrés par l'écran
LOT 2 existant, ``/mrv/parametres``, et les facteurs carburant par
``/admin/emission-factors`` — non dupliqués ici, seulement liés en croisé).
Aucun paramètre ``strict`` : ce n'est pas un agrégat KPI, NC-04 ne s'y
applique pas.

Exports voyage (PDF/DOCX) : portage direct de ``dashboard_env_router`` —
mêmes gabarits (``pdf/dashboard_voyage.html``, ``services.docx_generator``),
génériques et indépendants de l'ancien routeur. Avec cette brique, la
couverture fonctionnelle de ``dashboard_perf_router`` est désormais
équivalente à celle de ``dashboard_env_router`` (action 6/NC-05 peut être
engagée).

Calcul serveur exclusivement (même posture que ``dashboard_env_router``) :
ce routeur résout les paramètres HTTP (période/méthode/navire) + la
géométrie SVG de la tendance (server-rendered, pas de lib CDN) et appelle
``services.kpi_env`` — aucune formule n'est recalculée ici ni côté client.
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

router = APIRouter(prefix="/dashboard-perf", tags=["dashboard-perf"])

# Borne défensive identique à dashboard_env_router (dashboard_parameters.value
# est un Numeric(15, 6)).
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


# Géométrie ROB/propulsion — même patron que ``dashboard_env_router``,
# dupliquée volontairement plutôt qu'importée depuis un module voué au
# décommissionnement (NC-05).
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
    Departure/Arrival, seule source ROB déclarée R14-v2) ; marqueurs de
    soutage superposés à leur date de livraison. x = temps, y = ROB (t)."""
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
    """Barres de tendance des anomalies (12 mois) — même patron que ``_trend_bars``."""
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

    vessels = await _active_vessels(db)
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


# ═══════════════════════════════════════ Page 2 — Suivi opérationnel (kpi:C)


@router.get("/vessels/{vessel_id}", response_class=HTMLResponse)
async def dashboard_perf_vessel(
    vessel_id: int,
    request: Request,
    year: int | None = None,
    method: str = "A",
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("kpi", "C")),
) -> HTMLResponse:
    """Page 2 — suivi opérationnel d'un navire, exclusivement event-driven
    (NC-04 : ``strict=True``).

    Les totaux agrégés (conso/CO2/distance/décompte) n'incluent que les
    voyages ``source="events"`` ; la liste ``op.voyages`` reste complète —
    chaque ligne porte son propre ``source``, affiché explicitement (jamais
    un voyage legacy présenté comme une donnée événementielle normale).
    """
    if method not in EF_METHODS:
        method = "A"
    now = datetime.now(UTC)
    period = year or now.year

    op = await vessel_operational(db, vessel_id, period=period, method=method, strict=True)
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
        "staff/dashboard_perf/_vessel_fragment.html"
        if request.headers.get("hx-request")
        else "staff/dashboard_perf/vessel.html"
    )
    return templates.TemplateResponse(template_name, ctx)


# ═══════════════════════════════════════ Page 3 — Détail voyage (mrv:C)


@router.get("/voyages/{leg_id}", response_class=HTMLResponse)
async def dashboard_perf_voyage(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """Page 3 — détail d'un voyage : chaîne d'événements, ROB timeline (SVG),
    conso vs cible, ME/AE, écarts R14/R22, profil de propulsion, carte
    MapLibre colorée par catégorie de propulsion.

    NC-04 : ``voyage_detail`` n'a pas de mode strict (rien à agréger/filtrer
    pour un seul voyage) — ``is_event_sourced`` (dérivé de ``d.source``) est
    calculé ici pour que le template affiche un bandeau explicite quand ce
    voyage provient du repli legacy, plutôt que de présenter ses chiffres
    comme une donnée événementielle normale.
    """
    detail = await voyage_detail(db, leg_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="voyage inconnu")

    rob = _rob_timeline(detail.rob_chain, detail.bunkers)
    prop_bar = _propulsion_bar(detail.propulsion)

    return templates.TemplateResponse(
        "staff/dashboard_perf/voyage.html",
        {
            "request": request,
            "user": user,
            "d": detail,
            "rob": rob,
            "prop_bar": prop_bar,
            "is_event_sourced": detail.source == "events",
            "maptiler_token": settings.maptiler_token,
            "map_points": detail.map_points,
            "map_segments": detail.map_segments,
        },
    )


# ═══════════════════════════════════════ Exports voyage — PDF / DOCX (mrv:C)
# Portage direct de dashboard_env_router (mêmes gabarits ``pdf/dashboard_voyage.html``
# et ``services.docx_generator``, génériques — ils ne dépendent d'aucune route de
# l'ancien routeur) ; dernière brique avant le décommissionnement (action 6, NC-05).


@router.get("/voyages/{leg_id}/export.pdf")
async def dashboard_perf_voyage_pdf(
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
async def dashboard_perf_voyage_docx(
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


# ═══════════════════════════════════════ Page 4 — Qualité des données (mrv:C)


@router.get("/quality", response_class=HTMLResponse)
async def dashboard_perf_quality(
    request: Request,
    vessel_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """Page 4 — tour de contrôle qualité : anomalies par sévérité/règle,
    resets compteur en attente (action ``/mrv/qualite/.../confirm-reset`` du
    LOT 8), soutages non recoupés FLGO, complétude noon, tendance.

    Aucune route d'action ici : les formulaires POST pointent vers
    ``mrv_router`` (LOT 8) et les deep-links vers ``/mrv/qualite`` filtré.
    Pas de paramètre ``strict`` : ``quality_overview`` ne lit que des tables
    exclusivement event-sourcées (cf. docstring du module) — déjà aligné
    sur NC-04 par construction.
    """
    overview = await quality_overview(db, vessel_id=vessel_id)
    vessels = await _active_vessels(db)
    can_act = await has_permission_effective(db, user.role, "mrv", "M")
    trend_bars, trend_chart = _quality_trend_bars(overview.trend, overview.trend_max)

    can_admin_params = await has_permission_effective(db, user.role, "mrv", "S")

    return templates.TemplateResponse(
        "staff/dashboard_perf/quality.html",
        {
            "request": request,
            "user": user,
            "o": overview,
            "vessels": vessels,
            "filter_vessel_id": vessel_id,
            "can_act": can_act,
            "can_admin_params": can_admin_params,
            "trend_bars": trend_bars,
            "trend_chart": trend_chart,
        },
    )


# ═══════════════════════════════════════════════ Page 5 — Administration (mrv:S)


async def _history_for_params(
    db: AsyncSession, param_ids: list[int]
) -> dict[int, list[ActivityLog]]:
    """Historique des modifications par paramètre (journal ``activity_logs``).

    Même source que ``dashboard_env_router`` (pas de table dédiée) : l'audit
    trail append-only existant (``services.activity.record``) sert de source
    d'historique, sans nouveau schéma.
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
async def dashboard_perf_parameters(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "S")),
) -> HTMLResponse:
    """Page 5 — Administration : édition des ``dashboard_parameters``.

    Périmètre volontairement restreint à cette seule table (les seuils du
    moteur de règles ``ValidationRuleThreshold`` sont administrés par l'écran
    LOT 2 existant, ``/mrv/parametres`` — non dupliqué ici, seulement lié en
    croisé, de même que ``/admin/emission-factors`` pour les facteurs
    carburant, LOT 1). Pas de mode ``strict`` : ce n'est pas un agrégat KPI.
    """
    params = list(
        (await db.execute(select(DashboardParameter).order_by(DashboardParameter.parameter_name)))
        .scalars()
        .all()
    )
    history_by_param = await _history_for_params(db, [p.id for p in params])
    vessels = await _active_vessels(db)
    vessel_by_id = {v.id: v for v in vessels}

    return templates.TemplateResponse(
        "staff/dashboard_perf/parameters.html",
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
async def dashboard_perf_parameters_update(
    param_id: int,
    request: Request,
    value: str = Form(...),
    unit: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "S")),
):
    """Édite la valeur (et l'unité) d'un ``DashboardParameter`` — tracé en activity_log.

    Le journal réutilise le même ``module="dashboard_env"`` que l'ancien
    routeur : c'est le même ``DashboardParameter``, la même table
    ``activity_logs`` — pas une nouvelle catégorie d'événement, juste un
    second point d'entrée pour l'éditer.
    """
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

    target = "/dashboard-perf/parameters"
    if request.headers.get("hx-request"):
        return Response(status_code=200, headers={"HX-Redirect": target})
    return RedirectResponse(url=target, status_code=303)
