"""Captain / On board — SOF events, ETA shifts, onboard messages, cargo docs.

Reprises de la V3.0.0 :
- Saisie chronologique d'événements SOF (EOSP, SOSP, NOR, PILOT_ON…).
- Déclaration d'un décalage d'ETA avec motif obligatoire (9 raisons codifiées).
- Messagerie de bord avec @mentions et bot MYTOWT_BOT.
- Génération de cargo documents (NOR, NOR_RT, LOP, Mate's Receipt).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.leg import Leg
from app.models.noon_report import NoonReport
from app.models.sof_event import (
    CargoDocument, ETA_SHIFT_REASONS, EtaShift,
    OnboardMessage, OnboardMessageMention, SOF_EVENT_TYPES, SofEvent,
)
from app.models.user import User
from app.models.vessel import Vessel
from app.models.watch_log import WatchLog
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.signature import (
    compute_noon_hash, compute_sof_hash, compute_watch_hash, sign_record,
)
from app.templating import templates

router = APIRouter(prefix="/captain", tags=["captain"])

MENTION_RE = re.compile(r"@([A-Za-z0-9_]{2,40})")
BOT_TRIGGERS = ("@MYTOWT_BOT", "@mytowt_bot", "@bot")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def captain_index(
    request: Request,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    legs = list((await db.execute(
        select(Leg).order_by(Leg.etd.desc()).limit(20)
    )).scalars().all())
    selected = (await db.get(Leg, leg_id)) if leg_id else (legs[0] if legs else None)
    events: list[SofEvent] = []
    eta_shifts: list[EtaShift] = []
    messages: list[OnboardMessage] = []
    docs: list[CargoDocument] = []
    vessel = None
    if selected:
        events = list((await db.execute(
            select(SofEvent).where(SofEvent.leg_id == selected.id)
            .order_by(SofEvent.occurred_at.desc()).limit(100)
        )).scalars().all())
        eta_shifts = list((await db.execute(
            select(EtaShift).where(EtaShift.leg_id == selected.id)
            .order_by(EtaShift.declared_at.desc())
        )).scalars().all())
        messages = list((await db.execute(
            select(OnboardMessage).where(OnboardMessage.leg_id == selected.id)
            .order_by(OnboardMessage.created_at.desc()).limit(50)
        )).scalars().all())
        docs = list((await db.execute(
            select(CargoDocument).where(CargoDocument.leg_id == selected.id)
            .order_by(CargoDocument.issued_at.desc())
        )).scalars().all())
        vessel = await db.get(Vessel, selected.vessel_id)
    return templates.TemplateResponse(
        "staff/captain/index.html",
        {
            "request": request, "user": user,
            "legs": legs, "leg": selected, "vessel": vessel,
            "events": events, "eta_shifts": eta_shifts,
            "messages": messages, "docs": docs,
            "event_types": SOF_EVENT_TYPES,
            "eta_reasons": ETA_SHIFT_REASONS,
        },
    )


@router.post("/legs/{leg_id}/sof")
async def add_sof_event(
    leg_id: int,
    request: Request,
    event_type: str = Form(...),
    occurred_at: str = Form(...),
    label: str | None = Form(None),
    latitude: float | None = Form(None),
    longitude: float | None = Form(None),
    notes: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
):
    if event_type not in SOF_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="invalid event_type")
    if not await db.get(Leg, leg_id):
        raise HTTPException(status_code=404)
    e = SofEvent(
        leg_id=leg_id,
        event_type=event_type,
        label=label,
        occurred_at=datetime.fromisoformat(occurred_at),
        latitude=latitude, longitude=longitude,
        notes=notes,
        recorded_by_id=user.id,
        recorded_by_name=user.full_name or user.username,
    )
    db.add(e)
    await db.flush()
    await activity_record(
        db, action="create", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="captain", entity_type="sof_event",
        entity_id=e.id, entity_label=f"{event_type}@{occurred_at}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/captain?leg_id={leg_id}", status_code=303)


@router.post("/legs/{leg_id}/eta-shift")
async def declare_eta_shift(
    leg_id: int,
    request: Request,
    new_eta: str = Form(...),
    reason: str = Form(...),
    detail: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
):
    if reason not in ETA_SHIFT_REASONS:
        raise HTTPException(status_code=400, detail="invalid reason")
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    shift = EtaShift(
        leg_id=leg_id,
        previous_eta=leg.eta,
        new_eta=datetime.fromisoformat(new_eta),
        reason=reason, detail=detail,
        declared_by_id=user.id,
        declared_by_name=user.full_name or user.username,
    )
    db.add(shift)
    leg.eta = shift.new_eta
    await db.flush()
    await activity_record(
        db, action="update", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="captain", entity_type="eta_shift",
        entity_id=shift.id, entity_label=f"leg={leg_id} reason={reason}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/captain?leg_id={leg_id}", status_code=303)


@router.post("/legs/{leg_id}/messages")
async def post_onboard_message(
    leg_id: int,
    request: Request,
    body: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
):
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    msg = OnboardMessage(
        leg_id=leg_id, vessel_id=leg.vessel_id,
        author_id=user.id,
        author_name=user.full_name or user.username,
        is_bot=False, body=body.strip(),
    )
    db.add(msg)
    await db.flush()
    # Detect @mentions
    for tag in MENTION_RE.findall(body):
        target_user = (await db.execute(
            select(User).where(User.username == tag)
        )).scalar_one_or_none()
        db.add(OnboardMessageMention(
            message_id=msg.id,
            mentioned_user_id=target_user.id if target_user else None,
            mentioned_text=tag,
        ))
    # Bot reply (placeholder — extended by chat service in Phase 5)
    if any(t.lower() in body.lower() for t in BOT_TRIGGERS):
        db.add(OnboardMessage(
            leg_id=leg_id, vessel_id=leg.vessel_id,
            author_id=None, author_name="MYTOWT_BOT",
            is_bot=True,
            body=f"Bonjour {user.full_name or user.username}, le bot Kairos est en cours d'intégration.",
        ))
    await db.flush()
    return RedirectResponse(url=f"/captain?leg_id={leg_id}", status_code=303)


@router.post("/legs/{leg_id}/docs")
async def create_cargo_document(
    leg_id: int,
    request: Request,
    kind: str = Form(...),
    reference: str | None = Form(None),
    issued_at: str = Form(...),
    party_name: str | None = Form(None),
    body: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
):
    if not await db.get(Leg, leg_id):
        raise HTTPException(status_code=404)
    d = CargoDocument(
        leg_id=leg_id, kind=kind, reference=reference,
        issued_at=datetime.fromisoformat(issued_at),
        party_name=party_name, body=body,
    )
    db.add(d)
    await db.flush()
    await activity_record(
        db, action="create", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="captain", entity_type="cargo_document",
        entity_id=d.id, entity_label=f"{kind} {reference or ''}".strip(),
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/captain?leg_id={leg_id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────
#                  Signature / lock — SOF / noon / watch
# ─────────────────────────────────────────────────────────────────────


@router.post("/sof-events/{event_id}/sign")
async def sign_sof_event(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
):
    """Signe un SOF event → ``is_locked = True``, plus de modification possible."""
    e = await db.get(SofEvent, event_id)
    if e is None:
        raise HTTPException(status_code=404)
    sign_record(e, user, hash_fn=compute_sof_hash)
    await db.flush()
    await activity_record(
        db, action="sign", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="captain", entity_type="sof_event",
        entity_id=e.id, entity_label=f"{e.event_type}@{e.occurred_at.isoformat()}",
        detail=e.signature_hash[:12] if e.signature_hash else None,
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/captain?leg_id={e.leg_id}", status_code=303)


@router.post("/noon-reports/{report_id}/sign")
async def sign_noon_report(
    report_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
):
    n = await db.get(NoonReport, report_id)
    if n is None:
        raise HTTPException(status_code=404)
    sign_record(n, user, hash_fn=compute_noon_hash)
    await db.flush()
    await activity_record(
        db, action="sign", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="captain", entity_type="noon_report",
        entity_id=n.id, entity_label=f"leg={n.leg_id}@{n.recorded_at.isoformat()}",
        detail=n.signature_hash[:12] if n.signature_hash else None,
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/onboard/navigation?leg_id={n.leg_id}", status_code=303)


@router.post("/watch-logs/{log_id}/sign")
async def sign_watch_log(
    log_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
):
    w = await db.get(WatchLog, log_id)
    if w is None:
        raise HTTPException(status_code=404)
    sign_record(w, user, hash_fn=compute_watch_hash)
    await db.flush()
    await activity_record(
        db, action="sign", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="captain", entity_type="watch_log",
        entity_id=w.id, entity_label=f"leg={w.leg_id} {w.watch_date} {w.watch_period}",
        detail=w.signature_hash[:12] if w.signature_hash else None,
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/onboard/navigation?leg_id={w.leg_id}", status_code=303)


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
