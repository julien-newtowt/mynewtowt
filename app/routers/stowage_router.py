"""Stowage — plan d'arrimage 18 zones, suggestion auto et impression.

Vue principale : grille 3 ponts × 2 cales × 3 blocs avec affectations.
Algorithme de suggestion : services.stowage.suggest_assignments.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.commercial import Order
from app.models.leg import Leg
from app.models.packing_list import PackingListBatch
from app.models.stowage import (
    BLOCKS,
    DANGEROUS_ZONES,
    DECKS,
    HOLDS,
    ZONE_LOADING_ORDER,
    StowageItem,
    StowagePlan,
)
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.stowage import (
    _vessel_class_for_leg,
    evaluate_plan,
    gather_suggestion_items,
    locate_batch,
    locate_for_order,
    parse_zone,
    suggest_assignments,
    zone_label,
    zone_usage_summary,
)
from app.services.stowage_specs import get_specs
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stowage", tags=["stowage"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def stowage_index(
    request: Request,
    vessel: str | None = None,
    year: int | None = None,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    from app.services.leg_filter import build_leg_filter, set_leg_filter_cookie

    f = await build_leg_filter(db, vessel=vessel, year=year, leg_id=leg_id, request=request)
    plans = list(
        (await db.execute(select(StowagePlan).order_by(StowagePlan.updated_at.desc()).limit(50)))
        .scalars()
        .all()
    )
    legs_by_id: dict[int, Leg] = {}
    for p in plans:
        leg = await db.get(Leg, p.leg_id)
        if leg is not None:
            legs_by_id[p.leg_id] = leg
    response = templates.TemplateResponse(
        "staff/stowage/index.html",
        {
            "request": request,
            "user": user,
            "leg_filter_ctx": f,
            "plans": plans,
            "legs_by_id": legs_by_id,
        },
    )
    set_leg_filter_cookie(response, f)
    return response


@router.get("/legs/{leg_id}", response_class=HTMLResponse)
async def stowage_plan_view(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    # Renforcement : selectinload(StowagePlan.items) lit les colonnes packing-list
    # de stowage_items ajoutées par la migration 0037. Si elle n'est pas appliquée,
    # le SELECT échoue (500). On dégrade alors vers le plan seul + items vides
    # (savepoint isolé), comme stowage_specs.get_specs / _vessel_class_for_leg.
    try:
        async with db.begin_nested():
            plan = (
                await db.execute(
                    select(StowagePlan)
                    .options(selectinload(StowagePlan.items))
                    .where(StowagePlan.leg_id == leg_id)
                )
            ).scalar_one_or_none()
        items = list(plan.items) if (plan is not None and plan.items is not None) else []
    except (ProgrammingError, OperationalError):
        logger.warning(
            "stowage_items (colonnes 0037) indisponible — plan affiché sans item "
            "(migration 0037 non appliquée ?)"
        )
        plan = (
            await db.execute(select(StowagePlan).where(StowagePlan.leg_id == leg_id))
        ).scalar_one_or_none()
        items = []
    if plan is None:
        plan = StowagePlan(leg_id=leg_id, status="draft")
        db.add(plan)
        await db.flush()
    usage = zone_usage_summary(
        [
            {"zone": it.zone, "pallet_format": it.pallet_format, "pallet_count": it.pallet_count}
            for it in items
        ]
    )
    evaluation = await evaluate_plan(db, leg_id)
    return templates.TemplateResponse(
        "staff/stowage/plan.html",
        {
            "request": request,
            "user": user,
            "leg": leg,
            "plan": plan,
            "items": items,
            "zones": ZONE_LOADING_ORDER,
            "dangerous_zones": DANGEROUS_ZONES,
            "usage": usage,
            "evaluation": evaluation,
            "decks": DECKS,
            "holds": HOLDS,
            "blocks": BLOCKS,
            "ship_map_target": None,
        },
    )


@router.post("/plans/{plan_id}/items")
async def add_item(
    plan_id: int,
    request: Request,
    zone: str = Form(...),
    pallet_format: str = Form("EPAL"),
    pallet_count: int = Form(1),
    weight_kg: float | None = Form(None),
    is_dangerous: bool = Form(False),
    is_oversized: bool = Form(False),
    is_stacked: bool = Form(False),
    stackable: bool = Form(True),
    length_cm: float | None = Form(None),
    width_cm: float | None = Form(None),
    height_cm: float | None = Form(None),
    hs_code: str | None = Form(None),
    imdg_class: str | None = Form(None),
    notes: str | None = Form(None),
    order_id: int | None = Form(None),
    batch_id: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    plan = await db.get(StowagePlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404)
    if zone not in ZONE_LOADING_ORDER:
        raise HTTPException(status_code=400, detail="zone invalide")
    # STO-05 (A3) — blocage configurable : si le feature flag est actif, on
    # refuse un ajout qui dépasserait la capacité/charge de la zone (sinon
    # l'évaluation se contente d'un avertissement non bloquant).
    from app.services.feature_flags import is_enabled
    from app.services.stowage import STOWAGE_BLOCK_FLAG, check_zone_admission

    if await is_enabled(db, STOWAGE_BLOCK_FLAG, user_role=user.role):
        ok, reason = await check_zone_admission(
            db,
            plan.leg_id,
            zone,
            add_pallets=pallet_count,
            add_weight_kg=weight_kg,
            pallet_format=pallet_format,
        )
        if not ok:
            raise HTTPException(status_code=400, detail=reason)
    item = StowageItem(
        plan_id=plan_id,
        order_id=order_id,
        batch_id=batch_id,
        zone=zone,
        pallet_format=pallet_format,
        pallet_count=pallet_count,
        weight_kg=weight_kg,
        is_dangerous=is_dangerous,
        is_oversized=is_oversized,
        is_stacked=is_stacked,
        stackable=stackable,
        length_cm=length_cm,
        width_cm=width_cm,
        height_cm=height_cm,
        hs_code=hs_code,
        imdg_class=imdg_class,
        notes=notes,
    )
    db.add(item)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="cargo",
        entity_type="stowage_item",
        entity_id=item.id,
        entity_label=f"plan={plan_id} zone={zone}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/stowage/legs/{plan.leg_id}", status_code=303)


@router.post("/plans/{plan_id}/items/{item_id}/move")
async def move_item(
    plan_id: int,
    item_id: int,
    request: Request,
    new_zone: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    """STO-02 — déplace une palette d'une zone à l'autre (sans supprimer/recréer)."""
    item = await db.get(StowageItem, item_id)
    if item is None or item.plan_id != plan_id:
        raise HTTPException(status_code=404)
    if new_zone not in ZONE_LOADING_ORDER:
        raise HTTPException(status_code=400, detail="zone invalide")
    plan = await db.get(StowagePlan, plan_id)
    old_zone = item.zone
    item.zone = new_zone
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="cargo",
        entity_type="stowage_item",
        entity_id=item.id,
        entity_label=f"plan={plan_id} {old_zone}→{new_zone}",
        ip_address=_client_ip(request),
    )
    leg_id = plan.leg_id if plan else None
    return RedirectResponse(url=f"/stowage/legs/{leg_id}", status_code=303)


@router.post("/plans/{plan_id}/items/{item_id}/delete")
async def delete_item(
    plan_id: int,
    item_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "S")),
):
    """STO-03 — retire une affectation sans tout réécraser."""
    item = await db.get(StowageItem, item_id)
    if item is None or item.plan_id != plan_id:
        raise HTTPException(status_code=404)
    plan = await db.get(StowagePlan, plan_id)
    leg_id = plan.leg_id if plan else None
    await db.delete(item)
    await db.flush()
    await activity_record(
        db,
        action="delete",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="cargo",
        entity_type="stowage_item",
        entity_id=item_id,
        entity_label=f"plan={plan_id} item={item_id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/stowage/legs/{leg_id}", status_code=303)


@router.get("/onboard/{leg_id}", response_class=HTMLResponse)
async def stowage_onboard_view(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    """STO-01 — vue « à bord » du plan de chargement (lecture seule, perm captain)."""
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    plan = (
        await db.execute(
            select(StowagePlan)
            .options(selectinload(StowagePlan.items))
            .where(StowagePlan.leg_id == leg_id)
        )
    ).scalar_one_or_none()
    items = list(plan.items) if plan and plan.items else []
    usage = zone_usage_summary(
        [
            {"zone": it.zone, "pallet_format": it.pallet_format, "pallet_count": it.pallet_count}
            for it in items
        ]
    )
    return templates.TemplateResponse(
        "staff/stowage/onboard.html",
        {
            "request": request,
            "user": user,
            "leg": leg,
            "plan": plan,
            "items": items,
            "zones": ZONE_LOADING_ORDER,
            "dangerous_zones": DANGEROUS_ZONES,
            "usage": usage,
        },
    )


@router.post("/plans/{plan_id}/suggest")
async def suggest_plan(
    plan_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    """Génère des affectations en aspirant les batches PL des orders du leg.

    STO-09 — fallback : une commande sans batch de packing list (PL absente ou
    encore vide) est tout de même arrimée via un item placeholder issu de la
    réservation, pour permettre l'arrimage avant la saisie des documents cargo.
    """
    plan = await db.get(StowagePlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404)
    items_in = await gather_suggestion_items(db, plan.leg_id)
    # Capacités réelles de la classe du navire (référentiel d'arrimage).
    specs = await get_specs(db, await _vessel_class_for_leg(db, plan.leg_id))
    capacities = {zone: spec.get("capacity_epal") for zone, spec in specs.items()}
    placed = suggest_assignments(items_in, capacities=capacities)
    # Clear previous suggestions and re-add. Requête explicite plutôt que
    # ``plan.items`` (relation lazy → MissingGreenlet en contexte async quand le
    # plan vient d'une autre requête sans items eager-loadés).
    existing = list(
        (await db.execute(select(StowageItem).where(StowageItem.plan_id == plan.id)))
        .scalars()
        .all()
    )
    for it in existing:
        await db.delete(it)
    await db.flush()
    for p in placed:
        if p.get("zone") in ("OVERFLOW", None):
            continue
        db.add(
            StowageItem(
                plan_id=plan.id,
                order_id=p.get("order_id"),
                batch_id=p.get("batch_id"),
                zone=p["zone"],
                pallet_format=p["pallet_format"],
                pallet_count=p["pallet_count"],
                weight_kg=p.get("weight_kg"),
                description=p.get("description"),
                hs_code=p.get("hs_code"),
                imdg_class=p.get("imdg_class"),
                un_number=p.get("un_number"),
                length_cm=p.get("length_cm"),
                width_cm=p.get("width_cm"),
                height_cm=p.get("height_cm"),
                cubage_m3=p.get("cubage_m3"),
                stackable=p.get("stackable", True),
                is_dangerous=p.get("is_dangerous", False),
                is_oversized=p.get("is_oversized", False),
            )
        )
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="cargo",
        entity_type="stowage_plan",
        entity_id=plan.id,
        entity_label=f"leg={plan.leg_id}",
        detail="auto-suggest",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/stowage/legs/{plan.leg_id}", status_code=303)


@router.post("/plans/{plan_id}/approve")
async def approve_plan(
    plan_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "M")),
):
    plan = await db.get(StowagePlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404)
    plan.status = "approved"
    plan.approved_at = datetime.now(UTC)
    plan.approved_by = user.full_name or user.username
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="cargo",
        entity_type="stowage_plan",
        entity_id=plan.id,
        entity_label=f"leg={plan.leg_id}",
        detail="approved",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/stowage/legs/{plan.leg_id}", status_code=303)


@router.get("/legs/{leg_id}/plan.pdf")
async def stowage_plan_pdf(
    leg_id: int,
    request: Request,
    lang: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
):
    """Plan d'arrimage imprimable (WeasyPrint), style onepager.

    STO-06 — bilingue FR/EN : ``?lang=en`` (ou la langue de l'utilisateur)
    produit un plan communicable à un équipage / port étranger.
    """
    from fastapi.responses import Response
    from weasyprint import HTML  # local import — heavy native deps

    from app.config import settings
    from app.i18n import get_lang_from_request
    from app.models.port import Port
    from app.models.vessel import Vessel
    from app.services.stowage import stowage_pdf_labels
    from app.templating import brand_for_lang

    # Langue : query explicite (?lang=en) sinon préférence utilisateur / requête.
    pdf_lang = (lang or get_lang_from_request(request, user) or "fr").lower()
    if pdf_lang not in ("fr", "en"):
        pdf_lang = "fr"  # plan bilingue FR/EN uniquement (STO-06)

    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    plan = (
        await db.execute(
            select(StowagePlan)
            .options(selectinload(StowagePlan.items))
            .where(StowagePlan.leg_id == leg_id)
        )
    ).scalar_one_or_none()
    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id else None
    pol = await db.get(Port, leg.departure_port_id) if leg.departure_port_id else None
    pod = await db.get(Port, leg.arrival_port_id) if leg.arrival_port_id else None
    evaluation = await evaluate_plan(db, leg_id)

    tpl = templates.get_template("pdf/stowage_plan.html")
    html = tpl.render(
        leg=leg,
        vessel=vessel,
        pol=pol,
        pod=pod,
        plan=plan,
        items=(plan.items if plan else []),
        evaluation=evaluation,
        decks=DECKS,
        holds=HOLDS,
        blocks=BLOCKS,
        zone_label=zone_label,
        parse_zone=parse_zone,
        lang=pdf_lang,
        labels=stowage_pdf_labels(pdf_lang),
        brand=brand_for_lang(pdf_lang),
        issued_at=datetime.now(UTC),
        site_url=settings.site_url,
    )
    pdf = HTML(string=html, base_url=settings.site_url).write_pdf()
    code = leg.leg_code or leg_id
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="arrimage_{code}.pdf"'},
    )


@router.get("/locate/batch/{batch_id}", response_class=HTMLResponse)
async def locate_batch_view(
    batch_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    """Repérage visuel : où se trouve un lot (batch) à bord du navire."""
    positions = await locate_batch(db, batch_id)
    batch = await db.get(PackingListBatch, batch_id)
    leg = None
    evaluation = None
    if positions:
        leg = await db.get(Leg, positions[0]["leg_id"])
        if leg is not None:
            evaluation = await evaluate_plan(db, leg.id)
    target_zones = {p["zone"] for p in positions}
    return templates.TemplateResponse(
        "staff/stowage/locate.html",
        {
            "request": request,
            "user": user,
            "positions": positions,
            "target_zones": target_zones,
            "batch": batch,
            "order": None,
            "leg": leg,
            "evaluation": evaluation,
            "decks": DECKS,
            "holds": HOLDS,
            "blocks": BLOCKS,
            "title": f"Lot #{batch_id}",
        },
    )


@router.get("/locate/order/{order_id}", response_class=HTMLResponse)
async def locate_order_view(
    order_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    """Repérage visuel : positions à bord des lots d'une commande."""
    positions = await locate_for_order(db, order_id)
    order = await db.get(Order, order_id)
    leg = None
    evaluation = None
    if positions:
        leg = await db.get(Leg, positions[0]["leg_id"])
        if leg is not None:
            evaluation = await evaluate_plan(db, leg.id)
    target_zones = {p["zone"] for p in positions}
    return templates.TemplateResponse(
        "staff/stowage/locate.html",
        {
            "request": request,
            "user": user,
            "positions": positions,
            "target_zones": target_zones,
            "batch": None,
            "order": order,
            "leg": leg,
            "evaluation": evaluation,
            "decks": DECKS,
            "holds": HOLDS,
            "blocks": BLOCKS,
            "title": f"Commande #{order_id}",
        },
    )


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )
