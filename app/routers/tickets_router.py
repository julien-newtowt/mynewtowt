"""Escale ticketing routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.leg import Leg
from app.models.user import User
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.tickets import (
    CATEGORIES,
    CATEGORY_LABELS,
    KANBAN_COLUMNS,
    PRIORITIES,
    PRIORITY_LABELS,
    PRIORITY_SLA_HOURS,
    STATUS_LABELS,
    TicketError,
    add_comment,
    assign_ticket,
    change_status,
    create_ticket,
    get_by_reference,
    is_sla_breached,
    list_for_kanban,
    stats as ticket_stats,
)
from app.templating import templates

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("", response_class=HTMLResponse)
async def kanban(
    request: Request,
    priority: str | None = None,
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("tickets", "C")),
) -> HTMLResponse:
    columns = await list_for_kanban(db, priority=priority, category=category)
    s = await ticket_stats(db)
    return templates.TemplateResponse(
        "staff/tickets/kanban.html",
        {
            "request": request,
            "user": user,
            "columns": columns,
            "kanban_cols": KANBAN_COLUMNS,
            "status_labels": STATUS_LABELS,
            "category_labels": CATEGORY_LABELS,
            "priority_labels": PRIORITY_LABELS,
            "stats": s,
            "filter_priority": priority,
            "filter_category": category,
            "categories": CATEGORIES,
            "priorities": PRIORITIES,
            "is_breached": is_sla_breached,
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def new_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("tickets", "M")),
) -> HTMLResponse:
    legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()).limit(50))).scalars().all())
    users = list((await db.execute(select(User).where(User.is_active.is_(True)).order_by(User.full_name))).scalars().all())
    return templates.TemplateResponse(
        "staff/tickets/new.html",
        {
            "request": request,
            "user": user,
            "legs": legs,
            "users": users,
            "categories": CATEGORIES,
            "category_labels": CATEGORY_LABELS,
            "priorities": PRIORITIES,
            "priority_labels": PRIORITY_LABELS,
            "sla_hours": PRIORITY_SLA_HOURS,
            "error": None,
        },
    )


@router.post("/new", response_class=HTMLResponse)
async def create_action(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("tickets", "M")),
) -> HTMLResponse:
    form = await request.form()
    try:
        ticket = await create_ticket(
            db,
            category=form.get("category", ""),
            priority=form.get("priority", ""),
            title=form.get("title", ""),
            description=form.get("description", ""),
            leg_id=int(form["leg_id"]) if form.get("leg_id") else None,
            assigned_to_id=int(form["assigned_to_id"]) if form.get("assigned_to_id") else None,
            external_contact=form.get("external_contact") or None,
            created_by_id=user.id,
        )
    except TicketError as e:
        legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()).limit(50))).scalars().all())
        users = list((await db.execute(select(User).where(User.is_active.is_(True)))).scalars().all())
        return templates.TemplateResponse(
            "staff/tickets/new.html",
            {
                "request": request,
                "user": user,
                "legs": legs,
                "users": users,
                "categories": CATEGORIES,
                "category_labels": CATEGORY_LABELS,
                "priorities": PRIORITIES,
                "priority_labels": PRIORITY_LABELS,
                "sla_hours": PRIORITY_SLA_HOURS,
                "error": str(e),
            },
            status_code=400,
        )

    await activity_record(
        db,
        action="ticket_create",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="tickets",
        entity_type="ticket",
        entity_id=ticket.id,
        entity_label=ticket.reference,
        detail=f"{ticket.priority}/{ticket.category}: {ticket.title}",
    )
    return RedirectResponse(url=f"/tickets/{ticket.reference}", status_code=303)


@router.get("/{ref}", response_class=HTMLResponse)
async def detail(
    request: Request,
    ref: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("tickets", "C")),
) -> HTMLResponse:
    ticket = await get_by_reference(db, ref)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    await db.refresh(ticket, attribute_names=["comments"])
    leg = await db.get(Leg, ticket.leg_id) if ticket.leg_id else None
    assignee = await db.get(User, ticket.assigned_to_id) if ticket.assigned_to_id else None
    users = list((await db.execute(select(User).where(User.is_active.is_(True)))).scalars().all())
    return templates.TemplateResponse(
        "staff/tickets/detail.html",
        {
            "request": request,
            "user": user,
            "ticket": ticket,
            "leg": leg,
            "assignee": assignee,
            "users": users,
            "category_labels": CATEGORY_LABELS,
            "priority_labels": PRIORITY_LABELS,
            "status_labels": STATUS_LABELS,
            "is_breached": is_sla_breached(ticket),
        },
    )


@router.post("/{ref}/status", response_class=HTMLResponse)
async def status_action(
    ref: str,
    new_status: str = Form(...),
    reason: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("tickets", "M")),
) -> RedirectResponse:
    ticket = await get_by_reference(db, ref)
    if not ticket:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        await change_status(db, ticket, new_status, reason=reason or None)
    except TicketError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await activity_record(
        db,
        action="ticket_status",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="tickets",
        entity_type="ticket",
        entity_id=ticket.id,
        entity_label=ticket.reference,
        detail=f"→ {new_status}",
    )
    return RedirectResponse(url=f"/tickets/{ticket.reference}", status_code=303)


@router.post("/{ref}/assign", response_class=HTMLResponse)
async def assign_action(
    ref: str,
    assigned_to_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("tickets", "M")),
) -> RedirectResponse:
    ticket = await get_by_reference(db, ref)
    if not ticket:
        raise HTTPException(status_code=404, detail="Not found")
    new_id = int(assigned_to_id) if assigned_to_id else None
    await assign_ticket(db, ticket, new_id)
    return RedirectResponse(url=f"/tickets/{ticket.reference}", status_code=303)


@router.post("/{ref}/comment", response_class=HTMLResponse)
async def comment_action(
    ref: str,
    body: str = Form(...),
    is_internal: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("tickets", "M")),
) -> RedirectResponse:
    ticket = await get_by_reference(db, ref)
    if not ticket:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        await add_comment(
            db, ticket,
            body=body, author_id=user.id, author_name=user.username,
            is_internal=(is_internal == "on"),
        )
    except TicketError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(url=f"/tickets/{ticket.reference}", status_code=303)
