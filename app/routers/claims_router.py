"""Claims — sinistres cargo / crew / hull / war_risk / third_party.

Workflow status :
  open → in_review → provisioned → settled (ou rejected) → closed
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.booking import Booking
from app.models.claim import CLAIM_STATUSES, CLAIM_TYPES, Claim, ClaimTimelineEntry
from app.models.leg import Leg
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.templating import templates

router = APIRouter(prefix="/claims", tags=["claims"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def claims_index(
    request: Request,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "C")),
) -> HTMLResponse:
    stmt = select(Claim).order_by(Claim.declared_at.desc())
    if status:
        stmt = stmt.where(Claim.status == status)
    claims = list((await db.execute(stmt)).scalars().all())
    counts = dict.fromkeys(CLAIM_STATUSES, 0)
    for c in claims:
        counts[c.status] = counts.get(c.status, 0) + 1
    return templates.TemplateResponse(
        "staff/claims/index.html",
        {
            "request": request,
            "user": user,
            "claims": claims,
            "counts": counts,
            "claim_types": CLAIM_TYPES,
            "filter_status": status,
            "statuses": CLAIM_STATUSES,
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def claim_new_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "M")),
):
    legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()).limit(50))).scalars().all())
    return templates.TemplateResponse(
        "staff/claims/new.html",
        {"request": request, "user": user, "legs": legs, "claim_types": CLAIM_TYPES},
    )


@router.post("")
@router.post("/")
async def claim_create(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    claim_type: str = Form(...),
    occurred_at: str = Form(...),
    leg_id: int | None = Form(None),
    booking_id: int | None = Form(None),
    provision_eur: float | None = Form(None),
    insurer: str | None = Form(None),
    cargo_position: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "M")),
):
    if claim_type not in CLAIM_TYPES:
        raise HTTPException(status_code=400, detail="invalid claim_type")
    # Sequence reference CLM-YYYY-NNNN
    year = datetime.now(UTC).year
    seq = (
        (await db.scalar(select(func.count(Claim.id)).where(Claim.reference.like(f"CLM-{year}-%"))))
        or 0
    ) + 1
    ref = f"CLM-{year}-{seq:04d}"
    c = Claim(
        reference=ref,
        claim_type=claim_type,
        leg_id=leg_id,
        booking_id=booking_id,
        title=title.strip(),
        description=description,
        status="open",
        occurred_at=datetime.fromisoformat(occurred_at),
        provision_eur=Decimal(str(provision_eur)) if provision_eur else None,
        insurer=insurer,
        cargo_position=cargo_position,
        created_by_id=user.id,
    )
    db.add(c)
    await db.flush()
    db.add(
        ClaimTimelineEntry(
            claim_id=c.id,
            author_id=user.id,
            author_name=user.full_name or user.username,
            kind="open",
            body=f"Claim ouvert : {title}",
        )
    )
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="claims",
        entity_type="claim",
        entity_id=c.id,
        entity_label=ref,
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/claims/{c.id}", status_code=303)


@router.get("/{claim_id}", response_class=HTMLResponse)
async def claim_detail(
    claim_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "C")),
) -> HTMLResponse:
    claim = (
        await db.execute(
            select(Claim).options(selectinload(Claim.timeline)).where(Claim.id == claim_id)
        )
    ).scalar_one_or_none()
    if claim is None:
        raise HTTPException(status_code=404)
    leg = await db.get(Leg, claim.leg_id) if claim.leg_id else None
    booking = await db.get(Booking, claim.booking_id) if claim.booking_id else None
    return templates.TemplateResponse(
        "staff/claims/detail.html",
        {
            "request": request,
            "user": user,
            "claim": claim,
            "leg": leg,
            "booking": booking,
            "statuses": CLAIM_STATUSES,
        },
    )


@router.post("/{claim_id}/status")
async def claim_update_status(
    claim_id: int,
    request: Request,
    new_status: str = Form(...),
    note: str | None = Form(None),
    settled_eur: float | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "M")),
):
    if new_status not in CLAIM_STATUSES:
        raise HTTPException(status_code=400)
    c = await db.get(Claim, claim_id)
    if c is None:
        raise HTTPException(status_code=404)
    old_status = c.status
    c.status = new_status
    if new_status == "settled":
        c.settled_at = datetime.now(UTC)
        if settled_eur is not None:
            c.settled_eur = Decimal(str(settled_eur))
    db.add(
        ClaimTimelineEntry(
            claim_id=c.id,
            author_id=user.id,
            author_name=user.full_name or user.username,
            kind="status",
            body=(note or "") + f" ({old_status} → {new_status})",
        )
    )
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="claims",
        entity_type="claim",
        entity_id=c.id,
        entity_label=c.reference,
        detail=f"{old_status} → {new_status}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/claims/{claim_id}", status_code=303)


@router.post("/{claim_id}/notes")
async def claim_add_note(
    claim_id: int,
    request: Request,
    body: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "M")),
):
    c = await db.get(Claim, claim_id)
    if c is None:
        raise HTTPException(status_code=404)
    db.add(
        ClaimTimelineEntry(
            claim_id=c.id,
            author_id=user.id,
            author_name=user.full_name or user.username,
            kind="note",
            body=body.strip(),
        )
    )
    await db.flush()
    return RedirectResponse(url=f"/claims/{claim_id}", status_code=303)


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )
