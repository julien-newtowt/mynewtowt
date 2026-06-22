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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.booking import Booking
from app.models.commercial import Order
from app.models.leg import Leg
from app.models.packing_list import (
    PackingList,
    PackingListBatch,
    PackingListDocument,
    PortalMessage,
)
from app.models.port import Port
from app.models.vessel import Vessel
from app.services import rate_limit
from app.services.packing_list import (
    apply_batch_update,
    can_modify,
    coerce_batch_form,
    get_by_token,
    log_portal_access,
    record_audit,
)
from app.services.safe_files import UploadRejected, resolve_path, save_upload
from app.templating import templates

# CARGO-06 — types de documents déposables par l'expéditeur depuis le portail.
_PORTAL_DOC_KINDS = ("customs", "msds", "other")

router = APIRouter(prefix="/p", tags=["cargo-portal"])


async def _load_or_410(db: AsyncSession, token: str, request: Request) -> PackingList:
    # SEC-02 — rate-limit par IP sur le portail token. On compte aussi les
    # accès à un token invalide (freine le balayage de tokens). Le service de
    # rate-limit existe déjà (app/services/rate_limit) ; il n'était pas câblé.
    ip = _client_ip(request) or "unknown"
    if await rate_limit.exceeded(
        db,
        scope="portal_token",
        identifier=ip,
        max_attempts=60,
        window_minutes=10,
    ):
        raise HTTPException(status_code=429, detail="Trop de requêtes — patientez quelques minutes.")
    await rate_limit.record(db, scope="portal_token", identifier=ip)
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
    # PL issue d'une commande (rail A) OU d'un booking (rail B) : on dérive
    # le voyage du parent présent (B1 — fusion des rails).
    order = await db.get(Order, pl.order_id) if pl.order_id else None
    booking = await db.get(Booking, pl.booking_id) if pl.booking_id else None
    leg_id = (order.leg_id if order else None) or (booking.leg_id if booking else None)
    leg = await db.get(Leg, leg_id) if leg_id else None
    vessel = await db.get(Vessel, leg.vessel_id) if leg else None
    pol = await db.get(Port, leg.departure_port_id) if leg else None
    pod = await db.get(Port, leg.arrival_port_id) if leg else None
    # Repérage à bord — uniquement les positions de CETTE packing list
    # (jamais l'occupation globale du navire — confidentialité inter-clients).
    from app.models.stowage import BLOCKS, DECKS, HOLDS
    from app.services.stowage import locate_for_packing_list

    positions = await locate_for_packing_list(db, pl.id)
    return templates.TemplateResponse(
        "portal/home.html",
        {
            "request": request,
            "pl": pl,
            "order": order,
            "booking": booking,
            "leg": leg,
            "vessel": vessel,
            "pol": pol,
            "pod": pod,
            "token": token,
            "positions": positions,
            "target_zones": {p["zone"] for p in positions},
            "decks": DECKS,
            "holds": HOLDS,
            "blocks": BLOCKS,
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
async def portal_packing_add(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """CARGO-02/03 — ajout d'un batch par l'expéditeur (tous champs : adresses,
    marchandise, dimensions). Audit de la création."""
    pl = await _load_or_410(db, token, request)
    if not can_modify(pl):
        raise HTTPException(status_code=409, detail="packing list verrouillée")
    # Valeurs typées ; on ne passe au constructeur que les champs renseignés
    # (les colonnes à défaut — pallet_format/pallet_count — gardent leur défaut).
    vals = {k: v for k, v in coerce_batch_form(dict(await request.form())).items() if v is not None}
    count = int(
        (
            await db.scalar(
                select(func.count(PackingListBatch.id)).where(
                    PackingListBatch.packing_list_id == pl.id
                )
            )
        )
        or 0
    )
    b = PackingListBatch(packing_list_id=pl.id, batch_number=count + 1, **vals)
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
        new_value=f"{b.pallet_count}×{b.pallet_format}",
    )
    return RedirectResponse(url=f"/p/{token}/packing", status_code=303)


async def _portal_batch_or_404(
    db: AsyncSession, pl: PackingList, batch_id: int
) -> PackingListBatch:
    """Charge un batch en garantissant qu'il appartient bien à la PL du token."""
    b = await db.get(PackingListBatch, batch_id)
    if b is None or b.packing_list_id != pl.id:
        raise HTTPException(status_code=404)
    return b


@router.post("/{token}/packing/batches/{batch_id}/edit")
async def portal_packing_edit(
    token: str, batch_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    """CARGO-03 — édition d'un batch par l'expéditeur (audit field-by-field)."""
    pl = await _load_or_410(db, token, request)
    if not can_modify(pl):
        raise HTTPException(status_code=409, detail="packing list verrouillée")
    batch = await _portal_batch_or_404(db, pl, batch_id)
    await apply_batch_update(
        db,
        batch=batch,
        new_values=coerce_batch_form(dict(await request.form())),
        actor="client",
        actor_name=None,
    )
    return RedirectResponse(url=f"/p/{token}/packing", status_code=303)


@router.post("/{token}/packing/batches/{batch_id}/delete")
async def portal_packing_delete(
    token: str, batch_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    """CARGO-03 — suppression d'un batch par l'expéditeur."""
    pl = await _load_or_410(db, token, request)
    if not can_modify(pl):
        raise HTTPException(status_code=409, detail="packing list verrouillée")
    batch = await _portal_batch_or_404(db, pl, batch_id)
    await record_audit(
        db,
        packing_list_id=pl.id,
        batch_id=batch_id,
        actor="client",
        actor_name=None,
        field="_delete_batch",
        old_value=f"{batch.pallet_count}×{batch.pallet_format}",
        new_value=None,
    )
    await db.delete(batch)
    await db.flush()
    return RedirectResponse(url=f"/p/{token}/packing", status_code=303)


# ─────────────────────────── Documents (CARGO-06) ───────────────────────────


@router.get("/{token}/documents", response_class=HTMLResponse)
async def portal_documents(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    pl = await _load_or_410(db, token, request)
    docs = list(
        (
            await db.execute(
                select(PackingListDocument)
                .where(PackingListDocument.packing_list_id == pl.id)
                .order_by(PackingListDocument.uploaded_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        "portal/documents.html",
        {"request": request, "pl": pl, "token": token, "docs": docs, "kinds": _PORTAL_DOC_KINDS},
    )


@router.post("/{token}/documents/upload")
async def portal_documents_upload(
    token: str,
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form("other"),
    label: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    pl = await _load_or_410(db, token, request)
    if not can_modify(pl):
        raise HTTPException(status_code=409, detail="packing list verrouillée")
    content = await file.read()
    try:
        rel_path, mime = save_upload(content, file.filename or "document", subdir="cargo-portal")
    except UploadRejected as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.add(
        PackingListDocument(
            packing_list_id=pl.id,
            kind=kind if kind in _PORTAL_DOC_KINDS else "other",
            label=(label or file.filename or "document")[:200],
            file_path=rel_path,
            file_mime=mime,
            uploaded_by="client",
        )
    )
    await db.flush()
    return RedirectResponse(url=f"/p/{token}/documents", status_code=303)


@router.get("/{token}/documents/{doc_id}/download")
async def portal_documents_download(
    token: str, doc_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    pl = await _load_or_410(db, token, request)
    doc = await db.get(PackingListDocument, doc_id)
    if doc is None or doc.packing_list_id != pl.id or not doc.file_path:
        raise HTTPException(status_code=404)
    try:
        path = resolve_path(doc.file_path)
    except (UploadRejected, FileNotFoundError) as e:
        raise HTTPException(status_code=404) from e
    return FileResponse(
        path, media_type=doc.file_mime or "application/octet-stream", filename=doc.label or "document"
    )


@router.post("/{token}/documents/{doc_id}/delete")
async def portal_documents_delete(
    token: str, doc_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    pl = await _load_or_410(db, token, request)
    doc = await db.get(PackingListDocument, doc_id)
    if doc is None or doc.packing_list_id != pl.id:
        raise HTTPException(status_code=404)
    if doc.file_path:
        try:
            resolve_path(doc.file_path).unlink(missing_ok=True)
        except (UploadRejected, FileNotFoundError):
            pass
    await db.delete(doc)
    await db.flush()
    return RedirectResponse(url=f"/p/{token}/documents", status_code=303)


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
