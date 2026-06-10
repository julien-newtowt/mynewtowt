"""Notifications dashboard — list / toggle-read / archive endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_staff
from app.database import get_db
from app.models.notification import Notification
from app.services.notifications import archive, mark_read

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.post("/{notif_id}/toggle-read")
async def toggle_read(
    notif_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
):
    n = await db.get(Notification, notif_id)
    if n is None:
        raise HTTPException(status_code=404)
    n.is_read = not n.is_read
    await db.flush()
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/{notif_id}/archive")
async def archive_one(
    notif_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
):
    n = await db.get(Notification, notif_id)
    if n is None:
        raise HTTPException(status_code=404)
    await archive(db, n)
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/archive-read")
async def archive_all_read(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
):
    stmt = select(Notification).where(Notification.is_read.is_(True)).where(Notification.is_archived.is_(False))
    for n in (await db.execute(stmt)).scalars().all():
        await archive(db, n)
    return RedirectResponse(url="/dashboard", status_code=303)
