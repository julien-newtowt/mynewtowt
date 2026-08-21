"""Support applicatif (« Assistance ») — routes.

⚠️ NE PAS CONFONDRE avec ``tickets_router`` (module ``tickets``), qui porte les
incidents d'exploitation portuaire en escale. **Aucun import croisé** entre les
deux (règle de différenciation, spec §1).

Deux règles de sécurité vivent ICI et non dans la matrice de permissions, qui ne
sait pas les exprimer (rôle × module × niveau) :

1. **Cloisonnement de lecture** — un non-administrateur ne voit que SES demandes.
   Un accès à celle d'un autre renvoie **404** et non 403 : un 403 confirmerait
   l'existence de la ressource.
2. **Tri réservé** — changement d'état (hors les deux transitions du demandeur)
   et assignation sont réservés à l'administrateur.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.support import SupportTicketAttachment
from app.models.user import User
from app.permissions import is_administrator, require_permission
from app.services import support
from app.services.activity import record as activity_record
from app.services.notifications import create as notify_create
from app.templating import templates

logger = logging.getLogger("support")

router = APIRouter(prefix="/support", tags=["support"])

_ESCALATION_ROLE = "administrateur"


def _is_admin(user) -> bool:
    return is_administrator(user.role)


async def _load_or_404(db: AsyncSession, ref: str, user):
    """Charge une demande en appliquant le cloisonnement de lecture.

    **404 et non 403** quand la demande existe mais n'appartient pas au lecteur :
    un 403 révélerait qu'une demande porte cette référence.
    """
    ticket = await support.get_by_reference(db, ref)
    if ticket is None:
        raise HTTPException(status_code=404, detail="demande introuvable")
    if not support.can_view(ticket, user_id=user.id, is_admin=_is_admin(user)):
        raise HTTPException(status_code=404, detail="demande introuvable")
    return ticket


def _text(form, field: str, default: str = "") -> str:
    """Valeur de formulaire garantie ``str``.

    ``FormData.get()`` rend ``UploadFile | str | None`` : un fichier envoyé dans
    un champ texte arriverait donc tel quel au métier. La plupart des routeurs du
    dépôt passent la valeur brute et accumulent des erreurs ``arg-type`` ; on
    coupe court ici, ce qui est aussi plus sûr.
    """
    value = form.get(field)
    return value if isinstance(value, str) else default


def _text_or_none(form, field: str) -> str | None:
    value = form.get(field)
    if not isinstance(value, str):
        return None
    return value or None


def _form_files(form, field: str) -> list:
    """Toutes les valeurs d'un champ de formulaire, pas seulement la dernière.

    ``FormData.get()`` ne rend que la **dernière** valeur d'un champ répété : sur
    un ``<input multiple>``, s'y fier perdrait silencieusement les fichiers
    précédents. ``getlist`` est donc obligatoire — mais il n'existe que sur
    ``FormData``, d'où le repli pour les mappings simples.
    """
    getlist = getattr(form, "getlist", None)
    if getlist is not None:
        return list(getlist(field))
    value = form.get(field)
    return [value] if value is not None else []


def _nomenclature() -> dict:
    """Valeurs et CLÉS i18n passées aux gabarits (aucun libellé en clair ici)."""
    return {
        "kinds": support.KINDS,
        "kind_keys": support.KIND_LABEL_KEYS,
        "severities": support.SEVERITIES,
        "severity_keys": support.SEVERITY_LABEL_KEYS,
        "statuses": support.STATUSES,
        "status_keys": support.STATUS_LABEL_KEYS,
    }


# ───────────────────────────── Listes ─────────────────────────────
# Les chemins statiques sont déclarés AVANT `/{ref}` : FastAPI résout dans
# l'ordre de déclaration, et `/{ref}` capturerait sinon « archives ».


async def _render_list(request, db, user, *, archived: bool):
    rows = await support.list_requests(
        db,
        viewer_id=user.id,
        is_admin=_is_admin(user),
        archived=archived,
        status=request.query_params.get("status") or None,
        kind=request.query_params.get("kind") or None,
        severity=request.query_params.get("severity") or None,
    )
    ctx = {
        "request": request,
        "user": user,
        "rows": rows,
        "archived_view": archived,
        "is_admin": _is_admin(user),
        "filter_status": request.query_params.get("status") or "",
        "filter_kind": request.query_params.get("kind") or "",
        "filter_severity": request.query_params.get("severity") or "",
        "archive_days": support.ARCHIVE_AFTER_DAYS,
        **_nomenclature(),
    }
    if _is_admin(user):
        ctx["stats"] = await support.stats(db)
    return templates.TemplateResponse("staff/support/list.html", ctx)


@router.get("", response_class=HTMLResponse)
async def list_current(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("support", "C")),
) -> HTMLResponse:
    return await _render_list(request, db, user, archived=False)


@router.get("/archives", response_class=HTMLResponse)
async def list_archives(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("support", "C")),
) -> HTMLResponse:
    """Archives — demandes terminales de plus de 90 jours.

    Cet écran n'est pas optionnel : sans lui, une demande archivée deviendrait
    inatteignable. C'est exactement le défaut constaté sur le module `tickets`,
    dont le code annonce un écran « Archives » qui n'existe pas.
    """
    return await _render_list(request, db, user, archived=True)


# ───────────────────────────── Création ─────────────────────────────


@router.get("/nouveau", response_class=HTMLResponse)
async def new_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("support", "M")),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "staff/support/new.html",
        {
            "request": request,
            "user": user,
            # Pré-rempli par le lien « Signaler un problème » (?from=…). Assaini
            # côté serveur à l'enregistrement — jamais fait confiance tel quel.
            "from_url": support.sanitize_page_url(request.query_params.get("from")) or "",
            "max_attachments": support.MAX_ATTACHMENTS,
            **_nomenclature(),
        },
    )


@router.post("/nouveau", response_class=HTMLResponse)
async def create_action(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("support", "M")),
):
    from app.services.planning import parse_form_datetime
    from app.services.safe_files import UploadRejected, content_length_exceeds_max

    # Pré-filtre anti-OOM : refuse un envoi géant AVANT de lire le corps.
    if content_length_exceeds_max(request.headers.get("content-length")):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="envoi trop volumineux",
        )

    form = await request.form()
    error: str | None = None
    try:
        ticket = await support.create_request(
            db,
            reporter_id=user.id,
            reporter_role=user.role,
            kind=_text(form, "kind"),
            severity=_text(form, "severity"),
            title=_text(form, "title"),
            description=_text(form, "description"),
            page_url=_text_or_none(form, "page_url"),
            http_referer=request.headers.get("referer"),
            user_agent=request.headers.get("user-agent"),
            app_version=settings.app_version,
            occurred_at=parse_form_datetime(_text(form, "occurred_at"), allow_empty=True),
        )
    except support.SupportError as exc:
        error = str(exc)

    if error is not None:
        return templates.TemplateResponse(
            "staff/support/new.html",
            {
                "request": request,
                "user": user,
                "from_url": support.sanitize_page_url(_text_or_none(form, "page_url")) or "",
                "max_attachments": support.MAX_ATTACHMENTS,
                "error": error,
                **_nomenclature(),
            },
            status_code=400,
        )

    # Pièces jointes facultatives fournies au dépôt. Une pièce refusée n'annule
    # PAS la demande : on la signale et on garde le reste (le signalement a plus
    # de valeur que sa capture d'écran).
    rejected: list[str] = []
    for upload in _form_files(form, "attachments"):
        filename = getattr(upload, "filename", None)
        if not filename:
            continue
        content = await upload.read()
        if not content:
            continue
        try:
            await support.add_attachment(
                db,
                ticket,
                content=content,
                original_name=filename,
                uploaded_by_id=user.id,
            )
        except (UploadRejected, support.SupportError) as exc:
            rejected.append(f"{filename} — {exc}")

    await activity_record(
        db,
        action="support_request_create",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="support",
        entity_type="support_ticket",
        entity_id=ticket.id,
        entity_label=ticket.reference,
        detail=(
            f"{ticket.kind}/{ticket.severity}: {ticket.title}"
            + (f" — pièces refusées : {'; '.join(rejected)}" if rejected else "")
        ),
    )
    await notify_create(
        db,
        type="info",
        title=f"Assistance — {ticket.reference}",
        detail=f"{ticket.severity} · {ticket.title}",
        link=f"/support/{ticket.reference}",
        target_role=_ESCALATION_ROLE,
    )
    if rejected:
        logger.warning("support %s — pièces refusées : %s", ticket.reference, rejected)
    return RedirectResponse(url=f"/support/{ticket.reference}", status_code=303)


# ───────────────────────────── Fiche ─────────────────────────────


@router.get("/{ref}", response_class=HTMLResponse)
async def detail(
    ref: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("support", "C")),
) -> HTMLResponse:
    ticket = await _load_or_404(db, ref, user)
    is_admin = _is_admin(user)
    assignees = (
        list((await db.execute(select(User).where(User.is_active.is_(True)))).scalars().all())
        if is_admin
        else []
    )
    return templates.TemplateResponse(
        "staff/support/detail.html",
        {
            "request": request,
            "user": user,
            "t_ref": ticket,
            "is_admin": is_admin,
            # ⚠️ Seule barrière contre la fuite d'une note interne au demandeur.
            "comments": support.visible_comments(ticket, is_admin=is_admin),
            "attachments": list(ticket.attachments),
            "read_only": support.is_read_only(ticket),
            "archived": support.is_archived(ticket),
            "allowed_targets": sorted(support.allowed_targets(ticket.status)),
            "reporter_targets": sorted(
                tgt
                for tgt in support.allowed_targets(ticket.status)
                if support.is_reporter_transition(ticket.status, tgt)
            ),
            "assignees": assignees,
            "max_attachments": support.MAX_ATTACHMENTS,
            **_nomenclature(),
        },
    )


# ───────────────────────────── Mutations ─────────────────────────────


@router.post("/{ref}/commentaire")
async def comment_action(
    ref: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("support", "M")),
):
    ticket = await _load_or_404(db, ref, user)
    if support.is_read_only(ticket):
        raise HTTPException(status_code=409, detail="demande terminée ou archivée")
    form = await request.form()
    is_admin = _is_admin(user)
    # Une note interne ne peut être posée que par l'administrateur : sinon le
    # demandeur pourrait s'écrire des notes qu'il est seul à ne pas voir.
    is_internal = bool(form.get("is_internal")) and is_admin
    try:
        await support.add_comment(
            db,
            ticket,
            body=_text(form, "body"),
            author_id=user.id,
            author_name=user.full_name or user.username,
            is_internal=is_internal,
        )
    except support.SupportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await activity_record(
        db,
        action="support_comment_add",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="support",
        entity_type="support_ticket",
        entity_id=ticket.id,
        entity_label=ticket.reference,
        detail="note interne" if is_internal else "commentaire",
    )
    if not is_internal:
        # Prévient l'autre partie : l'admin si c'est le demandeur qui écrit,
        # le demandeur sinon.
        if is_admin and ticket.reporter_id != user.id:
            await notify_create(
                db,
                type="info",
                title=f"Assistance — {ticket.reference}",
                detail="Nouveau message du support.",
                link=f"/support/{ticket.reference}",
                target_user_id=ticket.reporter_id,
            )
        else:
            await notify_create(
                db,
                type="info",
                title=f"Assistance — {ticket.reference}",
                detail="Nouveau message du demandeur.",
                link=f"/support/{ticket.reference}",
                target_role=_ESCALATION_ROLE,
            )
    return RedirectResponse(url=f"/support/{ticket.reference}", status_code=303)


@router.post("/{ref}/statut")
async def status_action(
    ref: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("support", "M")),
):
    ticket = await _load_or_404(db, ref, user)
    if support.is_archived(ticket):
        raise HTTPException(status_code=409, detail="demande archivée : lecture seule")
    form = await request.form()
    target = _text(form, "status")

    # ── Tri réservé : hors les deux transitions du demandeur, admin seul.
    if not _is_admin(user) and not support.is_reporter_transition(ticket.status, target):
        raise HTTPException(status_code=403, detail="tri réservé à l'administration")

    previous = ticket.status
    try:
        await support.change_status(
            db, ticket, target, resolution=_text_or_none(form, "resolution")
        )
    except support.InvalidSupportTransition as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except support.SupportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await activity_record(
        db,
        action="support_status_change",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="support",
        entity_type="support_ticket",
        entity_id=ticket.id,
        entity_label=ticket.reference,
        detail=f"{previous} → {ticket.status}",
    )
    if ticket.reporter_id != user.id:
        await notify_create(
            db,
            type="info",
            title=f"Assistance — {ticket.reference}",
            detail=f"Votre demande passe à « {ticket.status} ».",
            link=f"/support/{ticket.reference}",
            target_user_id=ticket.reporter_id,
        )
    return RedirectResponse(url=f"/support/{ticket.reference}", status_code=303)


@router.post("/{ref}/assignation")
async def assign_action(
    ref: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("support", "M")),
):
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="assignation réservée à l'administration")
    ticket = await _load_or_404(db, ref, user)
    form = await request.form()
    raw = _text(form, "assigned_to_id")
    await support.assign(db, ticket, int(raw) if raw else None)
    await activity_record(
        db,
        action="support_assign",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="support",
        entity_type="support_ticket",
        entity_id=ticket.id,
        entity_label=ticket.reference,
        detail=f"assignée à {raw or '—'}",
    )
    return RedirectResponse(url=f"/support/{ticket.reference}", status_code=303)


# ───────────────────────── Pièces jointes ─────────────────────────


@router.post("/{ref}/pieces")
async def attachment_add(
    ref: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("support", "M")),
):
    from app.services.safe_files import UploadRejected, content_length_exceeds_max

    if content_length_exceeds_max(request.headers.get("content-length")):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="envoi trop volumineux",
        )
    ticket = await _load_or_404(db, ref, user)
    form = await request.form()
    upload = form.get("attachment")
    # `isinstance` et non `hasattr` : le champ peut arriver en texte ou absent,
    # et seul un test de type restreint réellement `UploadFile | str | None`.
    if not isinstance(upload, UploadFile) or not upload.filename:
        raise HTTPException(status_code=400, detail="aucun fichier")
    filename = upload.filename
    content = await upload.read()
    try:
        att = await support.add_attachment(
            db, ticket, content=content, original_name=filename, uploaded_by_id=user.id
        )
    except (UploadRejected, support.SupportError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await activity_record(
        db,
        action="support_attachment_add",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="support",
        entity_type="support_ticket",
        entity_id=ticket.id,
        entity_label=ticket.reference,
        detail=f"{att.original_name} ({att.size_bytes} o)",
    )
    return RedirectResponse(url=f"/support/{ticket.reference}", status_code=303)


@router.get("/{ref}/pieces/{att_id}/telecharger")
async def attachment_download(
    ref: str,
    att_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("support", "C")),
):
    """Téléchargement — demandeur ou administrateur SEULEMENT.

    ⚠️ Point de contrôle le plus sensible du module. Une capture d'écran est une
    exfiltration potentielle : elle peut contenir des données d'un autre module
    (finance, RH). Le droit ``support:C`` ne donne donc PAS accès aux pièces
    d'autrui.
    """
    from app.services.safe_files import UploadRejected, resolve_path

    ticket = await _load_or_404(db, ref, user)
    att = await db.get(SupportTicketAttachment, att_id)
    if att is None or att.support_ticket_id != ticket.id:
        raise HTTPException(status_code=404)
    if not support.can_download_attachment(ticket, user_id=user.id, is_admin=_is_admin(user)):
        raise HTTPException(status_code=404)
    try:
        path = resolve_path(att.file_path)
    except (UploadRejected, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="fichier introuvable") from exc
    return FileResponse(
        path,
        media_type=att.file_mime or "application/octet-stream",
        filename=att.original_name or path.name,
    )


@router.post("/{ref}/pieces/{att_id}/supprimer")
async def attachment_delete(
    ref: str,
    att_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("support", "M")),
):
    """Suppression d'une pièce : son auteur, ou l'administrateur."""
    from app.services.safe_files import resolve_path

    ticket = await _load_or_404(db, ref, user)
    att = await db.get(SupportTicketAttachment, att_id)
    if att is None or att.support_ticket_id != ticket.id:
        raise HTTPException(status_code=404)
    if not (_is_admin(user) or att.uploaded_by_id == user.id):
        raise HTTPException(status_code=403, detail="suppression réservée à l'auteur")
    label = att.original_name or str(att.id)
    try:
        resolve_path(att.file_path).unlink(missing_ok=True)
    except Exception:
        logger.exception("support attachment unlink failed (%s)", att.file_path)
    await db.delete(att)
    await db.flush()
    await activity_record(
        db,
        action="support_attachment_delete",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="support",
        entity_type="support_ticket",
        entity_id=ticket.id,
        entity_label=ticket.reference,
        detail=label,
    )
    return RedirectResponse(url=f"/support/{ticket.reference}", status_code=303)
