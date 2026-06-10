"""Cargo portal — accès public par token UUID hex 24 caractères, sans compte.

Le client expéditeur reçoit un lien `/p/{token}` valide 90 jours. Il peut :
- Voir le récap de sa commande, le navire, le voyage, le plan d'arrimage.
- Saisir / mettre à jour sa packing list (batches).
- Échanger des messages avec l'armateur.
- Déposer des documents (douane, MSDS, etc.).

Sécurité :
- Token validé à chaque hit (`PackingList.token_expires_at`).
- Token jamais loggé en clair : sha256 dans `portal_access_logs.token_hash`.
- Rate-limit per-token (scope='portal_token', service rate_limit existant).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.commercial import Order
from app.models.leg import Leg
from app.models.packing_list import (
    PackingList,
    PackingListBatch,
    PortalMessage,
)
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.packing_list import (
    can_modify,
    get_by_token,
    log_portal_access,
    record_audit,
)
from app.templating import templates

router = APIRouter(prefix="/p", tags=["cargo-portal"])


async def _load_or_410(db: AsyncSession, token: str, request: Request) -> PackingList:
    pl = await get_by_token(db, token)
    if pl is None:
        # Always log access attempts, even invalid (hashed)
        await log_portal_access(
            db,
            token=token,
            packing_list_id=None,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            path=request.url.path,
        )
        raise HTTPException(status_code=410, detail="Lien expiré ou invalide")
    await log_portal_access(
        db,
        token=token,
        packing_list_id=pl.id,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        path=request.url.path,
    )
    return pl


@router.get("/{token}", response_class=HTMLResponse)
async def portal_home(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    pl = await _load_or_410(db, token, request)
    order = await db.get(Order, pl.order_id)
    leg = await db.get(Leg, order.leg_id) if (order and order.leg_id) else None
    vessel = await db.get(Vessel, leg.vessel_id) if leg else None
    pol = await db.get(Port, leg.departure_port_id) if leg else None
    pod = await db.get(Port, leg.arrival_port_id) if leg else None
    return templates.TemplateResponse(
        "portal/home.html",
        {
            "request": request,
            "pl": pl,
            "order": order,
            "leg": leg,
            "vessel": vessel,
            "pol": pol,
            "pod": pod,
            "token": token,
        },
    )


@router.get("/{token}/packing", response_class=HTMLResponse)
async def portal_packing(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    pl = await _load_or_410(db, token, request)
    pl_full = (
        await db.execute(
            select(PackingList)
            .options(selectinload(PackingList.batches))
            .where(PackingList.id == pl.id)
        )
    ).scalar_one()
    return templates.TemplateResponse(
        "portal/packing.html",
        {"request": request, "pl": pl_full, "token": token},
    )


@router.post("/{token}/packing/batches")
async def portal_packing_add(
    token: str,
    request: Request,
    pallet_format: str = Form("EPAL"),
    pallet_count: int = Form(1),
    description: str | None = Form(None),
    hs_code: str | None = Form(None),
    weight_kg: float | None = Form(None),
    cubage_m3: float | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    pl = await _load_or_410(db, token, request)
    if not can_modify(pl):
        raise HTTPException(status_code=409, detail="packing list verrouillée")
    # Compute next batch number
    existing = list(
        (
            await db.execute(
                select(PackingListBatch).where(PackingListBatch.packing_list_id == pl.id)
            )
        )
        .scalars()
        .all()
    )
    b = PackingListBatch(
        packing_list_id=pl.id,
        batch_number=len(existing) + 1,
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
        db,
        packing_list_id=pl.id,
        batch_id=b.id,
        actor="client",
        actor_name=None,
        field="_create_batch",
        old_value=None,
        new_value=f"{pallet_count}×{pallet_format}",
    )
    return RedirectResponse(url=f"/p/{token}/packing", status_code=303)


@router.post("/{token}/packing/submit")
async def portal_packing_submit(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    pl = await _load_or_410(db, token, request)
    if not can_modify(pl):
        raise HTTPException(status_code=409)
    pl.status = "submitted"
    await db.flush()
    return RedirectResponse(url=f"/p/{token}/packing", status_code=303)


@router.get("/{token}/messages", response_class=HTMLResponse)
async def portal_messages(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    pl = await _load_or_410(db, token, request)
    messages = list(
        (
            await db.execute(
                select(PortalMessage)
                .where(PortalMessage.packing_list_id == pl.id)
                .order_by(PortalMessage.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        "portal/messages.html",
        {"request": request, "pl": pl, "messages": messages, "token": token},
    )


@router.post("/{token}/messages")
async def portal_message_post(
    token: str,
    request: Request,
    body: str = Form(...),
    sender_name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    pl = await _load_or_410(db, token, request)
    db.add(
        PortalMessage(
            packing_list_id=pl.id,
            sender="client",
            sender_name=sender_name.strip()[:200],
            body=body.strip(),
        )
    )
    await db.flush()
    return RedirectResponse(url=f"/p/{token}/messages", status_code=303)


@router.get("/{token}/privacy", response_class=HTMLResponse)
async def portal_privacy(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    pl = await _load_or_410(db, token, request)
    return templates.TemplateResponse(
        "portal/privacy.html",
        {"request": request, "pl": pl, "token": token},
    )


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )
