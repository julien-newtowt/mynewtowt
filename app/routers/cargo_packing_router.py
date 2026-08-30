"""Packing list — vue staff interne (token-based portal = cargo_portal_router).

Reprise de la V3.0.0. Workflow draft → submitted → locked. Audit trail
field-by-field. Verrouillage par un staff après validation côté armateur.
"""

from __future__ import annotations

import json

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
from app.services import bl_workflow, cargo_excel
from app.services.activity import record as activity_record
from app.services.packing_list import (
    apply_batch_update,
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


def _mutation_response(request: Request, pl_id: int, message: str) -> Response:
    """Réponse standard d'une mutation répétitive de la fiche packing list.

    Reprise UX Phase 3 (docs/design/03-reprise-ux-legacy.md) — copie EXACTE du
    pattern posé en Phase 1 par ``escale_router._mutation_response`` : sous
    HTMX, on ne recharge plus la page — 204 + ``HX-Trigger`` qui (a) affiche le
    toast (toast.js) et (b) déclenche ``cargoRefresh``, écouté par le conteneur
    ``#pl-sections`` qui se re-remplit via ``hx-get`` + ``hx-select`` sur la
    page elle-même. Sans JS : 303 classique, inchangé.

    ⚠️ Réservé aux actions répétitives (lock/unlock, ajout de batch, édition de
    batch, message staff→portail) — PAS aux actions BL sensibles
    (draft/revise/confirm-delivery/shipped-on-board) ni aux suppressions, qui
    gardent leur redirect classique (revue de sécurité plus simple).
    """
    if request.headers.get("hx-request"):
        return Response(
            status_code=204,
            headers={
                "HX-Trigger": json.dumps(
                    {
                        "toast": {"message": message, "type": "success"},
                        "cargoRefresh": True,
                    }
                )
            },
        )
    return RedirectResponse(url=f"/cargo/packing-lists/{pl_id}", status_code=303)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def packing_lists_index(
    request: Request,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    """Maillage cargo ↔ voyage (Phase 2, §9.2) — colonnes Voyage/Navire + filtre
    ``?leg_id=``, résolus COM-11 (``coalesce(pl.leg_id, order.leg_id,
    booking.leg_id)``) en **une seule requête groupée** (jointure Order/Booking
    dans le SELECT principal — pas de résolution par packing list)."""
    from app.models.leg import Leg
    from app.models.vessel import Vessel

    # COM-11 — même règle de résolution que `resolve_pl_context`, mais portée par
    # la requête (pas de coûteux aller-retour par ligne).
    resolved_leg_id = func.coalesce(PackingList.leg_id, Order.leg_id, Booking.leg_id)
    stmt = (
        select(PackingList, resolved_leg_id)
        .options(selectinload(PackingList.batches))
        .outerjoin(Order, PackingList.order_id == Order.id)
        .outerjoin(Booking, PackingList.booking_id == Booking.id)
    )
    if leg_id is not None:
        stmt = stmt.where(resolved_leg_id == leg_id)
    rows = (await db.execute(stmt.order_by(PackingList.updated_at.desc()).limit(100))).all()
    pls = [row[0] for row in rows]
    leg_id_by_pl: dict[int, int | None] = {row[0].id: row[1] for row in rows}

    # Legs (+ navires) des packing lists affichées, en un seul SELECT groupé.
    wanted_leg_ids = {lid for lid in leg_id_by_pl.values() if lid is not None}
    leg_and_vessel_by_leg_id: dict[int, tuple[Leg, Vessel | None]] = {}
    if wanted_leg_ids:
        leg_rows = (
            await db.execute(
                select(Leg, Vessel)
                .outerjoin(Vessel, Leg.vessel_id == Vessel.id)
                .where(Leg.id.in_(wanted_leg_ids))
            )
        ).all()
        leg_and_vessel_by_leg_id = {leg.id: (leg, vessel) for leg, vessel in leg_rows}

    filter_leg = await db.get(Leg, leg_id) if leg_id is not None else None

    from app.services import messaging

    unread = await messaging.portal_unread_counts(db, [pl.id for pl in pls], reader="staff")
    return templates.TemplateResponse(
        "staff/cargo/packing_lists.html",
        {
            "request": request,
            "user": user,
            "packing_lists": pls,
            "unread": unread,
            "leg_id_by_pl": leg_id_by_pl,
            "leg_and_vessel_by_leg_id": leg_and_vessel_by_leg_id,
            "filter_leg_id": leg_id,
            "filter_leg": filter_leg,
        },
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
    # ⚠️ `order_id` est NULL pour une packing list issue d'un BOOKING (XOR
    # `ck_packing_lists_order_xor_booking`) — c'est le cas normal du rail client, pas
    # une anomalie. Sans cette garde, `db.get(Order, None)` déclenche un SAWarning
    # « fully NULL primary key » à chaque affichage de ces packing lists.
    order = await db.get(Order, pl.order_id) if pl.order_id else None
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

    # §5.0 — date de mise à bord résolue par lot (dérivée de la timeline d'escale,
    # ou override justifié). Calculée ici et non dans le gabarit : la dérivation
    # interroge la base, et un gabarit ne doit pas déclencher de requêtes.
    # Maillage cargo ↔ voyage (Phase 2, §9.2) — même appel COM-11, réutilisé
    # aussi pour le bandeau de contexte voyage (leg/navire/POL/POD).
    _o, booking, leg, vessel, pol, pod = await resolve_pl_context(db, pl)
    sob_by_batch = {
        batch.id: await bl_workflow.resolve_shipped_on_board(
            db, batch=batch, leg_id=leg.id if leg else None
        )
        for batch in pl.batches
    }

    # §5.1 — registre de remise par lot.
    from app.services import bl_delivery

    delivery_by_batch = {
        batch.id: await bl_delivery.delivery_status(db, batch_id=batch.id) for batch in pl.batches
    }

    # §4.1 — documents annulés par une révision. Affichés : un registre doit montrer
    # ce qui a circulé, pas seulement l'état courant.
    revisions_by_batch = {
        batch.id: await bl_workflow.revisions_for_batch(db, batch_id=batch.id)
        for batch in pl.batches
    }

    return templates.TemplateResponse(
        "staff/cargo/packing_list_detail.html",
        {
            "request": request,
            "user": user,
            "pl": pl,
            "order": order,
            "booking": booking,
            "leg": leg,
            "vessel": vessel,
            "pol": pol,
            "pod": pod,
            "messages": messages,
            "sob_by_batch": sob_by_batch,
            "delivery_by_batch": delivery_by_batch,
            "revisions_by_batch": revisions_by_batch,
            "suggested_means": bl_delivery.SUGGESTED_MEANS,
            "number_of_originals": bl_delivery.NUMBER_OF_ORIGINALS,
        },
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
    return _mutation_response(request, pl_id, "Batch ajouté.")


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
    return _mutation_response(request, pl_id, "Packing list verrouillée.")


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
    return _mutation_response(request, pl_id, "Packing list déverrouillée.")


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
    return _mutation_response(request, pl_id, "Message envoyé au client.")


async def _get_batch_or_404(db: AsyncSession, pl_id: int, batch_id: int) -> PackingListBatch:
    b = await db.get(PackingListBatch, batch_id)
    if b is None or b.packing_list_id != pl_id:
        raise HTTPException(status_code=404)
    return b


def _assert_bl_not_frozen(batch: PackingListBatch) -> None:
    """409 si le lot porte un BL signé — la correction passe par une révision.

    ⚠️ À appeler **avant** toute écriture. Le verrou de la packing list
    (``can_modify``) et le gel du BL sont **indépendants** : le premier est porté
    par la packing list, le second par le lot. Vérifier l'un ne dispense pas de
    vérifier l'autre.
    """
    if bl_workflow.is_frozen(batch):
        raise HTTPException(
            status_code=409,
            detail=(
                f"BL {batch.bl_number or batch.batch_number} signé "
                f"({batch.bl_state}) — corriger par révision numérotée, pas par édition."
            ),
        )


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
    # ⚠️ Le gel se vérifie AVANT toute écriture : contrôler après aurait laissé la
    # modification s'appliquer sur un connaissement signé, ce qui est précisément
    # ce qu'on interdit.
    _assert_bl_not_frozen(batch)
    new_values = coerce_batch_form(dict(await request.form()))
    actor = user.full_name or user.username
    changed = await apply_batch_update(
        db, batch=batch, new_values=new_values, actor="staff", actor_name=actor
    )
    # Une modification effective après validation client ANNULE cette validation
    # et ramène le BL à `draft` — une validation porte sur un contenu précis.
    if changed:
        await bl_workflow.invalidate_validation_on_edit(
            db, batch=batch, actor_name=actor, ip=_client_ip(request)
        )
    return _mutation_response(request, pl_id, "Batch modifié.")


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
    # Un lot portant un BL signé ne se supprime pas : le registre doit rester
    # opposable. La correction passe par une révision qui annule la précédente,
    # les deux restant tracées.
    _assert_bl_not_frozen(batch)
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


@router.post("/{pl_id}/delete")
async def delete_packing_list(
    pl_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "S")),
):
    """CARGO-14 — suppression d'une packing list entière (interdite si
    verrouillée). Les batches, messages, documents et l'audit sont supprimés en
    cascade (FK ``ondelete=CASCADE`` / ``delete-orphan``). Tracé dans le journal
    d'activité (append-only, indépendant de la PL)."""
    pl = await db.get(PackingList, pl_id)
    if pl is None:
        raise HTTPException(status_code=404)
    if not can_modify(pl):
        raise HTTPException(status_code=409, detail="packing list verrouillée")
    await activity_record(
        db,
        action="delete",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="cargo",
        entity_type="packing_list",
        entity_id=pl_id,
        entity_label=f"PL-{pl_id}",
    )
    await db.delete(pl)
    await db.flush()
    return RedirectResponse(url="/cargo/packing-lists", status_code=303)


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


@router.post("/{pl_id}/batches/{batch_id}/bl/draft")
async def generate_bl_draft(
    pl_id: int,
    batch_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    """Génère le draft de BL et attribue son numéro. **Écriture ⇒ POST.**

    ⚠️ Ce que cette route corrige. L'émission passait par un `GET` en permission
    `cargo:C` qui **écrivait en base** :

    - un `GET` qui écrit s'exécute sur un **préchargement de lien**, un scan de
      sécurité ou un passage de crawler — donc des connaissements émis en série,
      avec des numéros consommés, sans que personne ne l'ait demandé ;
    - `cargo:C` est la permission de **consultation** : elle autorise `technique`,
      `data_analyst` et **`marins`** à émettre un titre de propriété.

    La génération est donc en `POST` + `cargo:M`, et la consultation reste en `GET`
    + `cargo:C` mais **n'écrit plus rien**.
    """
    pl = await db.get(PackingList, pl_id)
    if pl is None:
        raise HTTPException(status_code=404)
    if not can_modify(pl):
        raise HTTPException(status_code=409, detail="packing list verrouillée")
    batch = await _get_batch_or_404(db, pl_id, batch_id)
    _order, _booking, leg, _vessel, _pol, _pod = await resolve_pl_context(db, pl)
    try:
        await bl_workflow.generate_draft(
            db, pl=pl, batch=batch, leg=leg, user=user, ip=_client_ip(request)
        )
    except bl_workflow.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(
        url=f"/cargo/packing-lists/{pl_id}/batches/{batch_id}/bl.pdf", status_code=303
    )


@router.post("/{pl_id}/batches/{batch_id}/bl/shipped-on-board")
async def override_bl_shipped_on_board(
    pl_id: int,
    batch_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    """§5.0 — corrige la date de mise à bord, **justification obligatoire**.

    La date effective est normalement **dérivée** du dernier jour des opérations
    réelles d'escale. Cette route ne sert qu'aux cas où la timeline ne reflète pas
    la réalité (SOF saisi en retard, par exemple).

    Le motif est exigé : un connaissement antidaté est une fraude documentaire, et
    le journal demandé « en cas de contrôle » n'a de valeur que s'il porte le
    pourquoi. Un refus renvoie **400** (donnée d'entrée invalide), un BL signé
    **409** (la correction passe alors par une révision).
    """
    from datetime import date as _date

    from app.services.derived_override import JustificationRequired

    pl = await db.get(PackingList, pl_id)
    if pl is None:
        raise HTTPException(status_code=404)
    if not can_modify(pl):
        raise HTTPException(status_code=409, detail="packing list verrouillée")
    batch = await _get_batch_or_404(db, pl_id, batch_id)

    form = dict(await request.form())
    raw_date = str(form.get("shipped_on_board") or "").strip()
    try:
        new_date = _date.fromisoformat(raw_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="date de mise à bord invalide (format attendu AAAA-MM-JJ)"
        ) from exc

    _order, _booking, leg, _vessel, _pol, _pod = await resolve_pl_context(db, pl)
    try:
        await bl_workflow.override_shipped_on_board(
            db,
            batch=batch,
            leg_id=leg.id if leg else None,
            new_date=new_date,
            reason=str(form.get("reason") or ""),
            user=user,
            ip=_client_ip(request),
        )
    except JustificationRequired as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except bl_workflow.BlFrozen as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return RedirectResponse(url=f"/cargo/packing-lists/{pl_id}", status_code=303)


@router.get("/{pl_id}/batches/{batch_id}/bl.pdf")
async def batch_bill_of_lading(
    pl_id: int,
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> Response:
    """CARGO-01 — rend le BL d'un lot. **Lecture seule : n'attribue plus de numéro.**

    Un lot sans BL généré renvoie 404 : la consultation ne crée pas le document.
    Passer par `POST .../bl/draft` pour le générer.
    """
    pl = await db.get(PackingList, pl_id)
    if pl is None:
        raise HTTPException(status_code=404)
    batch = await _get_batch_or_404(db, pl_id, batch_id)
    if not batch.bl_number:
        raise HTTPException(
            status_code=404,
            detail="aucun BL généré pour ce lot — utiliser l'action « Générer le draft ».",
        )
    _order, _booking, leg, vessel, pol, pod = await resolve_pl_context(db, pl)
    bl_number = batch.bl_number
    # §5.0 — résolue ici (la dérivation interroge la base ; le rendu est synchrone).
    sob = await bl_workflow.resolve_shipped_on_board(
        db, batch=batch, leg_id=leg.id if leg else None
    )
    doc = render_bill_of_lading_from_pl(
        pl=pl,
        batch=batch,
        leg=leg,
        vessel=vessel,
        pol=pol,
        pod=pod,
        bl_number=bl_number,
        issued_at=batch.bl_issued_at,
        shipped_on_board=sob,
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
    # 🔴 Cet import REMPLACE les lots existants. S'il en reste un dont le BL est
    # signé, l'import détruirait un titre opposable — et le compterait comme
    # « importé ». On refuse en bloc plutôt que d'importer partiellement : un
    # import à moitié appliqué sur un registre de connaissements est pire qu'un
    # refus.
    frozen = [b for b in pl.batches if bl_workflow.is_frozen(b)]
    if frozen:
        raise HTTPException(
            status_code=409,
            detail=(
                "import refusé : "
                + ", ".join(f"BL {b.bl_number or b.batch_number} ({b.bl_state})" for b in frozen)
                + " — un lot signé ne peut pas être remplacé par un import."
            ),
        )
    # ⚠️ UPSERT, plus « delete-all + recreate ». L'ancien remplacement détruisait les
    # lots existants, donc leur `bl_number` : chaque import CONSOMMAIT des numéros de
    # connaissement et cassait les liens déjà transmis au client. Un registre ne se
    # reconstruit pas à chaque import de tableur.
    #
    # Rapprochement par `batch_number` (colonne `BATCH_NUMBER` de l'export). Une ligne
    # sans clé exploitable est une création.
    by_number = {b.batch_number: b for b in pl.batches if b.batch_number is not None}
    seen: set[int] = set()
    updated = created = 0
    for vals in parsed:
        row = dict(vals)
        match_number = row.pop(cargo_excel.MATCH_KEY, None)
        existing = by_number.get(match_number) if match_number is not None else None
        if existing is not None:
            seen.add(existing.id)
            # `apply_batch_update` audite champ par champ et ne touche NI `bl_number`
            # NI l'état du BL — c'est tout l'intérêt du rapprochement.
            changed = await apply_batch_update(
                db, batch=existing, new_values=row, actor="staff", actor_name=actor_name
            )
            if changed:
                await bl_workflow.invalidate_validation_on_edit(
                    db, batch=existing, actor_name=actor_name, ip=_client_ip(request)
                )
            updated += 1
        else:
            await create_batch(db, pl=pl, vals=row, actor="staff", actor_name=actor_name)
            created += 1
    await db.flush()

    # Lots absents de l'import. On ne supprime que ceux qui NE PORTENT PAS de
    # connaissement : détruire un lot numéroté consommerait son numéro sans retour et
    # casserait un lien déjà remis au client. Les autres sont conservés — et le dit,
    # plutôt que de laisser croire à une synchronisation complète.
    kept_numbered: list[str] = []
    removed = 0
    for b in list(pl.batches):
        if b.id in seen:
            continue
        if b.bl_number:
            kept_numbered.append(b.bl_number)
            continue
        await db.delete(b)
        removed += 1
    await db.flush()

    summary = f"{updated} mis à jour, {created} créés, {removed} supprimés"
    if kept_numbered:
        summary += (
            f", {len(kept_numbered)} conservés car déjà numérotés ({', '.join(kept_numbered)})"
        )
    await record_audit(
        db,
        packing_list_id=pl.id,
        batch_id=None,
        actor="staff",
        actor_name=actor_name,
        field="_import_excel",
        old_value=None,
        new_value=summary,
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


@router.post("/{pl_id}/batches/{batch_id}/bl/confirm-delivery")
async def ops_confirm_bl_delivery(
    pl_id: int,
    batch_id: int,
    request: Request,
    file: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    """§5.1 — repli Opérations : attester d'une remise **hors plateforme**.

    > « Si les BLs sont envoyés en papier par exemple, l'équipe opérations pourra
    > confirmer la réception côté client en ajoutant la date et heure de
    > confirmation et moyen (téléphone, mail, etc.) + PJ possible. »

    L'attestation est tracée **comme un repli** : elle n'est jamais présentée comme
    une déclaration du client. Le **moyen** de remise est obligatoire — une
    attestation qui ne dit pas *comment* la remise a eu lieu n'établit rien face à un
    assureur (400 s'il manque).

    Refusée tant que le connaissement n'est pas signé : avant la signature aucun
    original n'existe (409).
    """
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from app.services import bl_delivery
    from app.services.safe_files import UploadRejected, save_upload

    if content_length_exceeds_max(request.headers.get("content-length")):
        raise HTTPException(status_code=413, detail="fichier trop volumineux")

    pl = await db.get(PackingList, pl_id)
    if pl is None:
        raise HTTPException(status_code=404)
    batch = await _get_batch_or_404(db, pl_id, batch_id)

    form = dict(await request.form())
    raw_when = str(form.get("confirmed_at") or "").strip()
    try:
        # `datetime-local` rend « AAAA-MM-JJTHH:MM » (sans fuseau) : on l'ancre en
        # UTC plutôt que de laisser une valeur naïve entrer en base.
        when = _dt.fromisoformat(raw_when)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="date et heure de confirmation invalides (format attendu AAAA-MM-JJTHH:MM)",
        ) from exc
    if when.tzinfo is None:
        when = when.replace(tzinfo=_UTC)

    attachment_path = None
    if file is not None and getattr(file, "filename", None):
        content = await file.read()
        # Le header Content-Length est falsifiable : on revérifie après lecture.
        size_check = validate_size(content)
        if not size_check.ok:
            raise HTTPException(status_code=413, detail=size_check.reason)
        try:
            attachment_path, _mime = save_upload(
                content, file.filename or "preuve", subdir="bl-delivery"
            )
        except UploadRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        await bl_delivery.confirm_by_ops(
            db,
            batch=batch,
            user=user,
            confirmed_at=when,
            means=str(form.get("means") or ""),
            notes=str(form.get("notes") or ""),
            attachment_path=attachment_path,
            ip=_client_ip(request),
        )
    except bl_delivery.MeansRequired as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except bl_delivery.DeliveryReceiptError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return RedirectResponse(url=f"/cargo/packing-lists/{pl_id}", status_code=303)


@router.post("/{pl_id}/batches/{batch_id}/bl/revise")
async def revise_bl(
    pl_id: int,
    batch_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    """§4.1 — révise un connaissement **signé** : archive l'ancien, en ouvre un neuf.

    C'est la seule correction possible après signature. Le document annulé n'est ni
    modifié ni supprimé : il est archivé avec son numéro, son empreinte, son signataire
    et le contenu exact qui avait été signé.

    Le nouveau document repart à `draft` — le client devra **revalider** et le
    commandant **resigner**. Une révision est un document neuf, pas un correctif
    appliqué sous une signature déjà donnée.

    400 si le motif manque, 409 si le connaissement n'est pas signé (auquel cas la
    correction passe par l'édition ordinaire).
    """
    from app.services.derived_override import JustificationRequired

    pl = await db.get(PackingList, pl_id)
    if pl is None:
        raise HTTPException(status_code=404)
    batch = await _get_batch_or_404(db, pl_id, batch_id)
    form = dict(await request.form())
    try:
        await bl_workflow.create_revision(
            db,
            batch=batch,
            user=user,
            reason=str(form.get("reason") or ""),
            ip=_client_ip(request),
        )
    except JustificationRequired as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except bl_workflow.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return RedirectResponse(url=f"/cargo/packing-lists/{pl_id}", status_code=303)


@router.get("/{pl_id}/batches/{batch_id}/bl.docx")
async def batch_bill_of_lading_docx(
    pl_id: int,
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> Response:
    """BL éditable (Word) d'un lot — **lecture seule**, comme la version PDF.

    Remplace `/cargo/booking/{ref}/bl.docx` (rail booking retiré) : celui-ci
    fabriquait un numéro à la volée sans jamais l'enregistrer, et écrivait « Trois
    originaux signés » y compris sur un document que personne n'avait signé.
    """
    from app.services.docx_generator import build_bill_of_lading_docx_from_pl

    pl = await db.get(PackingList, pl_id)
    if pl is None:
        raise HTTPException(status_code=404)
    batch = await _get_batch_or_404(db, pl_id, batch_id)
    if not batch.bl_number:
        raise HTTPException(
            status_code=404,
            detail="aucun BL généré pour ce lot — utiliser l'action « Générer le draft ».",
        )
    _order, _booking, leg, vessel, pol, pod = await resolve_pl_context(db, pl)
    sob = await bl_workflow.resolve_shipped_on_board(
        db, batch=batch, leg_id=leg.id if leg else None
    )
    doc = build_bill_of_lading_docx_from_pl(
        pl=pl,
        batch=batch,
        leg=leg,
        vessel=vessel,
        pol=pol,
        pod=pod,
        bl_number=batch.bl_number,
        issued_at=batch.bl_issued_at,
        shipped_on_board=sob,
    )
    return Response(
        content=doc.docx,
        media_type=doc.mime,
        headers={"Content-Disposition": f'attachment; filename="{doc.filename}"'},
    )
