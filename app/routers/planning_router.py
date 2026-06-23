"""Planning module — Gantt view, leg CRUD, public share by token.

Auth: staff with `planning` permission (C/M/S per matrix).
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.geo import leg_trade_category
from app.services.planning import (
    InvalidLegDates,
    PlanningError,
    closed_weekdays_for_port,
    create_leg,
    create_share,
    delete_leg,
    detect_port_conflicts,
    detect_port_conflicts_view,
    list_legs_in_window,
    list_shares,
    lookup_share,
    next_working_departure,
    revoke_share,
    update_leg,
)
from app.templating import templates

router = APIRouter(prefix="/planning", tags=["planning"])

GANTT_WINDOW_DAYS = 90


# ---------------------------------------------------------------------------
# Gantt index (staff)
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_class=HTMLResponse,
)
async def gantt_index(
    request: Request,
    vessel_id: int | None = None,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> HTMLResponse:
    now = datetime.now(UTC)
    # Vue ANNÉE ENTIÈRE (req #5) — sélecteur d'année + filtre navire en tête.
    selected_year = year or now.year
    window_start = datetime(selected_year, 1, 1, tzinfo=UTC)
    window_end = datetime(selected_year, 12, 31, 23, 59, tzinfo=UTC)

    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    legs = await list_legs_in_window(
        db,
        date_from=window_start,
        date_to=window_end,
        vessel_id=vessel_id,
    )

    # Années disponibles (min/max ETD en base + année courante + sélectionnée).
    yr_row = (await db.execute(select(func.min(Leg.etd), func.max(Leg.etd)))).first()
    years: set[int] = {now.year, selected_year}
    if yr_row and yr_row[0] and yr_row[1]:
        years |= set(range(yr_row[0].year, yr_row[1].year + 1))
    years_sorted = sorted(years)

    # Pre-load port labels (avoid N+1)
    port_ids = {leg.departure_port_id for leg in legs} | {leg.arrival_port_id for leg in legs}
    ports = (
        {
            p.id: p
            for p in (await db.execute(select(Port).where(Port.id.in_(port_ids)))).scalars().all()
        }
        if port_ids
        else {}
    )

    conflicts = detect_port_conflicts(legs)
    conflict_ids: set[int] = set()
    for a, b in conflicts:
        conflict_ids.add(a)
        conflict_ids.add(b)

    # Build Gantt rows (one per vessel) with positioned bars
    gantt_rows = _build_gantt_rows(
        vessels=vessels,
        legs=legs,
        window_start=window_start,
        window_end=window_end,
        ports=ports,
        conflict_ids=conflict_ids,
    )

    # Repères mensuels pour l'axe du Gantt (12 mois de l'année).
    total_s = (window_end - window_start).total_seconds()
    month_marks = []
    for m in range(1, 13):
        ms = datetime(selected_year, m, 1, tzinfo=UTC)
        left = ((ms - window_start).total_seconds() / total_s) * 100
        month_marks.append({"label": ms.strftime("%b"), "left_pct": round(left, 3)})

    # Position du repère "aujourd'hui" (None si l'année affichée ≠ courante).
    today_pct = None
    if window_start <= now <= window_end:
        today_pct = round(((now - window_start).total_seconds() / total_s) * 100, 3)

    return templates.TemplateResponse(
        "staff/planning/index.html",
        {
            "request": request,
            "user": user,
            "vessels": vessels,
            "legs": legs,
            "ports": ports,
            "gantt_rows": gantt_rows,
            "filter_vessel_id": vessel_id,
            "selected_year": selected_year,
            "years": years_sorted,
            "month_marks": month_marks,
            "today_pct": today_pct,
            "window_start": window_start,
            "window_end": window_end,
            "conflict_count": len(conflicts),
        },
    )


# ---------------------------------------------------------------------------
# Conflits de port (deux navires au même port en même temps)
# ---------------------------------------------------------------------------


@router.get("/conflicts", response_class=HTMLResponse)
async def port_conflicts(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> HTMLResponse:
    """Liste les chevauchements d'escale : deux navires distincts présents au
    même port sur des périodes ``[ETA, ETA + durée d'escale]`` qui se
    recouvrent, sur la fenêtre des 90 prochains jours.
    """
    conflicts = await detect_port_conflicts_view(db, window_days=GANTT_WINDOW_DAYS)
    return templates.TemplateResponse(
        "staff/planning/port_conflicts.html",
        {
            "request": request,
            "user": user,
            "conflicts": conflicts,
            "window_days": GANTT_WINDOW_DAYS,
        },
    )


# ---------------------------------------------------------------------------
# Leg create / edit
# ---------------------------------------------------------------------------


async def _new_leg_suggestions(db: AsyncSession) -> dict[int, dict]:
    """Pré-calcule pour chaque navire la suggestion ETD/POL du prochain leg.

    Règle : ETD suggéré = (ATA si déjà arrivé, sinon ETA) du dernier leg
    de ce navire + ``port_stay_planned_hours`` du dernier leg (default 48h
    si non renseigné). POL suggéré = POD du dernier leg (continuité
    géographique). Si le navire n'a aucun leg, pas de suggestion.

    Le dict est sérialisé en data-attribute du form et appliqué côté JS
    quand l'utilisateur sélectionne un navire (cf. leg-form-suggest.js).
    """
    from sqlalchemy import desc

    suggestions: dict[int, dict] = {}
    vessels_q = await db.execute(select(Vessel))
    for v in vessels_q.scalars().all():
        last = (
            await db.execute(
                select(Leg).where(Leg.vessel_id == v.id).order_by(desc(Leg.etd)).limit(1)
            )
        ).scalar_one_or_none()
        if last is None:
            continue
        base = last.ata or last.eta
        if base is None:
            continue
        stay = last.port_stay_planned_hours or 48
        # Décale le départ si le port d'arrivée est fermé au commerce le WE :
        # l'escale glisse vers le(s) jour(s) ouvré(s) suivant(s).
        closed = await closed_weekdays_for_port(db, last.arrival_port_id)
        suggested = next_working_departure(base, stay, closed)
        suggestions[v.id] = {
            "etd": suggested.strftime("%Y-%m-%dT%H:%M"),
            "pol_id": last.arrival_port_id,
            "port_stay_hours": stay,
            "from_leg_code": last.leg_code,
            "from_eta": (last.eta.strftime("%Y-%m-%dT%H:%M") if last.eta else None),
            "from_ata": (last.ata.strftime("%Y-%m-%dT%H:%M") if last.ata else None),
        }
    return suggestions


@router.get(
    "/legs/new",
    response_class=HTMLResponse,
)
async def new_leg_form(
    request: Request,
    vessel_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    ports = list((await db.execute(select(Port).order_by(Port.locode))).scalars().all())
    suggestions = await _new_leg_suggestions(db)
    return templates.TemplateResponse(
        "staff/planning/leg_form.html",
        {
            "request": request,
            "user": user,
            "leg": None,
            "vessels": vessels,
            "ports": ports,
            "error": None,
            "suggestions": suggestions,
            "preselected_vessel_id": vessel_id,
        },
    )


@router.get("/legs/new-from-map", response_class=HTMLResponse)
async def new_leg_from_map(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    """Interactive map: click a port marker → snap → prefill form."""
    from app.config import settings

    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    return templates.TemplateResponse(
        "staff/planning/leg_from_map.html",
        {
            "request": request,
            "user": user,
            "vessels": vessels,
            "maptiler_token": settings.map_token,
        },
    )


@router.post("/legs/new", response_class=HTMLResponse)
async def create_leg_action(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    form = await request.form()
    try:
        leg = await create_leg(
            db,
            vessel_id=int(form["vessel_id"]),
            departure_port_id=int(form["departure_port_id"]),
            arrival_port_id=int(form["arrival_port_id"]),
            etd=_parse_dt(form.get("etd")),
            eta=_parse_dt(form.get("eta")),
            is_bookable=form.get("is_bookable") == "on",
            public_capacity_palettes=_maybe_int(form.get("public_capacity_palettes")),
            # public_price_per_palette_eur géré par /commercial (grilles tarifaires)
            booking_close_at=_parse_dt(form.get("booking_close_at"), allow_empty=True),
            transit_speed_kn=_maybe_float(form.get("transit_speed_kn")),
            elongation_coef=_maybe_float(form.get("elongation_coef")),
            port_stay_planned_hours=_maybe_int(form.get("port_stay_planned_hours")),
        )
    except (InvalidLegDates, PlanningError, KeyError, ValueError) as e:
        vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
        ports = list((await db.execute(select(Port).order_by(Port.locode))).scalars().all())
        return templates.TemplateResponse(
            "staff/planning/leg_form.html",
            {
                "request": request,
                "user": user,
                "leg": None,
                "vessels": vessels,
                "ports": ports,
                "error": f"Création impossible : {e}",
                "form": dict(form),
            },
            status_code=400,
        )

    await activity_record(
        db,
        action="leg_create",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="planning",
        entity_type="leg",
        entity_id=leg.id,
        entity_label=leg.leg_code,
    )
    return RedirectResponse(url=f"/planning/legs/{leg.id}", status_code=303)


@router.get(
    "/legs/{leg_id}",
    response_class=HTMLResponse,
)
async def leg_detail(
    request: Request,
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> HTMLResponse:
    leg = await _get_leg_or_404(db, leg_id)
    vessel = await db.get(Vessel, leg.vessel_id)
    pol = await db.get(Port, leg.departure_port_id)
    pod = await db.get(Port, leg.arrival_port_id)

    return templates.TemplateResponse(
        "staff/planning/leg_detail.html",
        {
            "request": request,
            "user": user,
            "leg": leg,
            "vessel": vessel,
            "pol": pol,
            "pod": pod,
        },
    )


@router.get(
    "/legs/{leg_id}/edit",
    response_class=HTMLResponse,
)
async def edit_leg_form(
    request: Request,
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    leg = await _get_leg_or_404(db, leg_id)
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    ports = list((await db.execute(select(Port).order_by(Port.locode))).scalars().all())
    return templates.TemplateResponse(
        "staff/planning/leg_form.html",
        {
            "request": request,
            "user": user,
            "leg": leg,
            "vessels": vessels,
            "ports": ports,
            "error": None,
        },
    )


@router.post("/legs/{leg_id}/edit", response_class=HTMLResponse)
async def update_leg_action(
    request: Request,
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    leg = await _get_leg_or_404(db, leg_id)
    form = await request.form()
    cascade = form.get("cascade") == "on"
    try:
        report = await update_leg(
            db,
            leg,
            vessel_id=_maybe_int(form.get("vessel_id")),
            etd=_parse_dt(form.get("etd"), allow_empty=True),
            eta=_parse_dt(form.get("eta"), allow_empty=True),
            departure_port_id=_maybe_int(form.get("departure_port_id")),
            arrival_port_id=_maybe_int(form.get("arrival_port_id")),
            is_bookable=(form.get("is_bookable") == "on"),
            public_capacity_palettes=_maybe_int(form.get("public_capacity_palettes")),
            # public_price_per_palette_eur géré par /commercial (grilles tarifaires)
            booking_close_at=_parse_dt(form.get("booking_close_at"), allow_empty=True),
            transit_speed_kn=_maybe_float(form.get("transit_speed_kn")),
            elongation_coef=_maybe_float(form.get("elongation_coef")),
            port_stay_planned_hours=_maybe_int(form.get("port_stay_planned_hours")),
            cascade=cascade,
        )
    except (InvalidLegDates, PlanningError) as e:
        vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
        ports = list((await db.execute(select(Port).order_by(Port.locode))).scalars().all())
        return templates.TemplateResponse(
            "staff/planning/leg_form.html",
            {
                "request": request,
                "user": user,
                "leg": leg,
                "vessels": vessels,
                "ports": ports,
                "error": str(e),
            },
            status_code=400,
        )
    await activity_record(
        db,
        action="leg_update",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="planning",
        entity_type="leg",
        entity_id=leg.id,
        entity_label=leg.leg_code,
        detail=(
            f"cascade delta={report.delta_hours:.1f}h " f"impacted={len(report.impacted_leg_ids)}"
            if report
            else None
        ),
    )
    return RedirectResponse(url=f"/planning/legs/{leg.id}", status_code=303)


@router.post("/legs/{leg_id}/delete")
async def delete_leg_action(
    request: Request,
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "S")),
):
    leg = await _get_leg_or_404(db, leg_id)
    try:
        await delete_leg(db, leg)
    except PlanningError as e:
        # Au lieu d'un 400 sec, on re-rend leg_detail avec un bandeau
        # d'erreur listant les dépendances bloquantes (UX > Exception).
        vessel = await db.get(Vessel, leg.vessel_id)
        pol = await db.get(Port, leg.departure_port_id)
        pod = await db.get(Port, leg.arrival_port_id)
        return templates.TemplateResponse(
            "staff/planning/leg_detail.html",
            {
                "request": request,
                "user": user,
                "leg": leg,
                "vessel": vessel,
                "pol": pol,
                "pod": pod,
                "delete_error": str(e),
            },
            status_code=400,
        )
    await activity_record(
        db,
        action="leg_delete",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="planning",
        entity_type="leg",
        entity_id=leg.id,
        entity_label=leg.leg_code,
    )
    return RedirectResponse(url="/planning", status_code=303)


# ---------------------------------------------------------------------------
# Public share management (staff)
# ---------------------------------------------------------------------------


async def _planning_rows(db: AsyncSession, *, vessel_id: int | None, year: int):
    """Legs de l'année (+ filtre navire), avec navire & ports résolus, triés ETD."""
    window_start = datetime(year, 1, 1, tzinfo=UTC)
    window_end = datetime(year, 12, 31, 23, 59, tzinfo=UTC)
    legs = await list_legs_in_window(
        db, date_from=window_start, date_to=window_end, vessel_id=vessel_id
    )
    legs = sorted(legs, key=lambda lg: lg.etd or window_start)
    vmap = {v.id: v for v in (await db.execute(select(Vessel))).scalars().all()}
    port_ids = {lg.departure_port_id for lg in legs} | {lg.arrival_port_id for lg in legs}
    pmap = (
        {
            p.id: p
            for p in (await db.execute(select(Port).where(Port.id.in_(port_ids)))).scalars().all()
        }
        if port_ids
        else {}
    )
    rows = [
        {
            "leg": lg,
            "vessel": vmap.get(lg.vessel_id),
            "pol": pmap.get(lg.departure_port_id),
            "pod": pmap.get(lg.arrival_port_id),
        }
        for lg in legs
    ]
    return rows, vmap, pmap


@router.get("/pdf/commercial")
async def planning_commercial_pdf(
    request: Request,
    vessel_id: int | None = None,
    year: int | None = None,
    lang: str = "fr",
    group_by: str = "chrono",
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
):
    """PLN-01 — brochure commerciale imprimable (PDF), filtrée navire/année,
    vue chronologique ou groupée par destination, FR/EN.
    """
    from app.services.pdf_generator import render_planning_brochure

    year = year or datetime.now(UTC).year
    lang = lang if lang in ("fr", "en") else "fr"
    rows, _vmap, _pmap = await _planning_rows(db, vessel_id=vessel_id, year=year)

    if group_by == "destination":
        buckets: dict[str, list] = {}
        for r in rows:
            key = r["pod"].name if r["pod"] else "—"
            buckets.setdefault(key, []).append(r)
        groups = [{"title": k, "rows": v} for k, v in sorted(buckets.items())]
    else:
        title = "Chronological schedule" if lang == "en" else "Calendrier chronologique"
        groups = [{"title": title, "rows": rows}]

    port_count = len(
        {r["pol"].id for r in rows if r["pol"]} | {r["pod"].id for r in rows if r["pod"]}
    )
    summary = {
        "leg_count": len(rows),
        "vessel_count": len({r["vessel"].id for r in rows if r["vessel"]}),
        "port_count": port_count,
    }
    vessel = await db.get(Vessel, vessel_id) if vessel_id else None
    meta = {"year": year, "vessel_name": vessel.name if vessel else None, "group_by": group_by}

    doc = render_planning_brochure(groups=groups, summary=summary, meta=meta, lang=lang)
    return Response(
        content=doc.pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="planning_newtowt.pdf"'},
    )


@router.get("/export/csv")
async def planning_export_csv(
    request: Request,
    vessel_id: int | None = None,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
):
    """PLN-03 — export CSV du planning réel (filtré navire/année)."""
    year = year or datetime.now(UTC).year
    rows, _vmap, _pmap = await _planning_rows(db, vessel_id=vessel_id, year=year)

    from app.utils.csv_safe import sanitize_row

    def _iso(dt):
        return dt.isoformat() if dt else ""

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "leg_code",
            "vessel",
            "pol_locode",
            "pol_name",
            "pod_locode",
            "pod_name",
            "etd",
            "eta",
            "atd",
            "ata",
            "status",
            "distance_nm",
            "is_bookable",
        ]
    )
    for r in rows:
        lg, v, pol, pod = r["leg"], r["vessel"], r["pol"], r["pod"]
        writer.writerow(
            sanitize_row(
                [
                    lg.leg_code,
                    v.name if v else "",
                    pol.locode if pol else "",
                    pol.name if pol else "",
                    pod.locode if pod else "",
                    pod.name if pod else "",
                    _iso(lg.etd),
                    _iso(lg.eta),
                    _iso(lg.atd),
                    _iso(lg.ata),
                    lg.status,
                    lg.distance_nm or "",
                    "1" if lg.is_bookable else "0",
                ]
            )
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="planning_reel.csv"'},
    )


@router.get("/by-port", response_class=HTMLResponse)
async def planning_by_port(
    request: Request,
    vessel_id: int | None = None,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> HTMLResponse:
    """PLN-06 — vue « par port » : départs et arrivées regroupés par port,
    avec signalement de retard (PLN-05, ≥ 4 h vs référence).
    """
    from app.services.planning import is_delayed, leg_delay_hours

    year = year or datetime.now(UTC).year
    rows, _vmap, pmap = await _planning_rows(db, vessel_id=vessel_id, year=year)

    groups: dict[int, dict] = {}

    def _bucket(port_id: int | None) -> dict | None:
        if port_id is None:
            return None
        if port_id not in groups:
            groups[port_id] = {"port": pmap.get(port_id), "departures": [], "arrivals": []}
        return groups[port_id]

    for r in rows:
        lg = r["leg"]
        entry = {
            "leg": lg,
            "vessel": r["vessel"],
            "pol": r["pol"],
            "pod": r["pod"],
            "delayed": is_delayed(lg),
            "delay_h": round(leg_delay_hours(lg), 1),
        }
        dep = _bucket(lg.departure_port_id)
        if dep is not None:
            dep["departures"].append(entry)
        arr = _bucket(lg.arrival_port_id)
        if arr is not None:
            arr["arrivals"].append(entry)

    port_groups = sorted(groups.values(), key=lambda g: g["port"].locode if g["port"] else "~")
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    return templates.TemplateResponse(
        "staff/planning/by_port.html",
        {
            "request": request,
            "user": user,
            "port_groups": port_groups,
            "vessels": vessels,
            "filter_vessel_id": vessel_id,
            "selected_year": year,
        },
    )


@router.get(
    "/shares",
    response_class=HTMLResponse,
)
async def shares_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> HTMLResponse:
    shares = await list_shares(db)
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    # Ports effectivement desservis (POL = ports de départ, POD = ports
    # d'arrivée) pour alimenter les sélecteurs de filtre du formulaire.
    pol_ids = select(Leg.departure_port_id).distinct()
    pod_ids = select(Leg.arrival_port_id).distinct()
    pol_ports = list(
        (await db.execute(select(Port).where(Port.id.in_(pol_ids)).order_by(Port.locode)))
        .scalars()
        .all()
    )
    pod_ports = list(
        (await db.execute(select(Port).where(Port.id.in_(pod_ids)).order_by(Port.locode)))
        .scalars()
        .all()
    )
    # id→port pour ré-afficher les filtres baked dans le tableau des partages.
    filter_ports = {p.id: p for p in pol_ports} | {p.id: p for p in pod_ports}
    return templates.TemplateResponse(
        "staff/planning/shares.html",
        {
            "request": request,
            "user": user,
            "shares": shares,
            "vessels": vessels,
            "pol_ports": pol_ports,
            "pod_ports": pod_ports,
            "filter_ports": filter_ports,
        },
    )


@router.post("/shares")
async def shares_create(
    request: Request,
    label: str = Form(""),
    vessel_id: str = Form(""),
    pol_port_id: str = Form(""),
    pod_port_id: str = Form(""),
    only_bookable: str = Form(""),
    description: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> RedirectResponse:
    await create_share(
        db,
        label=label.strip() or None,
        vessel_id=int(vessel_id) if vessel_id else None,
        pol_port_id=int(pol_port_id) if pol_port_id else None,
        pod_port_id=int(pod_port_id) if pod_port_id else None,
        only_bookable=(only_bookable == "on"),
        description=description.strip() or None,
        date_from=_parse_dt(date_from, allow_empty=True),
        date_to=_parse_dt(date_to, allow_empty=True),
        expires_at=None,
        created_by_id=user.id,
    )
    return RedirectResponse(url="/planning/shares", status_code=303)


@router.post("/shares/{share_id}/revoke")
async def share_revoke(
    share_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> RedirectResponse:
    from app.models.planning_share import PlanningShare

    share = await db.get(PlanningShare, share_id)
    if not share:
        raise HTTPException(status_code=404, detail="Not found")
    await revoke_share(db, share)
    return RedirectResponse(url="/planning/shares", status_code=303)


# ---------------------------------------------------------------------------
# Public planning view (token, no auth)
# ---------------------------------------------------------------------------


@router.get("/share/{token}", response_class=HTMLResponse)
async def public_share(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    share = await lookup_share(db, token)
    if not share:
        return templates.TemplateResponse(
            "public/404.html",
            {"request": request},
            status_code=404,
        )

    # Bump access counter
    share.access_count += 1
    share.last_access_at = datetime.now(UTC)

    now = datetime.now(UTC)
    # Période : si le partage a une plage explicite, on la respecte ;
    # sinon fenêtre par défaut (7 j passés → 90 j à venir). date_to est
    # rendue inclusive jusqu'à la fin de la journée.
    window_from = share.date_from or (now - timedelta(days=7))
    window_to = (
        share.date_to.replace(hour=23, minute=59, second=59)
        if share.date_to
        else now + timedelta(days=GANTT_WINDOW_DAYS)
    )
    legs = await list_legs_in_window(
        db,
        date_from=window_from,
        date_to=window_to,
        vessel_id=share.vessel_id,
    )
    if share.only_bookable:
        legs = [leg for leg in legs if leg.is_bookable]
    # Filtres géographiques optionnels (POL de départ / POD d'arrivée).
    if share.pol_port_id:
        legs = [leg for leg in legs if leg.departure_port_id == share.pol_port_id]
    if share.pod_port_id:
        legs = [leg for leg in legs if leg.arrival_port_id == share.pod_port_id]

    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    vessels_by_id = {v.id: v for v in vessels}
    port_ids = {leg.departure_port_id for leg in legs} | {leg.arrival_port_id for leg in legs}
    ports = (
        {
            p.id: p
            for p in (await db.execute(select(Port).where(Port.id.in_(port_ids)))).scalars().all()
        }
        if port_ids
        else {}
    )

    # Tableau commercial : une ligne par traversée, triée par date de départ.
    table_rows: list[dict] = []
    for leg in sorted(legs, key=lambda li: li.etd):
        vessel = vessels_by_id.get(leg.vessel_id)
        pol = ports.get(leg.departure_port_id)
        pod = ports.get(leg.arrival_port_id)
        transit_hours = (leg.eta - leg.etd).total_seconds() / 3600 if leg.eta and leg.etd else None
        transit_days = round(transit_hours / 24, 1) if transit_hours else None
        table_rows.append(
            {
                "leg_code": leg.leg_code,
                "vessel_code": vessel.code if vessel else "",
                "vessel_name": vessel.name if vessel else "",
                "pol": pol,
                "pod": pod,
                "etd": leg.etd,
                "eta": leg.eta,
                "transit_days": transit_days,
                "status": leg.status,
                "is_bookable": leg.is_bookable,
            }
        )

    return templates.TemplateResponse(
        "public/planning_share.html",
        {
            "request": request,
            "share": share,
            "vessels": vessels,
            "table_rows": table_rows,
            "generated_at": now,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_gantt_rows(
    *,
    vessels: list[Vessel],
    legs: list[Leg],
    window_start: datetime,
    window_end: datetime,
    ports: dict[int, Port],
    conflict_ids: set[int],
) -> list[dict]:
    total_seconds = (window_end - window_start).total_seconds()
    rows: list[dict] = []
    by_vessel: dict[int, list[Leg]] = {}
    for leg in legs:
        by_vessel.setdefault(leg.vessel_id, []).append(leg)

    for vessel in vessels:
        bars: list[dict] = []
        for leg in by_vessel.get(vessel.id, []):
            start = max(leg.etd, window_start)
            end = min(leg.eta, window_end)
            if end <= start:
                continue
            left_pct = ((start - window_start).total_seconds() / total_seconds) * 100
            width_pct = ((end - start).total_seconds() / total_seconds) * 100
            pol = ports.get(leg.departure_port_id)
            pod = ports.get(leg.arrival_port_id)
            bars.append(
                {
                    "leg_id": leg.id,
                    "leg_code": leg.leg_code,
                    "status": leg.status,
                    "category": leg_trade_category(
                        pol.country if pol else None, pod.country if pod else None
                    ),
                    "left_pct": round(left_pct, 3),
                    "width_pct": round(max(width_pct, 1.0), 3),
                    "pol_locode": pol.locode if pol else "",
                    "pod_locode": pod.locode if pod else "",
                    "etd": leg.etd,
                    "eta": leg.eta,
                    "in_conflict": leg.id in conflict_ids,
                    "is_bookable": leg.is_bookable,
                }
            )
        rows.append({"vessel": vessel, "bars": bars})
    return rows


async def _get_leg_or_404(db: AsyncSession, leg_id: int) -> Leg:
    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leg not found")
    return leg


def _parse_dt(value, allow_empty: bool = False) -> datetime:
    if value is None or value == "":
        if allow_empty:
            return None  # type: ignore[return-value]
        raise InvalidLegDates("Date required")
    # HTML <input type="datetime-local"> yields "2026-06-04T08:00"
    s = str(value).replace("T", " ")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise InvalidLegDates(f"Invalid date format: {value}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _maybe_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
