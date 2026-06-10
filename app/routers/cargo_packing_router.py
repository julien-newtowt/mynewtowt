"""Packing list — vue staff interne (token-based portal = cargo_portal_router).

Reprise de la V3.0.0. Workflow draft → submitted → locked. Audit trail
field-by-field. Verrouillage par un staff après validation côté armateur.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.commercial import Order
from app.models.packing_list import (
    PackingList,
    PackingListBatch,
    PortalMessage,
)
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.packing_list import (
    can_modify,
    lock,
    record_audit,
    unlock,
)
from app.templating import templates

router = APIRouter(prefix="/cargo/packing-lists", tags=["cargo-packing"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def packing_lists_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    pls = list((await db.execute(
        select(PackingList).options(selectinload(PackingList.batches))
        .order_by(PackingList.updated_at.desc()).limit(100)
    )).scalars().all())
    return templates.TemplateResponse(
        "staff/cargo/packing_lists.html",
        {"request": request, "user": user, "packing_lists": pls},
    )


@router.post("/from-order/{order_id}")
async def create_for_order(
    order_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    order = await db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404)
    existing = (await db.execute(
        select(PackingList).where(PackingList.order_id == order_id)
    )).scalar_one_or_none()
    if existing is not None:
        return RedirectResponse(url=f"/cargo/packing-lists/{existing.id}", status_code=303)
    pl = PackingList(order_id=order_id, status="draft")
    db.add(pl)
    await db.flush()
    await activity_record(
        db, action="create", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="cargo", entity_type="packing_list",
        entity_id=pl.id, entity_label=f"PL for {order.reference}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/cargo/packing-lists/{pl.id}", status_code=303)


@router.get("/{pl_id}", response_class=HTMLResponse)
async def packing_list_detail(
    pl_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    pl = (await db.execute(
        select(PackingList).options(selectinload(PackingList.batches))
        .where(PackingList.id == pl_id)
    )).scalar_one_or_none()
    if pl is None:
        raise HTTPException(status_code=404)
    order = await db.get(Order, pl.order_id)
    messages = list((await db.execute(
        select(PortalMessage).where(PortalMessage.packing_list_id == pl_id)
        .order_by(PortalMessage.created_at.desc()).limit(50)
    )).scalars().all())
    return templates.TemplateResponse(
        "staff/cargo/packing_list_detail.html",
        {"request": request, "user": user, "pl": pl, "order": order, "messages": messages},
    )


@router.post("/{pl_id}/batches")
async def add_batch(
    pl_id: int,
    request: Request,
    pallet_format: str = Form("EPAL"),
    pallet_count: int = Form(1),
    description: str | None = Form(None),
    hs_code: str | None = Form(None),
    weight_kg: float | None = Form(None),
    cubage_m3: float | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    pl = await db.get(PackingList, pl_id)
    if pl is None or not can_modify(pl):
        raise HTTPException(status_code=409, detail="packing list verrouillée")
    seq = (len(pl.batches) if hasattr(pl, "batches") and pl.batches is not None else 0) + 1
    b = PackingListBatch(
        packing_list_id=pl.id,
        batch_number=seq,
        pallet_format=pallet_format,
        pallet_count=pallet_count,
        description=description,
        hs_code=hs_code,
        weight_kg=weight_kg,
        cubage_m3=cubage_m3,
    )
    db.add(b)
    await db.flush()
    await record_audit(
        db, packing_list_id=pl.id, batch_id=b.id, actor="staff",
        actor_name=user.full_name or user.username,
        field="_create_batch", old_value=None,
        new_value=f"{pallet_count}×{pallet_format}",
    )
    return RedirectResponse(url=f"/cargo/packing-lists/{pl_id}", status_code=303)


@router.post("/{pl_id}/lock")
async def lock_pl(
    pl_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    pl = await db.get(PackingList, pl_id)
    if pl is None:
        raise HTTPException(status_code=404)
    await lock(db, pl, locked_by=user.full_name or user.username)
    await activity_record(
        db, action="update", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="cargo", entity_type="packing_list",
        entity_id=pl.id, entity_label=str(pl.id), detail="locked",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/cargo/packing-lists/{pl_id}", status_code=303)


@router.post("/{pl_id}/unlock")
async def unlock_pl(
    pl_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "S")),
):
    pl = await db.get(PackingList, pl_id)
    if pl is None:
        raise HTTPException(status_code=404)
    await unlock(db, pl)
    await activity_record(
        db, action="update", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="cargo", entity_type="packing_list",
        entity_id=pl.id, entity_label=str(pl.id), detail="unlocked",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/cargo/packing-lists/{pl_id}", status_code=303)


@router.post("/{pl_id}/messages")
async def post_message_staff(
    pl_id: int,
    request: Request,
    body: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    pl = await db.get(PackingList, pl_id)
    if pl is None:
        raise HTTPException(status_code=404)
    db.add(PortalMessage(
        packing_list_id=pl.id, sender="staff",
        sender_name=user.full_name or user.username,
        body=body.strip(),
    ))
    await db.flush()
    return RedirectResponse(url=f"/cargo/packing-lists/{pl_id}", status_code=303)


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
