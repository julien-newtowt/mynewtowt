"""Packing list — vue staff interne (token-based portal = cargo_portal_router).

Reprise de la V3.0.0. Workflow draft → submitted → locked. Audit trail
field-by-field. Verrouillage par un staff après validation côté armateur.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.booking import Booking
from app.models.commercial import Order
from app.models.packing_list import (
    PackingList,
    PackingListAudit,
    PackingListBatch,
    PortalMessage,
)
from app.permissions import require_permission
from app.services import cargo_excel
from app.services.activity import record as activity_record
from app.services.packing_list import (
    apply_batch_update,
    assign_bl_number,
    can_modify,
    coerce_batch_form,
    create_batch,
    lock,
    record_audit,
    resolve_pl_context,
    unlock,
)
from app.services.pdf_generator import (
    render_arrival_notice,
    render_bill_of_lading_from_pl,
)
from app.services.safe_files import content_length_exceeds_max
from app.templating import templates
from app.utils.file_validation import validate_size

router = APIRouter(prefix="/cargo/packing-lists", tags=["cargo-packing"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def packing_lists_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    pls = list(
        (
            await db.execute(
                select(PackingList)
                .options(selectinload(PackingList.batches))
                .order_by(PackingList.updated_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    from app.services import messaging

    unread = await messaging.portal_unread_counts(db, [pl.id for pl in pls], reader="staff")
    return templates.TemplateResponse(
        "staff/cargo/packing_lists.html",
        {"request": request, "user": user, "packing_lists": pls, "unread": unread},
    )


@router.post("/from-order/{order_id}")
async def create_for_order(
    order_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    from app.services.packing_list import ensure_for_order

    order = await db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404)
    pl, created = await ensure_for_order(db, order)
    if not created:
        return RedirectResponse(url=f"/cargo/packing-lists/{pl.id}", status_code=303)
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="cargo",
        entity_type="packing_list",
        entity_id=pl.id,
        entity_label=f"PL for {order.reference}",
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
    pl = (
        await db.execute(
            select(PackingList)
            .options(selectinload(PackingList.batches))
            .where(PackingList.id == pl_id)
        )
    ).scalar_one_or_none()
    if pl is None:
        raise HTTPException(status_code=404)
    order = await db.get(Order, pl.order_id)
    messages = list(
        (
            await db.execute(
                select(PortalMessage)
                .where(PortalMessage.packing_list_id == pl_id)
                .order_by(PortalMessage.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    # CARGO-14 — la consultation staff marque lus les messages du client.
    from app.services import messaging

    await messaging.mark_portal_read(db, pl_id, reader="staff")
    return templates.TemplateResponse(
        "staff/cargo/packing_list_detail.html",
        {"request": request, "user": user, "pl": pl, "order": order, "messages": messages},
    )


@router.post("/{pl_id}/batches")
async def add_batch(
    pl_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    """Ajout d'un batch (tous champs : marchandise + adresses BL — CARGO-02)."""
    pl = await db.get(PackingList, pl_id)
    if pl is None or not can_modify(pl):
        raise HTTPException(status_code=409, detail="packing list verrouillée")
    vals = {k: v for k, v in coerce_batch_form(dict(await request.form())).items() if v is not None}
    await create_batch(
        db, pl=pl, vals=vals, actor="staff", actor_name=user.full_name or user.username
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
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="cargo",
        entity_type="packing_list",
        entity_id=pl.id,
        entity_label=str(pl.id),
        detail="locked",
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
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="cargo",
        entity_type="packing_list",
        entity_id=pl.id,
        entity_label=str(pl.id),
        detail="unlocked",
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
    db.add(
        PortalMessage(
            packing_list_id=pl.id,
            sender="staff",
            sender_name=user.full_name or user.username,
            body=body.strip(),
        )
    )
    await db.flush()
    return RedirectResponse(url=f"/cargo/packing-lists/{pl_id}", status_code=303)


async def _get_batch_or_404(db: AsyncSession, pl_id: int, batch_id: int) -> PackingListBatch:
    b = await db.get(PackingListBatch, batch_id)
    if b is None or b.packing_list_id != pl_id:
        raise HTTPException(status_code=404)
    return b


@router.post("/{pl_id}/batches/{batch_id}/edit")
async def edit_batch(
    pl_id: int,
    batch_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    """CARGO-03 — édition d'un batch (audit field-by-field)."""
    pl = await db.get(PackingList, pl_id)
    if pl is None or not can_modify(pl):
        raise HTTPException(status_code=409, detail="packing list verrouillée")
    batch = await _get_batch_or_404(db, pl_id, batch_id)
    new_values = coerce_batch_form(dict(await request.form()))
    await apply_batch_update(
        db,
        batch=batch,
        new_values=new_values,
        actor="staff",
        actor_name=user.full_name or user.username,
    )
    return RedirectResponse(url=f"/cargo/packing-lists/{pl_id}", status_code=303)


@router.post("/{pl_id}/batches/{batch_id}/delete")
async def delete_batch(
    pl_id: int,
    batch_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "S")),
):
    """CARGO-03 — suppression d'un batch (interdite si PL verrouillée)."""
    pl = await db.get(PackingList, pl_id)
    if pl is None or not can_modify(pl):
        raise HTTPException(status_code=409, detail="packing list verrouillée")
    batch = await _get_batch_or_404(db, pl_id, batch_id)
    await record_audit(
        db,
        packing_list_id=pl_id,
        batch_id=batch_id,
        actor="staff",
        actor_name=user.full_name or user.username,
        field="_delete_batch",
        old_value=f"{batch.pallet_count}×{batch.pallet_format}",
        new_value=None,
    )
    await db.delete(batch)
    await db.flush()
    return RedirectResponse(url=f"/cargo/packing-lists/{pl_id}", status_code=303)


@router.get("/{pl_id}/history", response_class=HTMLResponse)
async def packing_list_history(
    pl_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    """CARGO-04 — vue de l'audit trail field-by-field de la packing list."""
    pl = await db.get(PackingList, pl_id)
    if pl is None:
        raise HTTPException(status_code=404)
    entries = list(
        (
            await db.execute(
                select(PackingListAudit)
                .where(PackingListAudit.packing_list_id == pl_id)
                .order_by(PackingListAudit.at.desc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        "staff/cargo/packing_list_history.html",
        {"request": request, "user": user, "pl": pl, "entries": entries},
    )


@router.get("/{pl_id}/batches/{batch_id}/bl.pdf")
async def batch_bill_of_lading(
    pl_id: int,
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> Response:
    """CARGO-01 — Bill of Lading d'un batch (numéro persistant, anti-doublon)."""
    pl = await db.get(PackingList, pl_id)
    if pl is None:
        raise HTTPException(status_code=404)
    batch = await _get_batch_or_404(db, pl_id, batch_id)
    _order, _booking, leg, vessel, pol, pod = await resolve_pl_context(db, pl)
    bl_number = await assign_bl_number(db, pl, batch, leg)
    doc = render_bill_of_lading_from_pl(
        pl=pl,
        batch=batch,
        leg=leg,
        vessel=vessel,
        pol=pol,
        pod=pod,
        bl_number=bl_number,
        issued_at=batch.bl_issued_at,
    )
    return Response(
        content=doc.pdf,
        media_type=doc.mime,
        headers={"Content-Disposition": f'inline; filename="{doc.filename}"'},
    )


@router.get("/{pl_id}/arrival-notice.pdf")
async def packing_list_arrival_notice(
    pl_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> Response:
    """CARGO-05 — Avis d'arrivée (Arrival Notice) de la packing list."""
    pl = (
        await db.execute(
            select(PackingList)
            .options(selectinload(PackingList.batches))
            .where(PackingList.id == pl_id)
        )
    ).scalar_one_or_none()
    if pl is None:
        raise HTTPException(status_code=404)
    _order, _booking, leg, vessel, pol, pod = await resolve_pl_context(db, pl)
    doc = render_arrival_notice(
        pl=pl, batches=list(pl.batches), leg=leg, vessel=vessel, pol=pol, pod=pod
    )
    return Response(
        content=doc.pdf,
        media_type=doc.mime,
        headers={"Content-Disposition": f'inline; filename="{doc.filename}"'},
    )


def _xlsx_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type=cargo_excel.XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _pl_excel_context(db: AsyncSession, pl: PackingList) -> dict:
    """Colonnes de contexte (voyage / navire / POL / POD) d'une packing list."""
    _order, _booking, leg, vessel, pol, pod = await resolve_pl_context(db, pl)
    return {
        "voyage_id": leg.leg_code if leg else None,
        "vessel": vessel.name if vessel else None,
        "pol_code": pol.locode if pol else None,
        "pod_code": pod.locode if pod else None,
    }


@router.get("/{pl_id}/template.xlsx")
async def packing_list_template_xlsx(
    pl_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> Response:
    """CARGO-09 — template Excel vide (en-têtes) pour saisie de masse."""
    pl = await db.get(PackingList, pl_id)
    if pl is None:
        raise HTTPException(status_code=404)
    return _xlsx_response(cargo_excel.build_template_xlsx(), f"packing_list_{pl_id}_template.xlsx")


@router.get("/{pl_id}/export.xlsx")
async def packing_list_export_xlsx(
    pl_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> Response:
    """CARGO-09 — export Excel des batches d'une packing list."""
    pl = (
        await db.execute(
            select(PackingList)
            .options(selectinload(PackingList.batches))
            .where(PackingList.id == pl_id)
        )
    ).scalar_one_or_none()
    if pl is None:
        raise HTTPException(status_code=404)
    ctx = await _pl_excel_context(db, pl)
    content = cargo_excel.export_packing_list_xlsx(list(pl.batches), **ctx)
    return _xlsx_response(content, f"packing_list_{pl_id}.xlsx")


@router.get("/voyage/{leg_id}/export.xlsx")
async def voyage_export_xlsx(
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> Response:
    """CARGO-09 — export Excel de toutes les packing lists d'un voyage (leg)."""
    pls = list(
        (
            await db.execute(
                select(PackingList)
                .options(selectinload(PackingList.batches))
                .outerjoin(Order, PackingList.order_id == Order.id)
                .outerjoin(Booking, PackingList.booking_id == Booking.id)
                # COM-11 — leg épinglé prioritaire (repli order/booking pour les PL
                # héritées), cohérent avec resolve_pl_context / la numérotation BL.
                .where(func.coalesce(PackingList.leg_id, Order.leg_id, Booking.leg_id) == leg_id)
                .order_by(PackingList.id)
            )
        )
        .scalars()
        .all()
    )
    rows: list[tuple] = []
    for pl in pls:
        ctx = await _pl_excel_context(db, pl)
        rows.extend((b, cargo_excel.batch_context(b, **ctx)) for b in pl.batches)
    return _xlsx_response(cargo_excel.export_rows_xlsx(rows), f"voyage_{leg_id}_packing.xlsx")


@router.post("/{pl_id}/import-xlsx")
async def packing_list_import_xlsx(
    pl_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    """CARGO-09 — import Excel : remplace les batches (refusé si PL verrouillée)."""
    if content_length_exceeds_max(request.headers.get("content-length")):
        raise HTTPException(status_code=413, detail="fichier trop volumineux")
    pl = (
        await db.execute(
            select(PackingList)
            .options(selectinload(PackingList.batches))
            .where(PackingList.id == pl_id)
        )
    ).scalar_one_or_none()
    if pl is None or not can_modify(pl):
        raise HTTPException(status_code=409, detail="packing list verrouillée")
    content = await file.read()
    # Le header Content-Length est falsifiable (et absent en transfert chunké) :
    # on revérifie la taille réelle après lecture (anti zip-bomb / OOM).
    size_check = validate_size(content)
    if not size_check.ok:
        raise HTTPException(status_code=413, detail=size_check.reason)
    try:
        parsed = cargo_excel.parse_xlsx(content)
    except Exception as exc:  # classeur illisible / corrompu
        raise HTTPException(status_code=400, detail="fichier Excel illisible") from exc
    if not parsed:
        raise HTTPException(status_code=400, detail="aucune ligne exploitable dans le fichier")
    actor_name = user.full_name or user.username
    # Remplacement : on vide les batches existants puis on recrée depuis l'import.
    for b in list(pl.batches):
        await db.delete(b)
    await db.flush()
    for vals in parsed:
        await create_batch(db, pl=pl, vals=vals, actor="staff", actor_name=actor_name)
    await record_audit(
        db,
        packing_list_id=pl.id,
        batch_id=None,
        actor="staff",
        actor_name=actor_name,
        field="_import_excel",
        old_value=None,
        new_value=f"{len(parsed)} batches importés",
    )
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=actor_name,
        user_role=user.role,
        module="cargo",
        entity_type="packing_list",
        entity_id=pl.id,
        entity_label=str(pl.id),
        detail=f"import Excel ({len(parsed)} batches)",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/cargo/packing-lists/{pl_id}", status_code=303)


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )
