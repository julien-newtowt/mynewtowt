"""Onboard cashbox routes — one cashbox per vessel, multi-currency.

Encaissement (income) / décaissement (expense) avec catégories distinctes,
pièces justificatives (scan), export comptable et clôture mensuelle qui
verrouille les mouvements de la période.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.onboard_cashbox import (
    CATEGORY_KIND,
    CATEGORY_LABELS,
    CURRENCY_LABELS,
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    SUPPORTED_CURRENCIES,
    CashboxMovement,
    categories_for,
)
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services import safe_files
from app.services.activity import record as activity_record
from app.services.cashbox import (
    CashboxError,
    PeriodClosed,
    add_movement,
    balances,
    close_month,
    export_csv,
    get_or_create,
    list_closures,
    period_movements,
    recent_movements,
)
from app.templating import templates

router = APIRouter(prefix="/cashbox", tags=["cashbox"])

_RECEIPT_SUBDIR = "cashbox/receipts"
_EXPORT_SUBDIR = "cashbox/exports"


@router.get("", response_class=HTMLResponse)
async def cashbox_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    summary = []
    for v in vessels:
        cb = await get_or_create(db, v.id)
        bal = await balances(db, cb)
        summary.append({"vessel": v, "cashbox": cb, "balances": bal})
    return templates.TemplateResponse(
        "staff/cashbox/index.html",
        {
            "request": request,
            "user": user,
            "summary": summary,
            "currency_labels": CURRENCY_LABELS,
        },
    )


@router.get("/{vessel_id}", response_class=HTMLResponse)
async def cashbox_detail(
    request: Request,
    vessel_id: int,
    currency: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    vessel = await db.get(Vessel, vessel_id)
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    cb = await get_or_create(db, vessel_id)
    bal = await balances(db, cb)
    mvts = await recent_movements(db, cb, currency=currency, limit=200)
    closures = await list_closures(db, cb, limit=24)
    now = datetime.now(UTC)
    return templates.TemplateResponse(
        "staff/cashbox/detail.html",
        {
            "request": request,
            "user": user,
            "vessel": vessel,
            "cashbox": cb,
            "balances": bal,
            "movements": mvts,
            "closures": closures,
            "currency_filter": currency,
            "currencies": SUPPORTED_CURRENCIES,
            "currency_labels": CURRENCY_LABELS,
            "income_categories": INCOME_CATEGORIES,
            "expense_categories": EXPENSE_CATEGORIES,
            "category_labels": CATEGORY_LABELS,
            "category_kind": CATEGORY_KIND,
            "default_year": now.year,
            "default_month": now.month,
        },
    )


@router.post("/{vessel_id}/movement")
async def add_mov(
    request: Request,
    vessel_id: int,
    amount: str = Form(...),
    currency: str = Form(...),
    category: str = Form(...),
    description: str = Form(...),
    movement_kind: str = Form("expense"),  # "income" | "expense"
    occurred_at: str = Form(""),
    receipt: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    cb = await get_or_create(db, vessel_id)
    if movement_kind not in ("income", "expense"):
        raise HTTPException(status_code=400, detail="Invalid movement kind")
    if category not in categories_for(movement_kind):
        raise HTTPException(
            status_code=400, detail="Catégorie incompatible avec le sens du mouvement"
        )
    try:
        amt = abs(Decimal(amount.replace(",", ".")))
    except (InvalidOperation, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid amount") from None
    if movement_kind == "expense":
        amt = -amt
    occ = None
    if occurred_at:
        try:
            occ = datetime.fromisoformat(occurred_at.replace("T", " "))
            if occ.tzinfo is None:
                occ = occ.replace(tzinfo=UTC)
        except ValueError:
            pass

    receipt_url, receipt_mime = await _maybe_store_receipt(receipt)

    try:
        mov = await add_movement(
            db,
            cb,
            amount=amt,
            currency=currency,
            category=category,
            description=description,
            occurred_at=occ,
            recorded_by_id=user.id,
            receipt_url=receipt_url,
            receipt_mime=receipt_mime,
        )
    except PeriodClosed as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except CashboxError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await activity_record(
        db,
        action="cashbox_movement",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="cashbox_movement",
        entity_id=mov.id,
        detail=f"vessel={vessel_id} {amt} {currency} {category}",
    )
    return RedirectResponse(url=f"/cashbox/{vessel_id}", status_code=303)


@router.post("/{vessel_id}/movement/{mov_id}/receipt")
async def attach_receipt(
    vessel_id: int,
    mov_id: int,
    receipt: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    mov = await _get_movement(db, vessel_id, mov_id)
    if mov.is_locked:
        raise HTTPException(status_code=400, detail="Mouvement verrouillé (clôturé)")
    rel, mime = await _maybe_store_receipt(receipt)
    if rel is None:
        raise HTTPException(status_code=400, detail="Aucun fichier valide")
    mov.receipt_url = rel
    mov.receipt_mime = mime
    await db.flush()
    await activity_record(
        db,
        action="cashbox_receipt",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="cashbox_movement",
        entity_id=mov.id,
        detail=f"vessel={vessel_id} justificatif",
    )
    return RedirectResponse(url=f"/cashbox/{vessel_id}", status_code=303)


@router.get("/{vessel_id}/movement/{mov_id}/receipt")
async def view_receipt(
    vessel_id: int,
    mov_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> Response:
    mov = await _get_movement(db, vessel_id, mov_id)
    if not mov.receipt_url:
        raise HTTPException(status_code=404, detail="Pas de justificatif")
    try:
        path = safe_files.resolve_path(mov.receipt_url)
    except (safe_files.UploadRejected, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail="Fichier introuvable") from e
    return Response(
        content=path.read_bytes(),
        media_type=mov.receipt_mime or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="justificatif-{mov_id}{path.suffix}"'},
    )


@router.get("/{vessel_id}/export.csv")
async def export_period(
    vessel_id: int,
    year: int,
    month: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> Response:
    vessel = await db.get(Vessel, vessel_id)
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    cb = await get_or_create(db, vessel_id)
    movs = await period_movements(db, cb, year=year, month=month)
    csv_text = export_csv(movs, vessel_code=vessel.code, period=f"{year}-{month:02d}")
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="caisse-{vessel.code}-{year}{month:02d}.csv"'
            )
        },
    )


@router.post("/{vessel_id}/close")
async def close_period(
    request: Request,
    vessel_id: int,
    year: int = Form(...),
    month: int = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    vessel = await db.get(Vessel, vessel_id)
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    cb = await get_or_create(db, vessel_id)

    # Soldes comptés saisis par devise : champ counted_<CUR>.
    form = await request.form()
    counted: dict[str, Decimal] = {}
    for cur in SUPPORTED_CURRENCIES:
        raw = (form.get(f"counted_{cur}") or "").strip().replace(",", ".")
        if raw:
            with contextlib.suppress(InvalidOperation):
                counted[cur] = Decimal(raw)

    # Export comptable d'abord (les données sont exportées puis verrouillées).
    movs = await period_movements(db, cb, year=year, month=month)
    csv_text = export_csv(movs, vessel_code=vessel.code, period=f"{year}-{month:02d}")
    export_path = None
    try:
        export_path, _mime = safe_files.save_upload(
            csv_text.encode("utf-8"),
            f"caisse-{vessel.code}-{year}{month:02d}.csv",
            subdir=_EXPORT_SUBDIR,
        )
    except safe_files.UploadRejected:
        export_path = None  # export best-effort ; la clôture verrouille quoi qu'il arrive

    try:
        closures = await close_month(
            db,
            cb,
            year=year,
            month=month,
            counted=counted,
            closed_by_id=user.id,
            export_path=export_path,
        )
    except CashboxError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    await activity_record(
        db,
        action="cashbox_close",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="captain",
        entity_type="cashbox_closure",
        entity_id=closures[0].id if closures else None,
        detail=f"vessel={vessel_id} {year}-{month:02d} devises={[c.currency for c in closures]}",
    )
    return RedirectResponse(url=f"/cashbox/{vessel_id}", status_code=303)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_movement(db: AsyncSession, vessel_id: int, mov_id: int) -> CashboxMovement:
    cb = await get_or_create(db, vessel_id)
    mov = await db.get(CashboxMovement, mov_id)
    if mov is None or mov.cashbox_id != cb.id:
        raise HTTPException(status_code=404, detail="Mouvement introuvable")
    return mov


async def _maybe_store_receipt(
    receipt: UploadFile | None,
) -> tuple[str | None, str | None]:
    """Valide + enregistre un justificatif uploadé. (None, None) si absent."""
    if receipt is None or not receipt.filename:
        return None, None
    content = await receipt.read()
    if not content:
        return None, None
    try:
        rel, mime = safe_files.save_upload(content, receipt.filename, subdir=_RECEIPT_SUBDIR)
    except safe_files.UploadRejected as e:
        raise HTTPException(status_code=400, detail=f"Justificatif rejeté : {e}") from e
    return rel, mime
