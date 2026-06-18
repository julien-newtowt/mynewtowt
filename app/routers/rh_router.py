"""RH — module Ressources Humaines.

ARC (L0 du cahier des charges SIRH) — extraction des routes ``/rh`` hors
de ``modules_router`` vers ce routeur dédié, prélude à la montée en
charge du SIRH (dossier collaborateur, contrats, EVP, self-service…).

État v1-L0 : reprise à l'identique du stub historique — saisie et
validation des **congés de marins** (``CrewLeave`` / ``CrewMember``).
Les écrans SIRH sédentaires (``employees`` & co.) arrivent aux
lots suivants (cf. ``docs/strategy/CAHIER_DES_CHARGES_SIRH.md``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.crew import CrewLeave, CrewMember
from app.permissions import require_permission
from app.templating import templates

router = APIRouter(tags=["rh"])


@router.get("/rh", response_class=HTMLResponse)
async def rh_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "C")),
) -> HTMLResponse:
    members = list(
        (await db.execute(select(CrewMember).where(CrewMember.is_active.is_(True)))).scalars().all()
    )
    leaves = list(
        (await db.execute(select(CrewLeave).order_by(CrewLeave.created_at.desc()).limit(50)))
        .scalars()
        .all()
    )
    pending = [lv for lv in leaves if lv.status == "requested"]
    return templates.TemplateResponse(
        "staff/rh/index.html",
        {
            "request": request,
            "user": user,
            "members": members,
            "leaves": leaves,
            "pending": pending,
        },
    )


@router.post("/rh/leave")
async def rh_create_leave(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    f = await request.form()
    leave = CrewLeave(
        crew_member_id=int(f["crew_member_id"]),
        kind=f["kind"],
        start_date=date.fromisoformat(f["start_date"]),
        end_date=date.fromisoformat(f["end_date"]),
        status="requested",
        reason=f.get("reason") or None,
    )
    db.add(leave)
    await db.flush()
    return RedirectResponse(url="/rh", status_code=303)


@router.post("/rh/leave/{leave_id}/decide")
async def rh_decide_leave(
    leave_id: int,
    decision: str = Form(...),  # 'approved' | 'rejected'
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    leave = await db.get(CrewLeave, leave_id)
    if not leave:
        raise HTTPException(status_code=404, detail="Not found")
    if decision not in ("approved", "rejected"):
        raise HTTPException(
            status_code=400,
            detail=f"decision must be 'approved' or 'rejected', got {decision!r}",
        )
    leave.status = decision
    leave.decided_by_id = user.id
    leave.decided_at = datetime.now(UTC)
    await db.flush()
    return RedirectResponse(url="/rh", status_code=303)
