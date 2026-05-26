"""Escale — operations portuaires + shifts dockers + timelines.

Reprises de la V3.0.0 :
- Liste filtrée par navire + année + leg sélectionné.
- Détail d'un leg : leg-summary + timeline opérations + shifts dockers.
- Création/édition d'opérations (IMPORT/EXPORT/BOTH, type+action, planned/actual).
- Création/édition de shifts dockers (cadence palettes/h).
- Lock de leg (clôture administrative).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.escale import (
    DIRECTIONS, OPERATION_ACTIONS, OPERATION_TYPES,
    DockerShift, EscaleOperation,
)
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.templating import templates

router = APIRouter(prefix="/escale", tags=["escale"])


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
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    selected_vessel = vessel or (vessels[0].code if vessels else None)
    current_year = year or datetime.now(timezone.utc).year
    years = list(range(current_year - 1, current_year + 3))

    stmt_legs = select(Leg).order_by(Leg.etd.asc())
    if selected_vessel:
        v = next((v for v in vessels if v.code == selected_vessel), None)
        if v:
            stmt_legs = stmt_legs.where(Leg.vessel_id == v.id)
    legs = list((await db.execute(stmt_legs)).scalars().all())
    legs = [l for l in legs if l.etd and l.etd.year == current_year]

    selected_leg = None
    operations: list[EscaleOperation] = []
    shifts: list[DockerShift] = []
    pol = pod = None
    vessel_status = None
    if leg_id:
        selected_leg = await db.get(Leg, leg_id)
        if selected_leg:
            operations = list((await db.execute(
                select(EscaleOperation).where(EscaleOperation.leg_id == leg_id)
                .order_by(EscaleOperation.planned_start.asc())
            )).scalars().all())
            shifts = list((await db.execute(
                select(DockerShift).where(DockerShift.leg_id == leg_id)
                .order_by(DockerShift.planned_start.asc())
            )).scalars().all())
            pol = await db.get(Port, selected_leg.departure_port_id)
            pod = await db.get(Port, selected_leg.arrival_port_id)
            vessel_status = (
                "en_mer" if (selected_leg.atd and not selected_leg.ata)
                else "a_quai"
            )

    return templates.TemplateResponse(
        "staff/escale/index.html",
        {
            "request": request, "user": user,
            "vessels": vessels, "selected_vessel": selected_vessel,
            "years": years, "current_year": current_year,
            "legs": legs, "selected_leg": selected_leg, "leg_id": leg_id,
            "operations": operations, "shifts": shifts,
            "pol": pol, "pod": pod, "vessel_status": vessel_status,
            "leg_locked": selected_leg.status == "completed" if selected_leg else False,
            "leg_terminated": bool(selected_leg and selected_leg.atd and selected_leg.ata) if selected_leg else False,
            "operation_types": OPERATION_TYPES,
            "operation_actions": OPERATION_ACTIONS,
            "directions": DIRECTIONS,
        },
    )


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
    notes: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    if not await db.get(Leg, leg_id):
        raise HTTPException(status_code=404)
    op = EscaleOperation(
        leg_id=leg_id,
        direction=direction,
        operation_type=operation_type,
        action=action,
        label=label,
        planned_start=datetime.fromisoformat(planned_start) if planned_start else None,
        planned_end=datetime.fromisoformat(planned_end) if planned_end else None,
        notes=notes,
    )
    db.add(op)
    await db.flush()
    await activity_record(
        db, action="create", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="escale", entity_type="escale_operation",
        entity_id=op.id, entity_label=f"{operation_type}/{action} leg={leg_id}",
        ip_address=_client_ip(request),
    )
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
    op.actual_start = datetime.now(timezone.utc)
    op.status = "in_progress"
    await db.flush()
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
    op.actual_end = datetime.now(timezone.utc)
    op.status = "completed"
    await db.flush()
    return RedirectResponse(url=f"/escale?leg_id={op.leg_id}", status_code=303)


@router.post("/legs/{leg_id}/dockers")
async def create_docker_shift(
    leg_id: int,
    request: Request,
    direction: str = Form("BOTH"),
    company: str | None = Form(None),
    nb_dockers: int = Form(0),
    palettes_target: int | None = Form(None),
    planned_start: str | None = Form(None),
    planned_end: str | None = Form(None),
    cost_eur: float | None = Form(None),
    notes: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    if not await db.get(Leg, leg_id):
        raise HTTPException(status_code=404)
    s = DockerShift(
        leg_id=leg_id, direction=direction,
        company=company, nb_dockers=nb_dockers,
        palettes_target=palettes_target,
        planned_start=datetime.fromisoformat(planned_start) if planned_start else None,
        planned_end=datetime.fromisoformat(planned_end) if planned_end else None,
        cost_eur=cost_eur, notes=notes,
    )
    db.add(s)
    await db.flush()
    await activity_record(
        db, action="create", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="escale", entity_type="docker_shift",
        entity_id=s.id, entity_label=f"shift {company} leg={leg_id}",
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
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    leg.status = "completed"
    await db.flush()
    await activity_record(
        db, action="update", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="escale", entity_type="leg",
        entity_id=leg.id, entity_label=leg.leg_code, detail="locked",
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
    from datetime import timezone

    from fastapi.responses import Response
    from weasyprint import HTML  # local import — heavy native deps

    from app.config import settings

    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)

    pol = await db.get(Port, leg.departure_port_id) if leg.departure_port_id else None
    pod = await db.get(Port, leg.arrival_port_id) if leg.arrival_port_id else None
    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id else None

    operations = list((await db.execute(
        select(EscaleOperation)
        .where(EscaleOperation.leg_id == leg_id)
        .order_by(EscaleOperation.planned_start.asc())
    )).scalars().all())

    shifts = list((await db.execute(
        select(DockerShift)
        .where(DockerShift.leg_id == leg_id)
        .order_by(DockerShift.planned_start.asc())
    )).scalars().all())

    tpl = templates.get_template("pdf/sof_escale.html")
    html = tpl.render(
        leg=leg,
        pol=pol,
        pod=pod,
        vessel=vessel,
        operations=operations,
        shifts=shifts,
        issued_at=datetime.now(timezone.utc),
        site_url=settings.site_url,
    )
    pdf = HTML(string=html, base_url=settings.site_url).write_pdf()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="SOF_{leg.leg_code}.pdf"'
        },
    )


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
