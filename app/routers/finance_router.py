"""Finance module — LegFinance, OPEX parameters, port configs.

Routes
------
GET  /finance                        → finance_index
GET  /finance/legs/{leg_id}/edit     → finance_leg_edit_form
POST /finance/legs/{leg_id}          → finance_leg_upsert
GET  /finance/opex                   → finance_opex_list
POST /finance/opex                   → finance_opex_create
POST /finance/opex/{param_id}/edit   → finance_opex_edit
POST /finance/opex/{param_id}/delete → finance_opex_delete
GET  /finance/ports                  → finance_ports_list
POST /finance/ports/{port_id}        → finance_port_config_upsert

Permissions:
  C = data_analyst + administrateur
  M = data_analyst + administrateur
  S = data_analyst + administrateur
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.finance import LegFinance, OpexParameter, PortConfig
from app.models.leg import Leg
from app.models.port import Port
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.templating import templates

router = APIRouter(prefix="/finance", tags=["finance"])


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _to_decimal(v: str | None, default: Decimal = Decimal("0")) -> Decimal:
    """Convert a form string to Decimal; return *default* when blank or invalid."""
    if v and v.strip():
        try:
            return Decimal(v.strip())
        except InvalidOperation:
            pass
    return default


def _to_decimal_or_none(v: str | None) -> Decimal | None:
    """Convert a form string to Decimal; return None when blank or invalid."""
    if v and v.strip():
        try:
            return Decimal(v.strip())
        except InvalidOperation:
            pass
    return None


def _client_ip(request: Request) -> str | None:
    return (
        request.headers.get("x-forwarded-for")
        or (request.client.host if request.client else None)
    )


# ──────────────────────────────────────────────────────────────
# 1. Index — overview of all LegFinance + OpexParameter
# ──────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def finance_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("finance", "C")),
) -> HTMLResponse:
    # All LegFinance rows (used in the table)
    finances: list[LegFinance] = list(
        (await db.execute(select(LegFinance))).scalars().all()
    )

    # Build a leg_code map so the template can label each row
    leg_ids = {f.leg_id for f in finances}
    leg_map: dict[int, Leg] = {}
    for lid in leg_ids:
        leg = await db.get(Leg, lid)
        if leg:
            leg_map[lid] = leg

    # All OPEX parameters (for the summary panel)
    opex: list[OpexParameter] = list(
        (
            await db.execute(
                select(OpexParameter).order_by(
                    OpexParameter.category, OpexParameter.parameter_name
                )
            )
        )
        .scalars()
        .all()
    )

    # All legs (for the "add LegFinance" dropdown — exclude already-linked ones)
    all_legs: list[Leg] = list(
        (
            await db.execute(select(Leg).order_by(Leg.etd.desc()))
        )
        .scalars()
        .all()
    )
    linked_leg_ids = {f.leg_id for f in finances}
    legs = [leg for leg in all_legs if leg.id not in linked_leg_ids]

    # Aggregated totals
    total_revenue = sum((f.revenue_eur for f in finances), Decimal("0"))
    total_costs = sum(
        (
            f.port_fees_eur + f.docker_costs_eur + f.opex_share_eur + f.other_costs_eur
            for f in finances
        ),
        Decimal("0"),
    )
    total_margin = sum((f.margin_eur for f in finances), Decimal("0"))

    return templates.TemplateResponse(
        "staff/finance/index.html",
        {
            "request": request,
            "user": user,
            "finances": finances,
            "leg_map": leg_map,
            "opex": opex,
            "legs": legs,
            "total_revenue_eur": total_revenue,
            "total_costs_eur": total_costs,
            "total_margin_eur": total_margin,
        },
    )


# ──────────────────────────────────────────────────────────────
# 2. LegFinance edit form
# ──────────────────────────────────────────────────────────────

@router.get("/legs/{leg_id}/edit", response_class=HTMLResponse)
async def finance_leg_edit_form(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("finance", "M")),
) -> HTMLResponse:
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404, detail="Leg not found")

    result = await db.execute(
        select(LegFinance).where(LegFinance.leg_id == leg_id)
    )
    finance: LegFinance | None = result.scalar_one_or_none()

    return templates.TemplateResponse(
        "staff/finance/leg_edit.html",
        {
            "request": request,
            "user": user,
            "finance": finance,
            "leg": leg,
        },
    )


# ──────────────────────────────────────────────────────────────
# 3. LegFinance upsert
# ──────────────────────────────────────────────────────────────

@router.post("/legs/{leg_id}")
async def finance_leg_upsert(
    leg_id: int,
    request: Request,
    revenue_eur: str | None = Form(None),
    port_fees_eur: str | None = Form(None),
    docker_costs_eur: str | None = Form(None),
    opex_share_eur: str | None = Form(None),
    other_costs_eur: str | None = Form(None),
    notes: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("finance", "M")),
) -> RedirectResponse:
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404, detail="Leg not found")

    rev = _to_decimal(revenue_eur)
    port = _to_decimal(port_fees_eur)
    docker = _to_decimal(docker_costs_eur)
    opex_s = _to_decimal(opex_share_eur)
    other = _to_decimal(other_costs_eur)
    margin = rev - port - docker - opex_s - other

    result = await db.execute(
        select(LegFinance).where(LegFinance.leg_id == leg_id)
    )
    finance: LegFinance | None = result.scalar_one_or_none()

    if finance is None:
        finance = LegFinance(leg_id=leg_id)
        db.add(finance)

    finance.revenue_eur = rev
    finance.port_fees_eur = port
    finance.docker_costs_eur = docker
    finance.opex_share_eur = opex_s
    finance.other_costs_eur = other
    finance.margin_eur = margin
    finance.notes = notes or None

    await db.flush()

    await activity_record(
        db,
        action="finance_leg_upsert",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="finance",
        entity_type="leg_finance",
        entity_id=finance.id,
        entity_label=leg.leg_code,
        detail=f"leg_id={leg_id} revenue={rev} margin={margin}",
        ip_address=_client_ip(request),
    )

    return RedirectResponse(url="/finance", status_code=303)


# ──────────────────────────────────────────────────────────────
# 4. OPEX list
# ──────────────────────────────────────────────────────────────

@router.get("/opex", response_class=HTMLResponse)
async def finance_opex_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("finance", "C")),
) -> HTMLResponse:
    opex_params: list[OpexParameter] = list(
        (
            await db.execute(
                select(OpexParameter).order_by(
                    OpexParameter.category, OpexParameter.parameter_name
                )
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        "staff/finance/opex.html",
        {
            "request": request,
            "user": user,
            "opex_params": opex_params,
        },
    )


# ──────────────────────────────────────────────────────────────
# 5. OPEX create
# ──────────────────────────────────────────────────────────────

@router.post("/opex")
async def finance_opex_create(
    request: Request,
    parameter_name: str = Form(...),
    parameter_value: str = Form(...),
    unit: str | None = Form(None),
    category: str | None = Form(None),
    description: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("finance", "M")),
) -> RedirectResponse:
    value = _to_decimal(parameter_value)
    param = OpexParameter(
        parameter_name=parameter_name.strip(),
        parameter_value=value,
        unit=unit.strip() if unit and unit.strip() else None,
        category=category.strip() if category and category.strip() else None,
        description=description.strip() if description and description.strip() else None,
    )
    db.add(param)
    await db.flush()

    await activity_record(
        db,
        action="opex_create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="finance",
        entity_type="opex_parameter",
        entity_id=param.id,
        entity_label=param.parameter_name,
        detail=f"value={value} unit={unit} category={category}",
        ip_address=_client_ip(request),
    )

    return RedirectResponse(url="/finance/opex", status_code=303)


# ──────────────────────────────────────────────────────────────
# 6. OPEX edit
# ──────────────────────────────────────────────────────────────

@router.post("/opex/{param_id}/edit")
async def finance_opex_edit(
    param_id: int,
    request: Request,
    parameter_name: str = Form(...),
    parameter_value: str = Form(...),
    unit: str | None = Form(None),
    category: str | None = Form(None),
    description: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("finance", "M")),
) -> RedirectResponse:
    param = await db.get(OpexParameter, param_id)
    if param is None:
        raise HTTPException(status_code=404, detail="OpexParameter not found")

    param.parameter_name = parameter_name.strip()
    param.parameter_value = _to_decimal(parameter_value)
    param.unit = unit.strip() if unit and unit.strip() else None
    param.category = category.strip() if category and category.strip() else None
    param.description = description.strip() if description and description.strip() else None

    await db.flush()

    await activity_record(
        db,
        action="opex_edit",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="finance",
        entity_type="opex_parameter",
        entity_id=param.id,
        entity_label=param.parameter_name,
        detail=f"value={param.parameter_value} unit={param.unit} category={param.category}",
        ip_address=_client_ip(request),
    )

    return RedirectResponse(url="/finance/opex", status_code=303)


# ──────────────────────────────────────────────────────────────
# 7. OPEX delete
# ──────────────────────────────────────────────────────────────

@router.post("/opex/{param_id}/delete")
async def finance_opex_delete(
    param_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("finance", "S")),
) -> RedirectResponse:
    param = await db.get(OpexParameter, param_id)
    if param is None:
        raise HTTPException(status_code=404, detail="OpexParameter not found")

    label = param.parameter_name
    await db.delete(param)
    await db.flush()

    await activity_record(
        db,
        action="opex_delete",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="finance",
        entity_type="opex_parameter",
        entity_id=param_id,
        entity_label=label,
        ip_address=_client_ip(request),
    )

    return RedirectResponse(url="/finance/opex", status_code=303)


# ──────────────────────────────────────────────────────────────
# 8. Port configs list
# ──────────────────────────────────────────────────────────────

@router.get("/ports", response_class=HTMLResponse)
async def finance_ports_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("finance", "C")),
) -> HTMLResponse:
    ports: list[Port] = list(
        (
            await db.execute(select(Port).order_by(Port.name))
        )
        .scalars()
        .all()
    )

    all_configs: list[PortConfig] = list(
        (await db.execute(select(PortConfig))).scalars().all()
    )
    configs: dict[int, PortConfig] = {pc.port_id: pc for pc in all_configs}

    return templates.TemplateResponse(
        "staff/finance/port_config.html",
        {
            "request": request,
            "user": user,
            "ports": ports,
            "configs": configs,
        },
    )


# ──────────────────────────────────────────────────────────────
# 9. Port config upsert
# ──────────────────────────────────────────────────────────────

@router.post("/ports/{port_id}")
async def finance_port_config_upsert(
    port_id: int,
    request: Request,
    agency_fee_eur: str | None = Form(None),
    pilot_fee_eur: str | None = Form(None),
    berth_fee_per_day_eur: str | None = Form(None),
    docker_fee_per_palette_eur: str | None = Form(None),
    notes: str | None = Form(None),
    agent_name: str | None = Form(None),
    agent_phone: str | None = Form(None),
    agent_email: str | None = Form(None),
    pilot_vhf_channel: str | None = Form(None),
    pilot_phone: str | None = Form(None),
    port_control_vhf_channel: str | None = Form(None),
    documents_required: str | None = Form(None),
    restrictions: str | None = Form(None),
    notes_for_captain: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("finance", "M")),
) -> RedirectResponse:
    port = await db.get(Port, port_id)
    if port is None:
        raise HTTPException(status_code=404, detail="Port not found")

    result = await db.execute(
        select(PortConfig).where(PortConfig.port_id == port_id)
    )
    config: PortConfig | None = result.scalar_one_or_none()

    if config is None:
        config = PortConfig(port_id=port_id)
        db.add(config)

    config.agency_fee_eur = _to_decimal_or_none(agency_fee_eur)
    config.pilot_fee_eur = _to_decimal_or_none(pilot_fee_eur)
    config.berth_fee_per_day_eur = _to_decimal_or_none(berth_fee_per_day_eur)
    config.docker_fee_per_palette_eur = _to_decimal_or_none(docker_fee_per_palette_eur)
    config.notes = notes.strip() if notes and notes.strip() else None
    config.agent_name = agent_name.strip() if agent_name and agent_name.strip() else None
    config.agent_phone = agent_phone.strip() if agent_phone and agent_phone.strip() else None
    config.agent_email = agent_email.strip() if agent_email and agent_email.strip() else None
    config.pilot_vhf_channel = pilot_vhf_channel.strip() if pilot_vhf_channel and pilot_vhf_channel.strip() else None
    config.pilot_phone = pilot_phone.strip() if pilot_phone and pilot_phone.strip() else None
    config.port_control_vhf_channel = port_control_vhf_channel.strip() if port_control_vhf_channel and port_control_vhf_channel.strip() else None
    config.documents_required = documents_required.strip() if documents_required and documents_required.strip() else None
    config.restrictions = restrictions.strip() if restrictions and restrictions.strip() else None
    config.notes_for_captain = notes_for_captain.strip() if notes_for_captain and notes_for_captain.strip() else None

    await db.flush()

    await activity_record(
        db,
        action="port_config_upsert",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="finance",
        entity_type="port_config",
        entity_id=config.id,
        entity_label=f"{port.locode} {port.name}",
        detail=f"port_id={port_id}",
        ip_address=_client_ip(request),
    )

    return RedirectResponse(url="/finance/ports", status_code=303)
