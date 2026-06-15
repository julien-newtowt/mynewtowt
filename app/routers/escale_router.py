"""Escale — operations portuaires + shifts dockers + timelines.

Reprises de la V3.0.0 :
- Liste filtrée par navire + année + leg sélectionné.
- Détail d'un leg : leg-summary + timeline opérations + shifts dockers.
- Création/édition d'opérations (IMPORT/EXPORT/BOTH, type+action, planned/actual).
- Création/édition de shifts dockers (cadence palettes/h).
- Lock de leg (clôture administrative).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.escale import (
    DIRECTIONS,
    ESCALE_ACTION_TO_SOF,
    OPERATION_ACTIONS,
    OPERATION_TYPES,
    DockerShift,
    EscaleOperation,
)
from app.models.leg import Leg
from app.models.port import Port
from app.models.sof_event import SOF_EVENT_TYPES, SofEvent
from app.models.stowage import HOLDS
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.stowage import occupation_by_hold
from app.templating import templates

logger = logging.getLogger("escale")

router = APIRouter(prefix="/escale", tags=["escale"])


def _escale_locked(leg: Leg) -> bool:
    """L'escale du leg est-elle verrouillée (clôture administrative) ?"""
    return leg.escale_locked_at is not None


def _assert_escale_unlocked(leg: Leg) -> None:
    """Garde-fou : refuse toute écriture si l'escale est verrouillée.

    Levée d'une 400 avec message FR explicite — appelée par tous les
    endpoints create/edit/start/end/delete d'opérations et de shifts.
    """
    if _escale_locked(leg):
        raise HTTPException(
            status_code=400,
            detail=(
                "Escale verrouillée — modification impossible. "
                "Déverrouillez l'escale pour la modifier."
            ),
        )


async def _sync_sof_from_operation(
    db: AsyncSession,
    request: Request,
    user,
    op: EscaleOperation,
) -> None:
    """FLX-04 — crée l'événement SOF équivalent à une opération d'escale.

    Idempotent (dédoublonne sur leg_id + event_type + occurred_at) et
    best-effort : toute erreur est journalisée mais ne casse jamais
    l'action escale. ``occurred_at`` = actual_start ou planned_start ou
    maintenant. Ne fait rien si l'``action`` n'a pas d'équivalent SOF.
    """
    event_type = ESCALE_ACTION_TO_SOF.get(op.action)
    if event_type is None or event_type not in SOF_EVENT_TYPES:
        return
    try:
        occurred_at = op.actual_start or op.planned_start or datetime.now(UTC)
        existing = (
            await db.execute(
                select(SofEvent.id).where(
                    SofEvent.leg_id == op.leg_id,
                    SofEvent.event_type == event_type,
                    SofEvent.occurred_at == occurred_at,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        e = SofEvent(
            leg_id=op.leg_id,
            event_type=event_type,
            label=op.label,
            occurred_at=occurred_at,
            notes=f"Auto depuis escale (op #{op.id})",
            recorded_by_id=user.id,
            recorded_by_name=user.full_name or user.username,
        )
        db.add(e)
        await db.flush()
        await activity_record(
            db,
            action="create",
            user_id=user.id,
            user_name=user.full_name or user.username,
            user_role=user.role,
            module="escale",
            entity_type="sof_event",
            entity_id=e.id,
            entity_label=f"{event_type}@{occurred_at.isoformat()}",
            detail=f"auto from escale op {op.id}",
            ip_address=_client_ip(request),
        )
    except Exception:
        logger.exception("SOF auto-creation failed for escale op %s (action=%s)", op.id, op.action)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def escale_index(
    request: Request,
    vessel: str | None = None,
    year: int | None = None,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "C")),
) -> HTMLResponse:
    from app.services.leg_filter import build_leg_filter, set_leg_filter_cookie

    # Module de filtrage standard (hérite du leg choisi sur /onboard via cookie).
    f = await build_leg_filter(db, vessel=vessel, year=year, leg_id=leg_id, request=request)
    vessels = f["vessels"]
    selected_vessel = f["selected_vessel"]
    current_year = f["current_year"]
    years = f["years"]
    legs = f["legs"]
    leg_id = f["leg_id"]

    selected_leg = None
    operations: list[EscaleOperation] = []
    shifts: list[DockerShift] = []
    pol = pod = None
    vessel_status = None
    stowage_by_hold: dict[str, dict] = {}
    if leg_id:
        selected_leg = await db.get(Leg, leg_id)
        if selected_leg:
            operations = list(
                (
                    await db.execute(
                        select(EscaleOperation)
                        .where(EscaleOperation.leg_id == leg_id)
                        .order_by(EscaleOperation.planned_start.asc())
                    )
                )
                .scalars()
                .all()
            )
            shifts = list(
                (
                    await db.execute(
                        select(DockerShift)
                        .where(DockerShift.leg_id == leg_id)
                        .order_by(DockerShift.planned_start.asc())
                    )
                )
                .scalars()
                .all()
            )
            pol = await db.get(Port, selected_leg.departure_port_id)
            pod = await db.get(Port, selected_leg.arrival_port_id)
            vessel_status = "en_mer" if (selected_leg.atd and not selected_leg.ata) else "a_quai"
            # B3 — occupation du plan d'arrimage par cale, pour relier le
            # planning dockers au stowage. Best-effort : ne casse jamais la
            # page si le plan/les items sont absents ou en erreur.
            try:
                stowage_by_hold = await occupation_by_hold(db, selected_leg.id)
            except Exception:
                logger.exception("occupation_by_hold failed for leg %s", selected_leg.id)
                stowage_by_hold = {}

    f["selected_leg"] = selected_leg
    response = templates.TemplateResponse(
        "staff/escale/index.html",
        {
            "request": request,
            "user": user,
            # Module de filtrage standard (cf. staff/_leg_filter.html).
            "leg_filter_ctx": f,
            "vessels": vessels,
            "selected_vessel": selected_vessel,
            "years": years,
            "current_year": current_year,
            "legs": legs,
            "selected_leg": selected_leg,
            "leg_id": leg_id,
            "operations": operations,
            "shifts": shifts,
            "pol": pol,
            "pod": pod,
            "stowage_by_hold": stowage_by_hold,
            "holds": HOLDS,
            "vessel_status": vessel_status,
            "leg_locked": _escale_locked(selected_leg) if selected_leg else False,
            "escale_locked_by": selected_leg.escale_locked_by if selected_leg else None,
            "escale_locked_at": selected_leg.escale_locked_at if selected_leg else None,
            "leg_terminated": (
                bool(selected_leg and selected_leg.atd and selected_leg.ata)
                if selected_leg
                else False
            ),
            "operation_types": OPERATION_TYPES,
            "operation_actions": OPERATION_ACTIONS,
            "directions": DIRECTIONS,
        },
    )
    set_leg_filter_cookie(response, f)
    return response


@router.post("/legs/{leg_id}/operations")
async def create_operation(
    leg_id: int,
    request: Request,
    direction: str = Form("BOTH"),
    operation_type: str = Form(...),
    action: str = Form(...),
    label: str | None = Form(None),
    planned_start: str | None = Form(None),
    planned_end: str | None = Form(None),
    cost_forecast: float | None = Form(None),
    cost_actual: float | None = Form(None),
    notes: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    _assert_escale_unlocked(leg)
    op = EscaleOperation(
        leg_id=leg_id,
        direction=direction,
        operation_type=operation_type,
        action=action,
        label=label,
        planned_start=datetime.fromisoformat(planned_start) if planned_start else None,
        planned_end=datetime.fromisoformat(planned_end) if planned_end else None,
        cost_forecast=cost_forecast,
        cost_actual=cost_actual,
        notes=notes,
    )
    db.add(op)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="escale",
        entity_type="escale_operation",
        entity_id=op.id,
        entity_label=f"{operation_type}/{action} leg={leg_id}",
        ip_address=_client_ip(request),
    )
    # FLX-04 — relier escale ↔ onboard : génère le SOF équivalent.
    await _sync_sof_from_operation(db, request, user, op)
    return RedirectResponse(url=f"/escale?leg_id={leg_id}", status_code=303)


@router.post("/operations/{op_id}/start")
async def start_operation(
    op_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    op = await db.get(EscaleOperation, op_id)
    if op is None:
        raise HTTPException(status_code=404)
    leg = await db.get(Leg, op.leg_id)
    if leg is not None:
        _assert_escale_unlocked(leg)
    op.actual_start = datetime.now(UTC)
    op.status = "in_progress"
    await db.flush()
    # FLX-04 — l'opération démarrée matérialise son SOF (occurred_at réel).
    await _sync_sof_from_operation(db, request, user, op)
    return RedirectResponse(url=f"/escale?leg_id={op.leg_id}", status_code=303)


@router.post("/operations/{op_id}/end")
async def end_operation(
    op_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    op = await db.get(EscaleOperation, op_id)
    if op is None:
        raise HTTPException(status_code=404)
    leg = await db.get(Leg, op.leg_id)
    if leg is not None:
        _assert_escale_unlocked(leg)
    op.actual_end = datetime.now(UTC)
    op.status = "completed"
    await db.flush()
    return RedirectResponse(url=f"/escale?leg_id={op.leg_id}", status_code=303)


def _normalize_hold(hold: str | None) -> str | None:
    """Valide la cale d'un shift docker contre ``stowage.HOLDS``.

    Renvoie le code HOLD ("AR"/"AV") s'il est connu, sinon ``None`` (cale
    non spécifiée) — y compris pour les sentinelles vides ("", "—").
    """
    if not hold:
        return None
    hold = hold.strip().upper()
    return hold if hold in HOLDS else None


@router.post("/legs/{leg_id}/dockers")
async def create_docker_shift(
    leg_id: int,
    request: Request,
    direction: str = Form("BOTH"),
    company: str | None = Form(None),
    nb_dockers: int = Form(0),
    palettes_target: int | None = Form(None),
    hold: str | None = Form(None),
    planned_start: str | None = Form(None),
    planned_end: str | None = Form(None),
    cost_eur: float | None = Form(None),
    notes: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    _assert_escale_unlocked(leg)
    s = DockerShift(
        leg_id=leg_id,
        direction=direction,
        company=company,
        nb_dockers=nb_dockers,
        palettes_target=palettes_target,
        hold=_normalize_hold(hold),
        planned_start=datetime.fromisoformat(planned_start) if planned_start else None,
        planned_end=datetime.fromisoformat(planned_end) if planned_end else None,
        cost_eur=cost_eur,
        notes=notes,
    )
    db.add(s)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="escale",
        entity_type="docker_shift",
        entity_id=s.id,
        entity_label=f"shift {company} leg={leg_id} cale={s.hold or '—'}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/escale?leg_id={leg_id}", status_code=303)


@router.post("/dockers/{shift_id}/progress")
async def docker_progress(
    shift_id: int,
    request: Request,
    palettes_done: int = Form(0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    s = await db.get(DockerShift, shift_id)
    if s is None:
        raise HTTPException(status_code=404)
    leg = await db.get(Leg, s.leg_id)
    if leg is not None:
        _assert_escale_unlocked(leg)
    s.palettes_done = palettes_done
    await db.flush()
    return RedirectResponse(url=f"/escale?leg_id={s.leg_id}", status_code=303)


@router.post("/legs/{leg_id}/lock")
async def lock_leg(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    """Verrouille l'escale du leg (clôture administrative).

    Renseigne ``escale_locked_at`` / ``escale_locked_by`` ; dès lors les
    endpoints de modification d'escale refusent toute écriture
    (``_assert_escale_unlocked``).
    """
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    leg.escale_locked_at = datetime.now(UTC)
    leg.escale_locked_by = user.full_name or user.username
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="escale",
        entity_type="leg",
        entity_id=leg.id,
        entity_label=leg.leg_code,
        detail="escale locked",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/escale?leg_id={leg_id}", status_code=303)


@router.post("/legs/{leg_id}/unlock")
async def unlock_leg(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "S")),
):
    """Déverrouille l'escale du leg (permission S = Suppress)."""
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    leg.escale_locked_at = None
    leg.escale_locked_by = None
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="escale",
        entity_type="leg",
        entity_id=leg.id,
        entity_label=leg.leg_code,
        detail="escale unlocked",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/escale?leg_id={leg_id}", status_code=303)


@router.get("/legs/{leg_id}/sof.pdf")
async def escale_sof_pdf(
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "C")),
):
    """Génère le SOF escale (opérations + shifts dockers) en PDF WeasyPrint."""

    from fastapi.responses import Response
    from weasyprint import HTML  # local import — heavy native deps

    from app.config import settings

    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)

    pol = await db.get(Port, leg.departure_port_id) if leg.departure_port_id else None
    pod = await db.get(Port, leg.arrival_port_id) if leg.arrival_port_id else None
    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id else None

    operations = list(
        (
            await db.execute(
                select(EscaleOperation)
                .where(EscaleOperation.leg_id == leg_id)
                .order_by(EscaleOperation.planned_start.asc())
            )
        )
        .scalars()
        .all()
    )

    shifts = list(
        (
            await db.execute(
                select(DockerShift)
                .where(DockerShift.leg_id == leg_id)
                .order_by(DockerShift.planned_start.asc())
            )
        )
        .scalars()
        .all()
    )

    tpl = templates.get_template("pdf/sof_escale.html")
    html = tpl.render(
        leg=leg,
        pol=pol,
        pod=pod,
        vessel=vessel,
        operations=operations,
        shifts=shifts,
        issued_at=datetime.now(UTC),
        site_url=settings.site_url,
    )
    pdf = HTML(string=html, base_url=settings.site_url).write_pdf()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="SOF_{leg.leg_code}.pdf"'},
    )


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )
