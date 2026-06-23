"""Claims — sinistres cargo / crew / hull / war_risk / third_party.

Workflow status :
  open → in_review → provisioned → settled (ou rejected) → closed
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.booking import Booking
from app.models.claim import (
    CLAIM_DOC_TYPES,
    CLAIM_STATUSES,
    CLAIM_TYPES,
    Claim,
    ClaimDocument,
    ClaimProvisionHistory,
    ClaimTimelineEntry,
)
from app.models.crew import CrewMember
from app.models.insurance import InsuranceContract
from app.models.leg import Leg
from app.models.sof_event import SofEvent
from app.permissions import require_permission
from app.services import notifications, safe_files
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
    # Booking Notes (BN) — réservations récentes, pour rattacher le claim.
    bookings = list(
        (await db.execute(select(Booking).order_by(Booking.created_at.desc()).limit(200)))
        .scalars()
        .all()
    )
    # Si un leg est présélectionné (query ?leg_id=), on pré-charge ses zones
    # d'arrimage pour le picker de position cale (claim cargo). Sinon liste
    # vide : l'opérateur choisit d'abord un leg puis ré-ouvre/édite.
    stowage_zones = await _stowage_zones_for(db, leg_id)
    contracts = await _active_contracts(db)
    # ONB-06 — marins actifs, pour rattacher un sinistre équipage à une personne.
    crew_members = list(
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
    return templates.TemplateResponse(
        "staff/claims/new.html",
        {
            "request": request,
            "user": user,
            "legs": legs,
            "leg_options": leg_options,
            "bookings": bookings,
            "claim_types": CLAIM_TYPES,
            "selected_leg_id": leg_id,
            "stowage_zones": stowage_zones,
            "contracts": contracts,
            "crew_members": crew_members,
        },
    )


async def _active_contracts(db: AsyncSession) -> list[InsuranceContract]:
    """Contrats d'assurance actifs, pour le sélecteur de lien assureur."""
    return list(
        (
            await db.execute(
                select(InsuranceContract)
                .where(InsuranceContract.is_active.is_(True))
                .order_by(InsuranceContract.reference)
            )
        )
        .scalars()
        .all()
    )


async def _auto_cargo_position(
    db: AsyncSession, *, leg_id: int | None, booking_id: int | None
) -> str | None:
    """Récupère best-effort la zone d'arrimage depuis le plan de chargement.

    Pour un sinistre cargo lié à un booking, on tente de localiser la
    marchandise (via la commande rattachée) dans le plan d'arrimage du leg —
    conformément à la spec V2 (« position récupérée automatiquement depuis le
    plan de chargement »). Best-effort : renvoie None si rien de net.
    """
    if not booking_id:
        return None
    try:
        from app.models.commercial import Order
        from app.services.stowage import locate_for_order

        order = (
            (
                await db.execute(select(Order).where(Order.booking_id == booking_id).limit(1))
            ).scalar_one_or_none()
            if hasattr(Order, "booking_id")
            else None
        )
        if order is None:
            return None
        spots = await locate_for_order(db, order.id)
        zones = {s.get("zone") for s in spots if s.get("zone")}
        # Position nette uniquement si la marchandise est dans une seule zone.
        if len(zones) == 1:
            return next(iter(zones))
    except Exception:
        return None
    return None


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
    crew_member_id: int | None = Form(None),
    provision_eur: float | None = Form(None),
    insurer: str | None = Form(None),
    insurer_claim_ref: str | None = Form(None),
    insurance_contract_id: int | None = Form(None),
    cargo_position: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "M")),
):
    if claim_type not in CLAIM_TYPES:
        raise HTTPException(status_code=400, detail="invalid claim_type")
    # Lien assureur structuré : si un contrat est choisi, on pré-remplit le nom
    # de l'assureur depuis le contrat (le texte libre reste un repli).
    contract = (
        await db.get(InsuranceContract, insurance_contract_id) if insurance_contract_id else None
    )
    if contract is not None and not (insurer or "").strip():
        insurer = contract.insurer
    # Position cale : pour un claim cargo lié à un leg, la valeur provient en
    # principe du picker (zones du plan d'arrimage). On normalise et on reste
    # tolérant — une valeur hors plan est conservée en texte libre (cf. mission
    # FLX-10 : le claim n'a pas de lien batch direct, la zone est choisie par
    # l'opérateur dans le plan du leg, pas auto-résolue).
    cargo_position = (cargo_position or "").strip() or None
    # E3 — auto-résolution depuis le plan de chargement si non renseignée.
    if cargo_position is None and claim_type == "cargo":
        cargo_position = await _auto_cargo_position(db, leg_id=leg_id, booking_id=booking_id)
    # Sequence reference CLM-YYYY-NNNN
    year = datetime.now(UTC).year
    seq = (
        (await db.scalar(select(func.count(Claim.id)).where(Claim.reference.like(f"CLM-{year}-%"))))
        or 0
    ) + 1
    ref = f"CLM-{year}-{seq:04d}"
    occurred_dt = datetime.fromisoformat(occurred_at)
    c = Claim(
        reference=ref,
        claim_type=claim_type,
        leg_id=leg_id,
        booking_id=booking_id,
        crew_member_id=crew_member_id,
        title=title.strip(),
        description=description,
        status="open",
        occurred_at=occurred_dt,
        provision_eur=Decimal(str(provision_eur)) if provision_eur else None,
        insurer=(insurer or None),
        insurer_claim_ref=(insurer_claim_ref or "").strip() or None,
        insurance_contract_id=insurance_contract_id,
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
    # ONB-06 — SOF auto : un sinistre rattaché à un leg pose un événement
    # « CLAIM_DECLARED » au Statement of Facts (traçabilité réglementaire).
    if leg_id is not None:
        db.add(
            SofEvent(
                leg_id=leg_id,
                event_type="CLAIM_DECLARED",
                label=f"Sinistre déclaré : {ref} — {title.strip()}",
                occurred_at=occurred_dt,
                recorded_by_id=user.id,
                recorded_by_name=user.full_name or user.username,
            )
        )
    # E6 — trace la provision initiale dans l'historique.
    if c.provision_eur is not None:
        db.add(
            ClaimProvisionHistory(
                claim_id=c.id,
                amount_eur=c.provision_eur,
                reason="Provision initiale",
                author_id=user.id,
                author_name=user.full_name or user.username,
            )
        )
    await db.flush()
    # E7 — notification du gestionnaire sinistres (rôle manager_maritime).
    await notifications.create(
        db,
        type="new_claim",
        title=f"Nouveau sinistre {ref}",
        detail=f"{claim_type} — {title}",
        link=f"/claims/{c.id}",
        target_role="manager_maritime",
    )
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


@router.get("/{claim_id:int}", response_class=HTMLResponse)
async def claim_detail(
    claim_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "C")),
) -> HTMLResponse:
    claim = (
        await db.execute(
            select(Claim)
            .options(
                selectinload(Claim.timeline),
                selectinload(Claim.documents),
                selectinload(Claim.provision_history),
            )
            .where(Claim.id == claim_id)
        )
    ).scalar_one_or_none()
    if claim is None:
        raise HTTPException(status_code=404)
    leg = await db.get(Leg, claim.leg_id) if claim.leg_id else None
    booking = await db.get(Booking, claim.booking_id) if claim.booking_id else None
    crew_member = await db.get(CrewMember, claim.crew_member_id) if claim.crew_member_id else None
    contract = (
        await db.get(InsuranceContract, claim.insurance_contract_id)
        if claim.insurance_contract_id
        else None
    )
    contracts = await _active_contracts(db)
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
            "crew_member": crew_member,
            "contract": contract,
            "contracts": contracts,
            "statuses": CLAIM_STATUSES,
            "doc_types": CLAIM_DOC_TYPES,
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
    # E7 — notifie le gestionnaire des étapes clés du cycle de vie.
    if new_status in ("provisioned", "settled", "rejected"):
        await notifications.create(
            db,
            type="new_claim",
            title=f"Sinistre {c.reference} — {new_status}",
            detail=c.title,
            link=f"/claims/{c.id}",
            target_role="manager_maritime",
        )
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


# ---------------------------------------------------------------------------
# E6 — Provision : révision tracée
# ---------------------------------------------------------------------------


@router.post("/{claim_id}/provision")
async def claim_update_provision(
    claim_id: int,
    request: Request,
    provision_eur: float | None = Form(None),
    reason: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "M")),
):
    """Révise la provision d'un sinistre et journalise la révision (montant + motif)."""
    c = await db.get(Claim, claim_id)
    if c is None:
        raise HTTPException(status_code=404)
    new_amount = Decimal(str(provision_eur)) if provision_eur is not None else None
    old_amount = c.provision_eur
    if new_amount == old_amount:
        return RedirectResponse(url=f"/claims/{claim_id}", status_code=303)
    c.provision_eur = new_amount
    db.add(
        ClaimProvisionHistory(
            claim_id=c.id,
            amount_eur=new_amount,
            reason=(reason or "").strip() or None,
            author_id=user.id,
            author_name=user.full_name or user.username,
        )
    )
    db.add(
        ClaimTimelineEntry(
            claim_id=c.id,
            author_id=user.id,
            author_name=user.full_name or user.username,
            kind="provision",
            body=f"Provision : {old_amount or '—'} € → {new_amount or '—'} €"
            + (f" ({reason})" if reason else ""),
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
        detail=f"provision {old_amount or '—'} → {new_amount or '—'}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/claims/{claim_id}", status_code=303)


# ---------------------------------------------------------------------------
# E2 — Lien assureur (contrat d'assurance)
# ---------------------------------------------------------------------------


@router.post("/{claim_id}/insurer")
async def claim_update_insurer(
    claim_id: int,
    request: Request,
    insurance_contract_id: int | None = Form(None),
    insurer: str | None = Form(None),
    insurer_claim_ref: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "M")),
):
    """Met à jour le lien assureur d'un sinistre (contrat structuré + références)."""
    c = await db.get(Claim, claim_id)
    if c is None:
        raise HTTPException(status_code=404)
    c.insurance_contract_id = insurance_contract_id
    contract = (
        await db.get(InsuranceContract, insurance_contract_id) if insurance_contract_id else None
    )
    c.insurer = (insurer or "").strip() or (contract.insurer if contract else None)
    c.insurer_claim_ref = (insurer_claim_ref or "").strip() or None
    await db.flush()
    return RedirectResponse(url=f"/claims/{claim_id}", status_code=303)


# ---------------------------------------------------------------------------
# E1 — Pièces jointes (factures, expertises…)
# ---------------------------------------------------------------------------


@router.post("/{claim_id}/documents")
async def claim_upload_document(
    claim_id: int,
    request: Request,
    doc_type: str = Form("autre"),
    label: str | None = Form(None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "M")),
):
    c = await db.get(Claim, claim_id)
    if c is None:
        raise HTTPException(status_code=404)
    if doc_type not in CLAIM_DOC_TYPES:
        doc_type = "autre"
    raw = await file.read()
    try:
        rel_path, mime = safe_files.save_upload(
            raw, file.filename or "piece", subdir=f"claims/{claim_id}"
        )
    except safe_files.UploadRejected as e:
        raise HTTPException(status_code=400, detail=f"Fichier refusé : {e}") from e
    db.add(
        ClaimDocument(
            claim_id=c.id,
            doc_type=doc_type,
            label=(label or file.filename or "").strip() or None,
            file_path=rel_path,
            file_mime=mime,
            uploaded_by=user.full_name or user.username,
        )
    )
    db.add(
        ClaimTimelineEntry(
            claim_id=c.id,
            author_id=user.id,
            author_name=user.full_name or user.username,
            kind="document",
            body=f"Pièce ajoutée ({doc_type}) : {label or file.filename}",
        )
    )
    await db.flush()
    return RedirectResponse(url=f"/claims/{claim_id}", status_code=303)


@router.get("/{claim_id}/documents/{doc_id}")
async def claim_download_document(
    claim_id: int,
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "C")),
):
    doc = await db.get(ClaimDocument, doc_id)
    if doc is None or doc.claim_id != claim_id or not doc.file_path:
        raise HTTPException(status_code=404)
    try:
        path = safe_files.resolve_path(doc.file_path)
    except (safe_files.UploadRejected, FileNotFoundError) as e:
        raise HTTPException(status_code=404) from e
    return Response(
        content=path.read_bytes(),
        media_type=doc.file_mime or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{doc.label or path.name}"'},
    )


@router.post("/{claim_id}/documents/{doc_id}/delete")
async def claim_delete_document(
    claim_id: int,
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "S")),
):
    doc = await db.get(ClaimDocument, doc_id)
    if doc is None or doc.claim_id != claim_id:
        raise HTTPException(status_code=404)
    await db.delete(doc)
    await db.flush()
    return RedirectResponse(url=f"/claims/{claim_id}", status_code=303)


# ---------------------------------------------------------------------------
# E8 — Reporting / KPI sinistres
# ---------------------------------------------------------------------------


async def _claims_stats(db: AsyncSession) -> dict:
    """Agrégats sinistralité : par type, par statut, provisions/règlements, délai."""
    claims = list((await db.execute(select(Claim))).scalars().all())
    by_type: dict[str, int] = dict.fromkeys(CLAIM_TYPES, 0)
    by_status: dict[str, int] = dict.fromkeys(CLAIM_STATUSES, 0)
    total_provision = Decimal("0")
    total_settled = Decimal("0")
    settle_days: list[float] = []
    for c in claims:
        by_type[c.claim_type] = by_type.get(c.claim_type, 0) + 1
        by_status[c.status] = by_status.get(c.status, 0) + 1
        if c.provision_eur:
            total_provision += c.provision_eur
        if c.settled_eur:
            total_settled += c.settled_eur
        if c.settled_at and c.declared_at:
            settle_days.append((c.settled_at - c.declared_at).total_seconds() / 86400.0)
    avg_settle = round(sum(settle_days) / len(settle_days), 1) if settle_days else None
    return {
        "total": len(claims),
        "by_type": by_type,
        "by_status": by_status,
        "total_provision": total_provision,
        "total_settled": total_settled,
        "avg_settle_days": avg_settle,
    }


@router.get("/stats", response_class=HTMLResponse)
async def claims_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "C")),
) -> HTMLResponse:
    stats = await _claims_stats(db)
    return templates.TemplateResponse(
        "staff/claims/stats.html",
        {"request": request, "user": user, "stats": stats},
    )


@router.get("/stats.csv")
async def claims_stats_csv(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "C")),
) -> Response:
    import csv
    import io

    stats = await _claims_stats(db)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["indicateur", "valeur"])
    w.writerow(["total_sinistres", stats["total"]])
    for t, n in stats["by_type"].items():
        w.writerow([f"type_{t}", n])
    for s, n in stats["by_status"].items():
        w.writerow([f"statut_{s}", n])
    w.writerow(["provision_totale_eur", stats["total_provision"]])
    w.writerow(["regle_total_eur", stats["total_settled"]])
    w.writerow(["delai_moyen_reglement_j", stats["avg_settle_days"] or ""])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="claims_stats.csv"'},
    )


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )
