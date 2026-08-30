"""Cargo module — document generation (BL, packing list, invoice, CO2).

Two entry points:

- Staff (/cargo/...) : list of bookings ready to issue documents, preview
  + download PDFs for any booking regardless of owner.
- Client (/me/bookings/{ref}/{doc}.pdf) : owner-only download of their
  own booking's documents.

Distance estimation: V3.0 uses a simple lookup table (orthodromic NM
between known port pairs). Beyond V3.0 we'll persist the actual leg
distance after the noon-report data is collected.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_client
from app.database import get_db
from app.i18n import t as i18n_t
from app.models.booking import Booking
from app.models.client_account import ClientAccount
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.anemos import resolve_distance_nm
from app.services.hx import mutation_response
from app.services.pdf_generator import (
    render_anemos_certificate,
    render_booking_note,
    render_invoice,
    render_packing_list,
)
from app.templating import templates

router = APIRouter(tags=["cargo"])


def _bl_mutation_response(request: Request, redirect_url: str, message: str) -> Response:
    """Réponse standard d'une mutation du rail BL client (reprise UX Phase 3, K-4).

    Wrapper d'une ligne sur ``services.hx.mutation_response`` (même motif que
    ``escale_router._mutation_response`` / ``client_dashboard_router._me_mutation_response``)
    — conservé pour ne pas retoucher tous les call sites de ce routeur. Ne
    change QUE la forme de la réponse — les invariants de validation (XOR
    client/staff, registre de remise append-only) restent entièrement portés
    par ``bl_workflow`` / ``bl_delivery``, appelés avant ce retour.
    """
    return mutation_response(
        request,
        redirect_url=redirect_url,
        message=message,
        refresh_event="meRefresh",
    )


async def _load_booking_bundle(
    db: AsyncSession, booking: Booking
) -> tuple[Leg, Vessel, Port, Port, ClientAccount]:
    leg = await db.get(Leg, booking.leg_id)
    vessel = await db.get(Vessel, leg.vessel_id) if leg else None
    pol = await db.get(Port, leg.departure_port_id) if leg else None
    pod = await db.get(Port, leg.arrival_port_id) if leg else None
    client = await db.get(ClientAccount, booking.client_account_id)
    # Eager-load items so the template never lazy-loads inside WeasyPrint.
    await db.refresh(booking, attribute_names=["items"])
    if not (leg and vessel and pol and pod and client):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Booking is missing referenced data (vessel/port/client)",
        )
    return leg, vessel, pol, pod, client


# ---------------------------------------------------------------------------
# Staff — cargo dashboard
# ---------------------------------------------------------------------------


@router.get("/cargo", response_class=HTMLResponse)
async def cargo_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    """List bookings that are 'issuable' (confirmed or beyond).

    Le nom du client et le code du leg sont joints ici : la liste affichait
    auparavant les identifiants techniques bruts (``#42``, ``17``), obligeant
    les agents à ouvrir chaque booking pour retrouver de quel client et de
    quelle traversée il s'agissait (demande Opérations, 2026-07).

    Jointures externes : ``client_account_id`` est nullable (booking saisi côté
    staff pour un client non inscrit), et on préfère afficher la ligne sans nom
    plutôt que de la faire disparaître.
    """
    issuable_statuses = ("confirmed", "loaded", "at_sea", "discharged", "delivered")
    res = await db.execute(
        select(Booking, ClientAccount.company_name, Leg.leg_code)
        .outerjoin(ClientAccount, ClientAccount.id == Booking.client_account_id)
        .outerjoin(Leg, Leg.id == Booking.leg_id)
        .where(Booking.status.in_(issuable_statuses))
        .order_by(Booking.created_at.desc())
        .limit(200)
    )
    rows = [
        {"booking": b, "client_name": company_name, "leg_code": leg_code}
        for b, company_name, leg_code in res.all()
    ]
    return templates.TemplateResponse(
        "staff/cargo/index.html",
        {"request": request, "user": user, "rows": rows},
    )


@router.get("/cargo/booking/{ref}", response_class=HTMLResponse)
async def cargo_booking_detail(
    request: Request,
    ref: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    booking = (
        await db.execute(select(Booking).where(Booking.reference == ref))
    ).scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    leg, vessel, pol, pod, client = await _load_booking_bundle(db, booking)
    return templates.TemplateResponse(
        "staff/cargo/booking_detail.html",
        {
            "request": request,
            "user": user,
            "booking": booking,
            "leg": leg,
            "vessel": vessel,
            "pol": pol,
            "pod": pod,
            "client": client,
        },
    )


# ---------------------------------------------------------------------------
# Staff PDF endpoints (all bookings)
# ---------------------------------------------------------------------------


@router.get("/cargo/booking/{ref}/packing-list.pdf")
async def staff_pl_pdf(
    ref: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> Response:
    return await _packing_response(db, ref)


@router.get("/cargo/booking/{ref}/invoice.pdf")
async def staff_invoice_pdf(
    ref: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> Response:
    return await _invoice_response(db, ref)


@router.get("/cargo/booking/{ref}/anemos.pdf")
async def staff_anemos_pdf(
    ref: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> Response:
    return await _co2_response(db, ref)


@router.get("/cargo/booking/{ref}/co2-certificate.pdf")
async def staff_co2_pdf_legacy(ref: str):
    """Backward-compat : ancien chemin → 301 vers anemos.pdf."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url=f"/cargo/booking/{ref}/anemos.pdf", status_code=301)


# ---------------------------------------------------------------------------
# Client PDF endpoints (owner-only)
# ---------------------------------------------------------------------------


@router.get("/me/bookings/{ref}/packing-list.pdf")
async def client_pl_pdf(
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await _packing_response(db, ref, owner_client_id=client.id)


@router.get("/me/bookings/{ref}/invoice.pdf")
async def client_invoice_pdf(
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await _invoice_response(db, ref, owner_client_id=client.id)


@router.get("/me/bookings/{ref}/booking-note.pdf")
async def client_booking_note_pdf(
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Booking note (COM-05) — confirme la réservation et ses conditions.

    La facturation est émise par la comptabilité NEWTOWT hors plateforme."""
    return await _booking_note_response(db, ref, owner_client_id=client.id)


@router.get("/me/bookings/{ref}/anemos.pdf")
async def client_anemos_pdf(
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Certificat Anemos (anciennement certificat CO₂) — PDF téléchargeable."""
    return await _co2_response(db, ref, owner_client_id=client.id)


@router.get("/me/bookings/{ref}/co2-certificate.pdf")
async def client_co2_pdf_legacy(ref: str):
    """Backward-compat : redirects 301 vers anemos.pdf."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(
        url=f"/me/bookings/{ref}/anemos.pdf",
        status_code=301,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_booking(db: AsyncSession, ref: str, owner_client_id: int | None = None) -> Booking:
    booking = (
        await db.execute(select(Booking).where(Booking.reference == ref))
    ).scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if owner_client_id is not None and booking.client_account_id != owner_client_id:
        raise HTTPException(status_code=404, detail="Not found")
    return booking


def _pdf_response(doc) -> Response:
    return Response(
        content=doc.pdf,
        media_type=doc.mime,
        headers={"Content-Disposition": f'inline; filename="{doc.filename}"'},
    )


def _docx_response(doc) -> Response:
    return Response(
        content=doc.docx,
        media_type=doc.mime,
        headers={"Content-Disposition": f'attachment; filename="{doc.filename}"'},
    )


async def _packing_response(db, ref, owner_client_id=None) -> Response:
    booking = await _get_booking(db, ref, owner_client_id)
    leg, vessel, pol, pod, client = await _load_booking_bundle(db, booking)
    doc = render_packing_list(
        booking=booking, leg=leg, vessel=vessel, pol=pol, pod=pod, client=client
    )
    return _pdf_response(doc)


async def _invoice_response(db, ref, owner_client_id=None) -> Response:
    booking = await _get_booking(db, ref, owner_client_id)
    leg, vessel, pol, pod, client = await _load_booking_bundle(db, booking)
    # Look for an existing ClientInvoice; if none, the PDF acts as a quote.
    from app.models.client_invoice import ClientInvoice

    invoice = (
        await db.execute(
            select(ClientInvoice)
            .where(ClientInvoice.booking_id == booking.id)
            .order_by(ClientInvoice.issued_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    doc = render_invoice(
        booking=booking,
        leg=leg,
        vessel=vessel,
        pol=pol,
        pod=pod,
        client=client,
        invoice=invoice,
    )
    return _pdf_response(doc)


async def _booking_note_response(db, ref, owner_client_id=None) -> Response:
    booking = await _get_booking(db, ref, owner_client_id)
    if booking.status in ("draft",):
        raise HTTPException(
            status_code=400,
            detail="Booking note not available until the booking is submitted",
        )
    leg, vessel, pol, pod, client = await _load_booking_bundle(db, booking)
    doc = render_booking_note(
        booking=booking, leg=leg, vessel=vessel, pol=pol, pod=pod, client=client
    )
    return _pdf_response(doc)


async def _co2_response(db, ref, owner_client_id=None) -> Response:
    booking = await _get_booking(db, ref, owner_client_id)
    leg, vessel, pol, pod, client = await _load_booking_bundle(db, booking)
    if booking.status not in ("discharged", "delivered"):
        raise HTTPException(
            status_code=400,
            detail="CO2 certificate is issued once the cargo is discharged",
        )
    distance = resolve_distance_nm(leg, pol, pod)
    from app.models.anemos_certificate import AnemosCertificate

    cert = (
        await db.execute(
            select(AnemosCertificate).where(AnemosCertificate.booking_id == booking.id)
        )
    ).scalar_one_or_none()
    # Équipage embarqué sur le voyage (leg) — figure sur le certificat Anemos.
    from app.services.crew_compliance import crew_for_leg

    crew = [
        {"full_name": m.full_name, "role": s.rank_label or m.role, "nationality": m.nationality}
        for s, m in await crew_for_leg(db, leg, vessel.id)
    ]
    doc = render_anemos_certificate(
        booking=booking,
        leg=leg,
        vessel=vessel,
        pol=pol,
        pod=pod,
        client=client,
        distance_nm=distance,
        certificate=cert,
        crew=crew,
    )
    return _pdf_response(doc)


# ---------------------------------------------------------------------------
# Rail packing list côté client — connaissements du booking
# ---------------------------------------------------------------------------
#
# Cf. `docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md` §4.1 et §5.4.
#
# ⚠️ Pourquoi ces routes existent. Le rail packing list produit **un
# connaissement par lot** (`PackingListBatch`), alors que l'URL client historique
# est au niveau du **booking** — un booking pouvant porter plusieurs lots, il n'y a
# pas de « le » BL du booking. D'où une **liste** puis un document par lot, et non
# une route unique.
#
# Ces routes sont aussi le **préalable au retrait du rail booking** (§5.4) : le
# retirer avant priverait le client de tout accès à son connaissement.
#
# Le contrôle de propriété passe par `_get_booking(..., owner_client_id=client.id)`
# qui renvoie **404** et non 403 pour un booking étranger : un 403 confirmerait
# l'existence de la référence.


async def _booking_batches_with_bl(db: AsyncSession, booking: Booking) -> list:
    """Lots du booking portant un BL, du plus ancien au plus récent."""
    from app.models.packing_list import PackingList, PackingListBatch

    stmt = (
        select(PackingListBatch)
        .join(PackingList, PackingListBatch.packing_list_id == PackingList.id)
        .where(PackingList.booking_id == booking.id)
        .where(PackingListBatch.bl_number.is_not(None))
        .order_by(PackingListBatch.bl_number)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _owned_batch_or_404(db: AsyncSession, booking: Booking, batch_id: int):
    """Le lot demandé appartient-il bien à CE booking ?

    Sans cette vérification, un client authentifié pourrait lire le connaissement
    d'un autre en devinant un `batch_id` — la référence de booking dans l'URL ne
    suffit pas, c'est le lot qui porte le document.
    """
    from app.models.packing_list import PackingList, PackingListBatch

    stmt = (
        select(PackingListBatch)
        .join(PackingList, PackingListBatch.packing_list_id == PackingList.id)
        .where(PackingList.booking_id == booking.id)
        .where(PackingListBatch.id == batch_id)
    )
    batch = (await db.execute(stmt)).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="Not found")
    return batch


@router.get("/me/bookings/{ref}/bls", response_class=HTMLResponse)
async def client_bl_list(
    ref: str,
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Connaissements du booking : état, document, action de validation."""
    from app.services import bl_delivery

    booking = await _get_booking(db, ref, owner_client_id=client.id)
    batches = await _booking_batches_with_bl(db, booking)
    # §5.1 — état de remise par lot. Calculé ici : un gabarit ne doit pas
    # déclencher de requêtes.
    delivery = {b.id: await bl_delivery.delivery_status(db, batch_id=b.id) for b in batches}
    return templates.TemplateResponse(
        request,
        "client/bl_list.html",
        {
            "booking": booking,
            "batches": batches,
            "client": client,
            "delivery": delivery,
            "number_of_originals": bl_delivery.NUMBER_OF_ORIGINALS,
        },
    )


@router.get("/me/bookings/{ref}/bl/{batch_id}.pdf")
async def client_batch_bl_pdf(
    ref: str,
    batch_id: int,
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Connaissement d'un lot.

    N'émet ni ne modifie le connaissement. Consigne en revanche l'**accès** dans le
    registre de remise (§5.1) quand le document est un original signé — un accès
    n'est jamais compté comme une réception.
    """
    from app.services import bl_workflow
    from app.services.packing_list import resolve_pl_context
    from app.services.pdf_generator import render_bill_of_lading_from_pl

    booking = await _get_booking(db, ref, owner_client_id=client.id)
    batch = await _owned_batch_or_404(db, booking, batch_id)
    if not batch.bl_number:
        raise HTTPException(status_code=404, detail="aucun connaissement pour ce lot")

    from app.models.packing_list import PackingList

    pl = await db.get(PackingList, batch.packing_list_id)
    if pl is None:
        # Ne devrait pas arriver (le lot a été trouvé PAR sa packing list), mais un
        # crash obscur sur une donnée incohérente vaut moins qu'un 404 explicite.
        raise HTTPException(status_code=404, detail="Not found")
    _o, _b, leg, vessel, pol, pod = await resolve_pl_context(db, pl)
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
        bl_number=batch.bl_number,
        issued_at=batch.bl_issued_at,
        shipped_on_board=sob,
    )

    # §5.1 — registre de remise. Consigner un ACCÈS sur un `GET` est assumé : c'est
    # le propre d'un journal d'accès, et son absence viderait le registre de son
    # intérêt. Deux garde-fous encadrent le risque :
    #  - un accès à un PROJET n'est pas consigné (aucun original n'existe encore) ;
    #  - un accès n'est JAMAIS compté comme une réception
    #    (`bl_delivery.has_client_acknowledgement` ignore ce canal).
    # Un préchargement de lien ne peut donc gonfler qu'un compteur de consultations,
    # jamais produire une preuve de remise.
    from app.services import bl_delivery

    await bl_delivery.record_download(
        db,
        batch=batch,
        client=client,
        ip=request.headers.get("x-forwarded-for")
        or (request.client.host if request.client else None),
    )
    return _pdf_response(doc)


@router.post("/me/bookings/{ref}/bl/{batch_id}/validate")
async def client_validate_bl(
    ref: str,
    batch_id: int,
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Validation du draft **par le client titulaire** du booking (§4.1).

    C'est bien le compte authentifié `/me` qui valide, **pas** le portail
    expéditeur `/p/{token}` : celui-ci est anonyme par conception et ne peut donc
    pas engager le client.

    Une modification ultérieure du contenu annulera cette validation et ramènera le
    BL à `draft` (règle de régression) — une validation porte sur un contenu précis.
    """
    from app.services import bl_workflow

    booking = await _get_booking(db, ref, owner_client_id=client.id)
    batch = await _owned_batch_or_404(db, booking, batch_id)
    try:
        await bl_workflow.validate_by_client(
            db,
            batch=batch,
            client=client,
            ip=request.headers.get("x-forwarded-for")
            or (request.client.host if request.client else None),
        )
    except bl_workflow.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _bl_mutation_response(
        request, f"/me/bookings/{ref}/bls", i18n_t("toast_bl_validated", client.language)
    )


@router.post("/me/bookings/{ref}/bl/{batch_id}/confirm-receipt")
async def client_confirm_bl_receipt(
    ref: str,
    batch_id: int,
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """§5.1 — le client **déclare** avoir reçu les originaux.

    C'est la preuve la plus forte du registre de remise, parce qu'elle vient du
    client lui-même. Refusée tant que le connaissement n'est pas signé : avant la
    signature aucun original n'existe, et confirmer la réception d'un projet
    n'établirait rien.
    """
    from app.services import bl_delivery

    booking = await _get_booking(db, ref, owner_client_id=client.id)
    batch = await _owned_batch_or_404(db, booking, batch_id)
    form = dict(await request.form())
    try:
        await bl_delivery.confirm_by_client(
            db,
            batch=batch,
            client=client,
            notes=str(form.get("notes") or ""),
            ip=request.headers.get("x-forwarded-for")
            or (request.client.host if request.client else None),
        )
    except bl_delivery.DeliveryReceiptError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _bl_mutation_response(
        request, f"/me/bookings/{ref}/bls", i18n_t("toast_bl_receipt_confirmed", client.language)
    )


@router.get("/me/bookings/{ref}/bl/{batch_id}.docx")
async def client_batch_bl_docx(
    ref: str,
    batch_id: int,
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """BL éditable (Word) d'un lot — remplace `/me/bookings/{ref}/bl.docx`.

    Même cloisonnement que la version PDF : le lot doit appartenir à CE booking, et
    l'accès à un original est consigné au registre de remise (§5.1).
    """
    from app.models.packing_list import PackingList
    from app.services import bl_delivery, bl_workflow
    from app.services.docx_generator import build_bill_of_lading_docx_from_pl
    from app.services.packing_list import resolve_pl_context

    booking = await _get_booking(db, ref, owner_client_id=client.id)
    batch = await _owned_batch_or_404(db, booking, batch_id)
    if not batch.bl_number:
        raise HTTPException(status_code=404, detail="aucun connaissement pour ce lot")

    pl = await db.get(PackingList, batch.packing_list_id)
    if pl is None:
        raise HTTPException(status_code=404, detail="Not found")
    _o, _b, leg, vessel, pol, pod = await resolve_pl_context(db, pl)
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
    await bl_delivery.record_download(
        db,
        batch=batch,
        client=client,
        ip=request.headers.get("x-forwarded-for")
        or (request.client.host if request.client else None),
    )
    return _docx_response(doc)
