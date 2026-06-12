"""Crew — équipage, assignments, calendrier, compliance Schengen, billets.

Reprise des écrans riches de la V3.0.0 :
- Liste avec stats (total/active/en_repos).
- Bordée par navire (qui est embarqué où, postes vacants).
- Page compliance Schengen (alertes <30 j, passport/visa expirants).
- Assignments embarquement/débarquement.
- Billets transport (uploads).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from datetime import date as _date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.crew import (
    CrewAssignment,
    CrewCertification,
    CrewLeave,
    CrewMember,
)
from app.models.crew_ticket import TRANSPORT_MODES, CrewTicket
from app.models.leg import Leg
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.crew_compliance import (
    REQUIRED_ROLES,
    ROLE_LABELS,
    normalize_role,
    passport_blocking_reason,
    refresh_member_schengen,
    refresh_schengen_for_members,
    vessel_readiness,
)
from app.templating import templates

router = APIRouter(prefix="/crew", tags=["crew"])


CREW_ROLES = (
    "capitaine",
    "second",
    "chef_mecanicien",
    "cook",
    "lieutenant",
    "bosco",
    "marin",
    "eleve_officier",
)
# REQUIRED_ROLES (armement réglementaire) : source unique dans
# services.crew_compliance — importé ci-dessus (FLX-06).


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def crew_index(
    request: Request,
    role: str | None = None,
    vessel: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "C")),
) -> HTMLResponse:
    stmt = select(CrewMember).where(CrewMember.is_active.is_(True))
    if role:
        stmt = stmt.where(CrewMember.role == role)
    members = list((await db.execute(stmt.order_by(CrewMember.full_name))).scalars().all())

    # FLX-06 : snapshot persisté — recalcule le statut Schengen et l'écrit
    # sur les lignes CrewMember (status / days / window_end) + flush, pour
    # que le statut affiché soit persisté et historisable.
    await refresh_schengen_for_members(db, members)

    total = len(members)

    # Bordée par navire (assignments actifs)
    now = datetime.now(UTC)
    active_assigns = list(
        (
            await db.execute(
                select(CrewAssignment)
                .where(CrewAssignment.embark_at.is_not(None))
                .where(
                    (CrewAssignment.disembark_at.is_(None)) | (CrewAssignment.disembark_at > now)
                )
            )
        )
        .scalars()
        .all()
    )
    legs_index = {}
    for a in active_assigns:
        leg = await db.get(Leg, a.leg_id)
        if leg:
            legs_index[a.id] = leg
    # Group by vessel
    bordees: dict[str, dict] = defaultdict(lambda: {"crew": [], "missing": []})
    member_by_id = {m.id: m for m in members}
    for a in active_assigns:
        leg = legs_index.get(a.id)
        if not leg:
            continue
        vsl = await db.get(Vessel, leg.vessel_id) if leg.vessel_id else None
        vname = vsl.name if vsl else "—"
        m = member_by_id.get(a.crew_member_id)
        if m is None:
            continue
        bordees[vname]["crew"].append(m)
    for info in bordees.values():
        roles_present = {normalize_role(m.role) for m in info["crew"]}
        info["missing"] = [ROLE_LABELS.get(r, r) for r in REQUIRED_ROLES if r not in roles_present]

    # Compliance alerts (passport / visa within 30 days)
    soon = _date.today() + timedelta(days=30)
    compliance_alerts = [
        m
        for m in members
        if (m.passport_expires_at and m.passport_expires_at <= soon)
        or (m.visa_us_expires_at and m.visa_us_expires_at <= soon)
        or (m.visa_br_expires_at and m.visa_br_expires_at <= soon)
        or m.schengen_status == "warning"
        or m.schengen_status == "non_compliant"
    ]

    stats = {
        "total": total,
        "active": sum(1 for a in active_assigns),
        "repos": total - sum(1 for a in active_assigns),
    }
    return templates.TemplateResponse(
        "staff/crew/index.html",
        {
            "request": request,
            "user": user,
            "members": members,
            "roles": CREW_ROLES,
            "selected_role": role,
            "bordees": dict(bordees),
            "compliance_alerts": compliance_alerts,
            "stats": stats,
        },
    )


@router.get("/compliance", response_class=HTMLResponse)
async def crew_compliance(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "C")),
) -> HTMLResponse:
    members = list(
        (
            await db.execute(
                select(CrewMember)
                .where(CrewMember.is_active.is_(True))
                .order_by(CrewMember.full_name)
            )
        )
        .scalars()
        .all()
    )
    today = _date.today()

    # FLX-06 : snapshot persisté — recalcule + écrit le statut Schengen
    # sur les lignes CrewMember avant affichage (flush dans le service).
    await refresh_schengen_for_members(db, members, today=today)

    # Armement réglementaire par navire (lecture seule, V1).
    vessels = list(
        (await db.execute(select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.name)))
        .scalars()
        .all()
    )
    readiness = []
    for v in vessels:
        r = await vessel_readiness(db, v.id, today)
        r["vessel_name"] = v.name
        readiness.append(r)

    rows = []
    for m in members:
        warnings: list[str] = []
        if m.passport_expires_at:
            days = (m.passport_expires_at - today).days
            if days <= 30:
                warnings.append(f"Passeport expire dans {days} j")
        if m.visa_us_expires_at:
            days = (m.visa_us_expires_at - today).days
            if days <= 30:
                warnings.append(f"Visa US expire dans {days} j")
        if m.visa_br_expires_at:
            days = (m.visa_br_expires_at - today).days
            if days <= 30:
                warnings.append(f"Visa BR expire dans {days} j")
        if m.seaman_book_expires_at:
            days = (m.seaman_book_expires_at - today).days
            if days <= 30:
                warnings.append(f"Seaman book expire dans {days} j")
        if m.schengen_status != "compliant":
            warnings.append(
                f"Schengen {m.schengen_status}"
                + (f" ({m.schengen_days_in_window} j)" if m.schengen_days_in_window else "")
            )
        rows.append({"member": m, "warnings": warnings})
    return templates.TemplateResponse(
        "staff/crew/compliance.html",
        {"request": request, "user": user, "rows": rows, "readiness": readiness},
    )


@router.get("/calendar", response_class=HTMLResponse)
async def crew_calendar(
    request: Request,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "C")),
) -> HTMLResponse:
    """Heatmap annuelle : jours embarqués vs au repos par membre."""
    y = year or _date.today().year
    start = _date(y, 1, 1)
    end = _date(y, 12, 31)
    members = list(
        (
            await db.execute(
                select(CrewMember)
                .where(CrewMember.is_active.is_(True))
                .order_by(CrewMember.full_name)
            )
        )
        .scalars()
        .all()
    )
    assigns = list((await db.execute(select(CrewAssignment))).scalars().all())
    # Build per-member day map
    days_by_member: dict[int, set[_date]] = defaultdict(set)
    for a in assigns:
        if not a.embark_at:
            continue
        d0 = a.embark_at.date()
        d1 = (a.disembark_at or datetime.now(UTC)).date()
        cur = max(d0, start)
        last = min(d1, end)
        while cur <= last:
            days_by_member[a.crew_member_id].add(cur)
            cur += timedelta(days=1)
    return templates.TemplateResponse(
        "staff/crew/calendar.html",
        {
            "request": request,
            "user": user,
            "year": y,
            "members": members,
            "days_by_member": {m.id: sorted(days_by_member.get(m.id, ())) for m in members},
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def crew_new_form(
    request: Request,
    user=Depends(require_permission("crew", "M")),
):
    return templates.TemplateResponse(
        "staff/crew/new.html",
        {"request": request, "user": user, "roles": CREW_ROLES, "member": None},
    )


@router.post("/members")
async def crew_create(
    request: Request,
    full_name: str = Form(...),
    role: str = Form(...),
    nationality: str | None = Form(None),
    passport_number: str | None = Form(None),
    passport_expires_at: str | None = Form(None),
    email: str | None = Form(None),
    phone: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "M")),
):
    if role not in CREW_ROLES:
        raise HTTPException(status_code=400, detail="invalid role")
    m = CrewMember(
        full_name=full_name.strip(),
        role=role,
        nationality=(nationality or "").strip().upper()[:2] or None,
        passport_number=(passport_number or "").strip() or None,
        passport_expires_at=(
            _date.fromisoformat(passport_expires_at) if passport_expires_at else None
        ),
        email=(email or "").strip() or None,
        phone=(phone or "").strip() or None,
    )
    db.add(m)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="crew",
        entity_type="crew_member",
        entity_id=m.id,
        entity_label=m.full_name,
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/crew", status_code=303)


async def _member_detail_context(
    request: Request,
    db: AsyncSession,
    user,
    member: CrewMember,
    *,
    error: str | None = None,
) -> dict:
    """Contexte commun de la fiche marin (GET + re-render erreur POST)."""
    member_id = member.id
    assigns = list(
        (
            await db.execute(
                select(CrewAssignment)
                .where(CrewAssignment.crew_member_id == member_id)
                .order_by(CrewAssignment.embark_at.desc())
            )
        )
        .scalars()
        .all()
    )
    certs = list(
        (
            await db.execute(
                select(CrewCertification).where(CrewCertification.crew_member_id == member_id)
            )
        )
        .scalars()
        .all()
    )
    leaves = list(
        (
            await db.execute(
                select(CrewLeave)
                .where(CrewLeave.crew_member_id == member_id)
                .order_by(CrewLeave.start_date.desc())
            )
        )
        .scalars()
        .all()
    )
    tickets = list(
        (
            await db.execute(
                select(CrewTicket)
                .where(CrewTicket.crew_member_id == member_id)
                .order_by(CrewTicket.departure_at.desc())
            )
        )
        .scalars()
        .all()
    )
    legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()))).scalars().all())
    return {
        "request": request,
        "user": user,
        "member": member,
        "assignments": assigns,
        "certifications": certs,
        "leaves": leaves,
        "tickets": tickets,
        "legs": legs,
        "roles": CREW_ROLES,
        "error": error,
    }


@router.get("/members/{member_id}", response_class=HTMLResponse)
async def crew_detail(
    member_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "C")),
) -> HTMLResponse:
    m = await db.get(CrewMember, member_id)
    if m is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "staff/crew/detail.html",
        await _member_detail_context(request, db, user, m),
    )


@router.post("/members/{member_id}/assignments")
async def crew_assign(
    member_id: int,
    request: Request,
    leg_id: int = Form(...),
    role_on_board: str | None = Form(None),
    embark_at: str | None = Form(None),
    disembark_at: str | None = Form(None),
    override_compliance: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "M")),
):
    member = await db.get(CrewMember, member_id)
    leg = await db.get(Leg, leg_id)
    if member is None or leg is None:
        raise HTTPException(status_code=404)

    try:
        embark_dt = datetime.fromisoformat(embark_at) if embark_at else None
        disembark_dt = datetime.fromisoformat(disembark_at) if disembark_at else None
    except ValueError:
        return templates.TemplateResponse(
            "staff/crew/detail.html",
            await _member_detail_context(
                request, db, user, member, error="Dates d'embarquement/débarquement invalides."
            ),
            status_code=400,
        )

    # FLX-06 — garde-fou conformité AVANT création de l'embarquement :
    # rafraîchit le snapshot Schengen persisté puis vérifie statut +
    # validité passeport jusqu'à la fin d'embarquement prévue.
    await refresh_member_schengen(db, member)

    blocking: list[str] = []
    if member.schengen_status == "non_compliant":
        days = member.schengen_days_in_window
        blocking.append(
            "Statut Schengen non conforme"
            + (f" ({days} j sur la fenêtre de 180 j, max 90 j)" if days is not None else "")
            + "."
        )
    deadline = (
        disembark_dt.date() if disembark_dt else embark_dt.date() if embark_dt else _date.today()
    )
    passport_reason = passport_blocking_reason(member, deadline)
    if passport_reason:
        blocking.append(passport_reason)

    override = override_compliance == "on"
    if blocking and not override:
        return templates.TemplateResponse(
            "staff/crew/detail.html",
            await _member_detail_context(
                request,
                db,
                user,
                member,
                error=(
                    "Embarquement bloqué : "
                    + " ".join(blocking)
                    + " Cochez « Forcer malgré la non-conformité » pour passer outre "
                    "(action tracée dans le journal d'audit)."
                ),
            ),
            status_code=400,
        )

    a = CrewAssignment(
        crew_member_id=member_id,
        leg_id=leg_id,
        role_on_board=(role_on_board or "").strip() or None,
        embark_at=embark_dt,
        disembark_at=disembark_dt,
    )
    db.add(a)
    await db.flush()

    overridden = bool(blocking and override)
    await activity_record(
        db,
        action="crew_assignment_override" if overridden else "create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="crew",
        entity_type="crew_assignment",
        entity_id=a.id,
        entity_label=f"member={member_id} leg={leg_id}",
        detail=(
            f"OVERRIDE compliance — motifs ignorés : {' '.join(blocking)}" if overridden else None
        ),
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/crew/members/{member_id}", status_code=303)


@router.post("/members/{member_id}/tickets")
async def crew_ticket_create(
    member_id: int,
    request: Request,
    mode: str = Form(...),
    reference: str | None = Form(None),
    carrier: str | None = Form(None),
    departure_at: str | None = Form(None),
    arrival_at: str | None = Form(None),
    departure_location: str | None = Form(None),
    arrival_location: str | None = Form(None),
    cost_eur: float | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "M")),
):
    if mode not in TRANSPORT_MODES:
        raise HTTPException(status_code=400, detail="invalid mode")
    if not await db.get(CrewMember, member_id):
        raise HTTPException(status_code=404)
    t = CrewTicket(
        crew_member_id=member_id,
        mode=mode,
        reference=reference,
        carrier=carrier,
        departure_at=datetime.fromisoformat(departure_at) if departure_at else None,
        arrival_at=datetime.fromisoformat(arrival_at) if arrival_at else None,
        departure_location=departure_location,
        arrival_location=arrival_location,
        cost_eur=cost_eur,
    )
    db.add(t)
    await db.flush()
    return RedirectResponse(url=f"/crew/members/{member_id}", status_code=303)


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )
