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
from app.services.stowage import zone_label, zones_for_leg
from app.templating import templates

router = APIRouter(prefix="/claims", tags=["claims"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def claims_index(
    request: Request,
    status: str | None = None,
    vessel: str | None = None,
    year: int | None = None,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "C")),
) -> HTMLResponse:
    from app.services.leg_filter import build_leg_filter, set_leg_filter_cookie

    flt = await build_leg_filter(db, vessel=vessel, year=year, leg_id=leg_id, request=request)
    stmt = select(Claim).order_by(Claim.declared_at.desc())
    if status:
        stmt = stmt.where(Claim.status == status)
    if leg_id:
        stmt = stmt.where(Claim.leg_id == leg_id)
    claims = list((await db.execute(stmt)).scalars().all())
    counts = dict.fromkeys(CLAIM_STATUSES, 0)
    for c in claims:
        counts[c.status] = counts.get(c.status, 0) + 1
    response = templates.TemplateResponse(
        "staff/claims/index.html",
        {
            "request": request,
            "user": user,
            "leg_filter_ctx": flt,
            "claims": claims,
            "counts": counts,
            "claim_types": CLAIM_TYPES,
            "filter_status": status,
            "statuses": CLAIM_STATUSES,
        },
    )
    set_leg_filter_cookie(response, flt)
    return response


async def _stowage_zones_for(db: AsyncSession, leg_id: int | None) -> list[dict]:
    """Zones du plan d'arrimage d'un leg pour le picker claims — best-effort.

    Toute erreur (pas de plan, etc.) → liste vide : la position cale reste
    saisissable en texte libre. Lecture seule.
    """
    if not leg_id:
        return []
    try:
        return await zones_for_leg(db, leg_id)
    except Exception:
        # best-effort : la position cale reste saisissable en texte libre.
        return []


@router.get("/new", response_class=HTMLResponse)
async def claim_new_form(
    request: Request,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "M")),
):
    from app.services.leg_filter import leg_select_options

    legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()).limit(50))).scalars().all())
    leg_options = await leg_select_options(db)
    # Si un leg est présélectionné (query ?leg_id=), on pré-charge ses zones
    # d'arrimage pour le picker de position cale (claim cargo). Sinon liste
    # vide : l'opérateur choisit d'abord un leg puis ré-ouvre/édite.
    stowage_zones = await _stowage_zones_for(db, leg_id)
    return templates.TemplateResponse(
        "staff/claims/new.html",
        {
            "request": request,
            "user": user,
            "legs": legs,
            "leg_options": leg_options,
            "claim_types": CLAIM_TYPES,
            "selected_leg_id": leg_id,
            "stowage_zones": stowage_zones,
        },
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
    # Position cale : pour un claim cargo lié à un leg, la valeur provient en
    # principe du picker (zones du plan d'arrimage). On normalise et on reste
    # tolérant — une valeur hors plan est conservée en texte libre (cf. mission
    # FLX-10 : le claim n'a pas de lien batch direct, la zone est choisie par
    # l'opérateur dans le plan du leg, pas auto-résolue).
    cargo_position = (cargo_position or "").strip() or None
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
    # Picker position cale : zones du plan d'arrimage du leg pour un claim
    # cargo (best-effort → liste vide si pas de plan / autre type).
    stowage_zones = (
        await _stowage_zones_for(db, claim.leg_id) if claim.claim_type == "cargo" else []
    )
    # Indice humain de la zone (partie après "—" du label), ou "" si la
    # position est du texte libre non conforme à la convention de nommage.
    full_label = zone_label(claim.cargo_position)
    cargo_position_hint = (
        full_label.split("—", 1)[1].strip() if claim.cargo_position and "—" in full_label else ""
    )
    return templates.TemplateResponse(
        "staff/claims/detail.html",
        {
            "request": request,
            "user": user,
            "claim": claim,
            "leg": leg,
            "booking": booking,
            "statuses": CLAIM_STATUSES,
            "stowage_zones": stowage_zones,
            "cargo_position_hint": cargo_position_hint,
        },
    )


@router.post("/{claim_id}/cargo-position")
async def claim_update_cargo_position(
    claim_id: int,
    request: Request,
    cargo_position: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "M")),
):
    """Remonte / met à jour la position cale (zone d'arrimage) d'un claim cargo.

    La valeur vient du picker (zones du plan d'arrimage du leg). Tolérant :
    une valeur hors plan est conservée en texte libre (cf. FLX-10 — pas de
    lien batch direct, la zone est choisie par l'opérateur). ``flush`` only.
    """
    c = await db.get(Claim, claim_id)
    if c is None:
        raise HTTPException(status_code=404)
    new_position = (cargo_position or "").strip() or None
    old_position = c.cargo_position
    if new_position == old_position:
        return RedirectResponse(url=f"/claims/{claim_id}", status_code=303)
    c.cargo_position = new_position
    db.add(
        ClaimTimelineEntry(
            claim_id=c.id,
            author_id=user.id,
            author_name=user.full_name or user.username,
            kind="note",
            body=f"Position cale : {old_position or '—'} → {new_position or '—'}",
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
        detail=f"cargo_position {old_position or '—'} → {new_position or '—'}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/claims/{claim_id}", status_code=303)


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
