"""Stowage — plan d'arrimage 18 zones, suggestion auto et impression.

Vue principale : grille 3 ponts × 2 cales × 3 blocs avec affectations.
Algorithme de suggestion : services.stowage.suggest_assignments.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.commercial import Order
from app.models.leg import Leg
from app.models.packing_list import PackingList, PackingListBatch
from app.models.stowage import (
    DANGEROUS_ZONES,
    ZONE_LOADING_ORDER,
    StowageItem,
    StowagePlan,
)
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.stowage import suggest_assignments, zone_usage_summary
from app.templating import templates

router = APIRouter(prefix="/stowage", tags=["stowage"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def stowage_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    plans = list(
        (await db.execute(select(StowagePlan).order_by(StowagePlan.updated_at.desc()).limit(50)))
        .scalars()
        .all()
    )
    legs_by_id: dict[int, Leg] = {}
    for p in plans:
        leg = await db.get(Leg, p.leg_id)
        if leg is not None:
            legs_by_id[p.leg_id] = leg
    return templates.TemplateResponse(
        "staff/stowage/index.html",
        {"request": request, "user": user, "plans": plans, "legs_by_id": legs_by_id},
    )


@router.get("/legs/{leg_id}", response_class=HTMLResponse)
async def stowage_plan_view(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    plan = (
        await db.execute(
            select(StowagePlan)
            .options(selectinload(StowagePlan.items))
            .where(StowagePlan.leg_id == leg_id)
        )
    ).scalar_one_or_none()
    if plan is None:
        plan = StowagePlan(leg_id=leg_id, status="draft")
        db.add(plan)
        await db.flush()
    items = plan.items if plan.items is not None else []
    usage = zone_usage_summary(
        [
            {"zone": it.zone, "pallet_format": it.pallet_format, "pallet_count": it.pallet_count}
            for it in items
        ]
    )
    return templates.TemplateResponse(
        "staff/stowage/plan.html",
        {
            "request": request,
            "user": user,
            "leg": leg,
            "plan": plan,
            "items": items,
            "zones": ZONE_LOADING_ORDER,
            "dangerous_zones": DANGEROUS_ZONES,
            "usage": usage,
        },
    )


@router.post("/plans/{plan_id}/items")
async def add_item(
    plan_id: int,
    request: Request,
    zone: str = Form(...),
    pallet_format: str = Form("EPAL"),
    pallet_count: int = Form(1),
    weight_kg: float | None = Form(None),
    is_dangerous: bool = Form(False),
    is_oversized: bool = Form(False),
    notes: str | None = Form(None),
    order_id: int | None = Form(None),
    batch_id: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    plan = await db.get(StowagePlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404)
    if zone not in ZONE_LOADING_ORDER:
        raise HTTPException(status_code=400, detail="zone invalide")
    item = StowageItem(
        plan_id=plan_id,
        order_id=order_id,
        batch_id=batch_id,
        zone=zone,
        pallet_format=pallet_format,
        pallet_count=pallet_count,
        weight_kg=weight_kg,
        is_dangerous=is_dangerous,
        is_oversized=is_oversized,
        notes=notes,
    )
    db.add(item)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="cargo",
        entity_type="stowage_item",
        entity_id=item.id,
        entity_label=f"plan={plan_id} zone={zone}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/stowage/legs/{plan.leg_id}", status_code=303)


@router.post("/plans/{plan_id}/suggest")
async def suggest_plan(
    plan_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    """Génère des affectations en aspirant les batches PL des orders du leg."""
    plan = await db.get(StowagePlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404)
    # Trouve les orders du leg
    orders = list(
        (await db.execute(select(Order).where(Order.leg_id == plan.leg_id))).scalars().all()
    )
    items_in: list[dict] = []
    for o in orders:
        pls = list(
            (await db.execute(select(PackingList).where(PackingList.order_id == o.id)))
            .scalars()
            .all()
        )
        for pl in pls:
            batches = list(
                (
                    await db.execute(
                        select(PackingListBatch).where(PackingListBatch.packing_list_id == pl.id)
                    )
                )
                .scalars()
                .all()
            )
            for b in batches:
                items_in.append(
                    {
                        "batch_id": b.id,
                        "order_id": o.id,
                        "pallet_format": b.pallet_format,
                        "pallet_count": b.pallet_count,
                        "is_dangerous": b.hazardous,
                        "is_oversized": _is_oversized(b),
                    }
                )
    placed = suggest_assignments(items_in)
    # Clear previous suggestions and re-add
    for it in plan.items or []:
        await db.delete(it)
    await db.flush()
    for p in placed:
        if p.get("zone") in ("OVERFLOW", None):
            continue
        db.add(
            StowageItem(
                plan_id=plan.id,
                order_id=p.get("order_id"),
                batch_id=p.get("batch_id"),
                zone=p["zone"],
                pallet_format=p["pallet_format"],
                pallet_count=p["pallet_count"],
                is_dangerous=p.get("is_dangerous", False),
                is_oversized=p.get("is_oversized", False),
            )
        )
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="cargo",
        entity_type="stowage_plan",
        entity_id=plan.id,
        entity_label=f"leg={plan.leg_id}",
        detail="auto-suggest",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/stowage/legs/{plan.leg_id}", status_code=303)


@router.post("/plans/{plan_id}/approve")
async def approve_plan(
    plan_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    plan = await db.get(StowagePlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404)
    plan.status = "approved"
    plan.approved_at = datetime.now(UTC)
    plan.approved_by = user.full_name or user.username
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="cargo",
        entity_type="stowage_plan",
        entity_id=plan.id,
        entity_label=f"leg={plan.leg_id}",
        detail="approved",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/stowage/legs/{plan.leg_id}", status_code=303)


def _is_oversized(batch: PackingListBatch) -> bool:
    """Indicates the batch needs SUP_AV (oversize basket : 380×150×220 cm, 5.1 t)."""
    if batch.length_cm and batch.length_cm > 380:
        return True
    if batch.width_cm and batch.width_cm > 150:
        return True
    if batch.height_cm and batch.height_cm > 220:
        return True
    return bool(batch.weight_kg and batch.weight_kg > 5100)


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )
