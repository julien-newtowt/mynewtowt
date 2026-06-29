"""Escale — operations portuaires + shifts dockers + timelines.

Reprises de la V3.0.0 :
- Liste filtrée par navire + année + leg sélectionné.
- Détail d'un leg : leg-summary + timeline opérations + shifts dockers.
- Création/édition d'opérations (IMPORT/EXPORT/BOTH, type+action, planned/actual).
- Création/édition de shifts dockers (cadence palettes/h).
- Lock de leg (clôture administrative).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.crew import CrewAssignment, CrewMember
from app.models.escale import (
    ACTIONS_BY_TYPE,
    DIRECTIONS,
    ESCALE_ACTION_TO_SOF,
    OPERATION_ACTIONS,
    OPERATION_TYPES,
    DockerShift,
    EscaleOperation,
)
from app.models.leg import Leg
from app.models.port import Port
from app.models.sof_event import SOF_EVENT_TYPES, SofEvent
from app.models.stowage import HOLDS
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.escale_crew import (
    couple_crew_assignment,
    embarkation_alerts,
    maybe_create_paf,
)
from app.services.stowage import occupation_by_hold
from app.templating import templates

logger = logging.getLogger("escale")

router = APIRouter(prefix="/escale", tags=["escale"])


def _escale_locked(leg: Leg) -> bool:
    """L'escale du leg est-elle verrouillée (clôture administrative) ?"""
    return leg.escale_locked_at is not None


def _assert_escale_unlocked(leg: Leg) -> None:
    """Garde-fou : refuse toute écriture si l'escale est verrouillée.

    Levée d'une 400 avec message FR explicite — appelée par tous les
    endpoints create/edit/start/end/delete d'opérations et de shifts.
    """
    if _escale_locked(leg):
        raise HTTPException(
            status_code=400,
            detail=(
                "Escale verrouillée — modification impossible. "
                "Déverrouillez l'escale pour la modifier."
            ),
        )


async def _sync_sof_from_operation(
    db: AsyncSession,
    request: Request,
    user,
    op: EscaleOperation,
) -> None:
    """FLX-04 — crée l'événement SOF équivalent à une opération d'escale.

    Idempotent (dédoublonne sur leg_id + event_type + occurred_at) et
    best-effort : toute erreur est journalisée mais ne casse jamais
    l'action escale. ``occurred_at`` = actual_start ou planned_start ou
    maintenant. Ne fait rien si l'``action`` n'a pas d'équivalent SOF.
    """
    event_type = ESCALE_ACTION_TO_SOF.get(op.action)
    if event_type is None or event_type not in SOF_EVENT_TYPES:
        return
    try:
        occurred_at = op.actual_start or op.planned_start or datetime.now(UTC)
        existing = (
            await db.execute(
                select(SofEvent.id).where(
                    SofEvent.leg_id == op.leg_id,
                    SofEvent.event_type == event_type,
                    SofEvent.occurred_at == occurred_at,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        e = SofEvent(
            leg_id=op.leg_id,
            event_type=event_type,
            label=op.label,
            occurred_at=occurred_at,
            notes=_auto_sof_note(op.id),
            recorded_by_id=user.id,
            recorded_by_name=user.full_name or user.username,
        )
        db.add(e)
        await db.flush()
        await activity_record(
            db,
            action="create",
            user_id=user.id,
            user_name=user.full_name or user.username,
            user_role=user.role,
            module="escale",
            entity_type="sof_event",
            entity_id=e.id,
            entity_label=f"{event_type}@{occurred_at.isoformat()}",
            detail=f"auto from escale op {op.id}",
            ip_address=_client_ip(request),
        )
    except Exception:
        logger.exception("SOF auto-creation failed for escale op %s (action=%s)", op.id, op.action)


def _auto_sof_note(op_id: int) -> str:
    return f"Auto depuis escale (op #{op_id})"


async def _unsync_sof_from_operation(db: AsyncSession, op: EscaleOperation) -> None:
    """Supprime les SOF auto-générés par une opération (avant re-synchro à l'édition).

    Évite l'accumulation d'événements SOF obsolètes quand l'heure d'une
    opération change : on retire l'auto-SOF précédent puis on le recrée à la
    nouvelle date (cf. ``edit_operation``).
    """
    rows = (
        (
            await db.execute(
                select(SofEvent).where(
                    SofEvent.leg_id == op.leg_id,
                    SofEvent.notes == _auto_sof_note(op.id),
                )
            )
        )
        .scalars()
        .all()
    )
    for ev in rows:
        await db.delete(ev)
    if rows:
        await db.flush()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def escale_index(
    request: Request,
    vessel: str | None = None,
    year: int | None = None,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "C")),
) -> HTMLResponse:
    from app.services.leg_filter import build_leg_filter, set_leg_filter_cookie

    # Module de filtrage standard (hérite du leg choisi sur /onboard via cookie).
    f = await build_leg_filter(db, vessel=vessel, year=year, leg_id=leg_id, request=request)
    vessels = f["vessels"]
    selected_vessel = f["selected_vessel"]
    current_year = f["current_year"]
    years = f["years"]
    legs = f["legs"]
    leg_id = f["leg_id"]

    selected_leg = None
    operations: list[EscaleOperation] = []
    shifts: list[DockerShift] = []
    pol = pod = None
    vessel_status = None
    stowage_by_hold: dict[str, dict] = {}
    vessel_crew: list[CrewMember] = []
    crew_assignments: list[CrewAssignment] = []
    embark_alerts: list[dict] = []
    crew_by_id: dict[int, str] = {}
    if leg_id:
        selected_leg = await db.get(Leg, leg_id)
        if selected_leg:
            operations = list(
                (
                    await db.execute(
                        select(EscaleOperation)
                        .where(EscaleOperation.leg_id == leg_id)
                        .order_by(EscaleOperation.planned_start.asc())
                    )
                )
                .scalars()
                .all()
            )
            shifts = list(
                (
                    await db.execute(
                        select(DockerShift)
                        .where(DockerShift.leg_id == leg_id)
                        .order_by(DockerShift.planned_start.asc())
                    )
                )
                .scalars()
                .all()
            )
            pol = await db.get(Port, selected_leg.departure_port_id)
            pod = await db.get(Port, selected_leg.arrival_port_id)
            vessel_status = "en_mer" if (selected_leg.atd and not selected_leg.ata) else "a_quai"
            # ESC-06 — équipage du navire (pour la sélection embarq./débarq.) +
            # affectations du voyage (panneau billets) + alertes de cohérence.
            if selected_leg.vessel_id:
                vessel_crew = list(
                    (
                        await db.execute(
                            select(CrewMember)
                            .where(CrewMember.is_active.is_(True))
                            .order_by(CrewMember.full_name.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
            crew_assignments = list(
                (
                    await db.execute(
                        select(CrewAssignment)
                        .where(CrewAssignment.leg_id == leg_id)
                        .order_by(CrewAssignment.embark_at.asc().nullslast())
                    )
                )
                .scalars()
                .all()
            )
            embark_alerts = embarkation_alerts(crew_assignments, selected_leg)
            # Noms des marins référencés (équipage actif + affectés au voyage).
            member_ids = {m.id for m in vessel_crew} | {a.crew_member_id for a in crew_assignments}
            if member_ids:
                rows = list(
                    (await db.execute(select(CrewMember).where(CrewMember.id.in_(member_ids))))
                    .scalars()
                    .all()
                )
                crew_by_id = {m.id: m.full_name for m in rows}
            else:
                crew_by_id = {}
            # B3 — occupation du plan d'arrimage par cale, pour relier le
            # planning dockers au stowage. Best-effort : ne casse jamais la
            # page si le plan/les items sont absents ou en erreur.
            try:
                stowage_by_hold = await occupation_by_hold(db, selected_leg.id)
            except Exception:
                logger.exception("occupation_by_hold failed for leg %s", selected_leg.id)
                stowage_by_hold = {}

    f["selected_leg"] = selected_leg

    # ESC-08 — synthèse commerciale + timeline + métriques de navigation du leg.
    leg_overview = None
    port_call = None
    nav_metrics = None
    lanes = None
    if selected_leg is not None:
        from app.services.leg_overview import (
            commercial_overview,
            operations_by_lane,
            port_call_steps,
        )
        from app.services.voyage_track import compute_metrics, positions_for_leg

        leg_overview = await commercial_overview(db, selected_leg.id)
        port_call = port_call_steps(selected_leg, operations)
        lanes = operations_by_lane(operations)
        positions = await positions_for_leg(db, selected_leg)
        nav_metrics = compute_metrics(positions, selected_leg, arr_port=pod)

    response = templates.TemplateResponse(
        "staff/escale/index.html",
        {
            "request": request,
            "user": user,
            # Module de filtrage standard (cf. staff/_leg_filter.html).
            "leg_filter_ctx": f,
            "vessels": vessels,
            "selected_vessel": selected_vessel,
            "years": years,
            "current_year": current_year,
            "legs": legs,
            "selected_leg": selected_leg,
            "leg_id": leg_id,
            "operations": operations,
            "shifts": shifts,
            "pol": pol,
            "pod": pod,
            # UX-03 — horloge sidebar « port de destination » (fuseau IANA).
            "next_port_tz": (pod.timezone if pod and pod.timezone else None),
            "next_port_label": (pod.locode if pod else None),
            "stowage_by_hold": stowage_by_hold,
            "holds": HOLDS,
            "vessel_status": vessel_status,
            "leg_locked": _escale_locked(selected_leg) if selected_leg else False,
            "escale_locked_by": selected_leg.escale_locked_by if selected_leg else None,
            "escale_locked_at": selected_leg.escale_locked_at if selected_leg else None,
            "leg_terminated": (
                bool(selected_leg and selected_leg.atd and selected_leg.ata)
                if selected_leg
                else False
            ),
            "operation_types": OPERATION_TYPES,
            "operation_actions": OPERATION_ACTIONS,
            "actions_by_type": ACTIONS_BY_TYPE,
            "leg_overview": leg_overview,
            "port_call": port_call,
            "nav_metrics": nav_metrics,
            "lanes": lanes,
            "directions": DIRECTIONS,
            # ESC-06 — couplage équipage.
            "vessel_crew": vessel_crew,
            "crew_assignments": crew_assignments,
            "embark_alerts": embark_alerts,
            "crew_by_id": crew_by_id,
        },
    )
    set_leg_filter_cookie(response, f)
    return response


@router.post("/legs/{leg_id}/operations")
async def create_operation(
    leg_id: int,
    request: Request,
    direction: str = Form("BOTH"),
    operation_type: str = Form(...),
    action: str = Form(...),
    label: str | None = Form(None),
    intervenant: str | None = Form(None),
    crew_member_id: int | None = Form(None),
    planned_start: str | None = Form(None),
    planned_end: str | None = Form(None),
    cost_forecast: float | None = Form(None),
    cost_actual: float | None = Form(None),
    notes: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    _assert_escale_unlocked(leg)
    op = EscaleOperation(
        leg_id=leg_id,
        direction=direction,
        operation_type=operation_type,
        action=action,
        label=label,
        intervenant=intervenant or None,
        planned_start=datetime.fromisoformat(planned_start) if planned_start else None,
        planned_end=datetime.fromisoformat(planned_end) if planned_end else None,
        cost_forecast=cost_forecast,
        cost_actual=cost_actual,
        notes=notes,
    )
    db.add(op)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="escale",
        entity_type="escale_operation",
        entity_id=op.id,
        entity_label=f"{operation_type}/{action} leg={leg_id}",
        ip_address=_client_ip(request),
    )
    # FLX-04 — relier escale ↔ onboard : génère le SOF équivalent.
    await _sync_sof_from_operation(db, request, user, op)
    # ESC-06 — couplage équipage (embarquement/débarquement) + auto-PAF FR.
    await couple_crew_assignment(db, op, leg, crew_member_id)
    await maybe_create_paf(db, op, leg)
    return RedirectResponse(url=f"/escale?leg_id={leg_id}", status_code=303)


@router.post("/operations/{op_id}/start")
async def start_operation(
    op_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    op = await db.get(EscaleOperation, op_id)
    if op is None:
        raise HTTPException(status_code=404)
    leg = await db.get(Leg, op.leg_id)
    if leg is not None:
        _assert_escale_unlocked(leg)
    op.actual_start = datetime.now(UTC)
    op.status = "in_progress"
    await db.flush()
    # FLX-04 — l'opération démarrée matérialise son SOF (occurred_at réel).
    await _sync_sof_from_operation(db, request, user, op)
    return RedirectResponse(url=f"/escale?leg_id={op.leg_id}", status_code=303)


@router.post("/operations/{op_id}/end")
async def end_operation(
    op_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    op = await db.get(EscaleOperation, op_id)
    if op is None:
        raise HTTPException(status_code=404)
    leg = await db.get(Leg, op.leg_id)
    if leg is not None:
        _assert_escale_unlocked(leg)
    op.actual_end = datetime.now(UTC)
    op.status = "completed"
    await db.flush()
    return RedirectResponse(url=f"/escale?leg_id={op.leg_id}", status_code=303)


def _parse_iso(value: str | None) -> datetime | None:
    """Parse tolérant d'un datetime ISO de formulaire (ESC-03)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@router.post("/operations/{op_id}/edit")
async def edit_operation(
    op_id: int,
    request: Request,
    direction: str = Form("BOTH"),
    operation_type: str = Form(...),
    action: str = Form(...),
    label: str | None = Form(None),
    intervenant: str | None = Form(None),
    planned_start: str | None = Form(None),
    planned_end: str | None = Form(None),
    actual_start: str | None = Form(None),
    actual_end: str | None = Form(None),
    status: str | None = Form(None),
    cost_forecast: float | None = Form(None),
    cost_actual: float | None = Form(None),
    notes: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    """ESC-01/03 — édition d'une opération (dont saisie manuelle des heures réelles)."""
    op = await db.get(EscaleOperation, op_id)
    if op is None:
        raise HTTPException(status_code=404)
    leg = await db.get(Leg, op.leg_id)
    if leg is not None:
        _assert_escale_unlocked(leg)
    op.direction = direction
    op.operation_type = operation_type
    op.action = action
    op.label = label
    op.intervenant = intervenant or None
    op.planned_start = _parse_iso(planned_start)
    op.planned_end = _parse_iso(planned_end)
    op.actual_start = _parse_iso(actual_start)
    op.actual_end = _parse_iso(actual_end)
    if status:
        op.status = status
    elif op.actual_end:
        op.status = "completed"
    elif op.actual_start:
        op.status = "in_progress"
    op.cost_forecast = cost_forecast
    op.cost_actual = cost_actual
    op.notes = notes
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="escale",
        entity_type="escale_operation",
        entity_id=op.id,
        entity_label=f"{operation_type}/{action} leg={op.leg_id}",
        ip_address=_client_ip(request),
    )
    # Réconcilie le SOF : retire l'auto-SOF obsolète (l'occurred_at a pu
    # changer) puis le recrée à la nouvelle date — pas d'accumulation.
    await _unsync_sof_from_operation(db, op)
    await _sync_sof_from_operation(db, request, user, op)
    return RedirectResponse(url=f"/escale?leg_id={op.leg_id}", status_code=303)


@router.post("/operations/{op_id}/delete")
async def delete_operation(
    op_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "S")),
):
    """ESC-01 — suppression d'une opération (interdite si escale verrouillée)."""
    op = await db.get(EscaleOperation, op_id)
    if op is None:
        raise HTTPException(status_code=404)
    leg = await db.get(Leg, op.leg_id)
    if leg is not None:
        _assert_escale_unlocked(leg)
    leg_id = op.leg_id
    await db.delete(op)
    await db.flush()
    await activity_record(
        db,
        action="delete",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="escale",
        entity_type="escale_operation",
        entity_id=op_id,
        entity_label=f"op {op_id} leg={leg_id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/escale?leg_id={leg_id}", status_code=303)


def _normalize_hold(hold: str | None) -> str | None:
    """Valide la cale d'un shift docker contre ``stowage.HOLDS``.

    Renvoie le code HOLD ("AR"/"AV") s'il est connu, sinon ``None`` (cale
    non spécifiée) — y compris pour les sentinelles vides ("", "—").
    """
    if not hold:
        return None
    hold = hold.strip().upper()
    return hold if hold in HOLDS else None


@router.post("/legs/{leg_id}/dockers")
async def create_docker_shift(
    leg_id: int,
    request: Request,
    direction: str = Form("BOTH"),
    company: str | None = Form(None),
    nb_dockers: int = Form(0),
    palettes_target: int | None = Form(None),
    hold: str | None = Form(None),
    planned_start: str | None = Form(None),
    planned_end: str | None = Form(None),
    cost_eur: float | None = Form(None),
    notes: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    _assert_escale_unlocked(leg)
    s = DockerShift(
        leg_id=leg_id,
        direction=direction,
        company=company,
        nb_dockers=nb_dockers,
        palettes_target=palettes_target,
        hold=_normalize_hold(hold),
        planned_start=datetime.fromisoformat(planned_start) if planned_start else None,
        planned_end=datetime.fromisoformat(planned_end) if planned_end else None,
        cost_eur=cost_eur,
        notes=notes,
    )
    db.add(s)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="escale",
        entity_type="docker_shift",
        entity_id=s.id,
        entity_label=f"shift {company} leg={leg_id} cale={s.hold or '—'}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/escale?leg_id={leg_id}", status_code=303)


@router.post("/dockers/{shift_id}/progress")
async def docker_progress(
    shift_id: int,
    request: Request,
    palettes_done: int = Form(0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    s = await db.get(DockerShift, shift_id)
    if s is None:
        raise HTTPException(status_code=404)
    leg = await db.get(Leg, s.leg_id)
    if leg is not None:
        _assert_escale_unlocked(leg)
    s.palettes_done = palettes_done
    await db.flush()
    return RedirectResponse(url=f"/escale?leg_id={s.leg_id}", status_code=303)


@router.post("/dockers/{shift_id}/edit")
async def edit_docker_shift(
    shift_id: int,
    request: Request,
    direction: str = Form("BOTH"),
    company: str | None = Form(None),
    nb_dockers: int = Form(0),
    palettes_target: int | None = Form(None),
    palettes_done: int | None = Form(None),
    hold: str | None = Form(None),
    planned_start: str | None = Form(None),
    planned_end: str | None = Form(None),
    actual_start: str | None = Form(None),
    actual_end: str | None = Form(None),
    cost_eur: float | None = Form(None),
    notes: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    """ESC-01/03 — édition d'un shift docker (dont heures réelles + cadence)."""
    s = await db.get(DockerShift, shift_id)
    if s is None:
        raise HTTPException(status_code=404)
    leg = await db.get(Leg, s.leg_id)
    if leg is not None:
        _assert_escale_unlocked(leg)
    s.direction = direction
    s.company = company
    s.nb_dockers = nb_dockers
    s.palettes_target = palettes_target
    if palettes_done is not None:
        s.palettes_done = palettes_done
    s.hold = _normalize_hold(hold)
    s.planned_start = _parse_iso(planned_start)
    s.planned_end = _parse_iso(planned_end)
    s.actual_start = _parse_iso(actual_start)
    s.actual_end = _parse_iso(actual_end)
    s.cost_eur = cost_eur
    s.notes = notes
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="escale",
        entity_type="docker_shift",
        entity_id=s.id,
        entity_label=f"shift {company} leg={s.leg_id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/escale?leg_id={s.leg_id}", status_code=303)


@router.post("/dockers/{shift_id}/delete")
async def delete_docker_shift(
    shift_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "S")),
):
    """ESC-01 — suppression d'un shift docker."""
    s = await db.get(DockerShift, shift_id)
    if s is None:
        raise HTTPException(status_code=404)
    leg = await db.get(Leg, s.leg_id)
    if leg is not None:
        _assert_escale_unlocked(leg)
    leg_id = s.leg_id
    await db.delete(s)
    await db.flush()
    await activity_record(
        db,
        action="delete",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="escale",
        entity_type="docker_shift",
        entity_id=shift_id,
        entity_label=f"shift {shift_id} leg={leg_id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/escale?leg_id={leg_id}", status_code=303)


def _to_utc(dt: datetime | None) -> datetime | None:
    """Rend un datetime aware UTC (les saisies de formulaire sont naïves)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


@router.post("/legs/{leg_id}/port-status")
async def update_port_status(
    leg_id: int,
    request: Request,
    new_status: str = Form(...),
    status_time: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    """ESC-02 — pilotage du statut portuaire : pose ATA/ATD, recalcule la
    finance (rollup) et notifie la compagnie. **Idempotent** : re-soumettre
    (correction d'horodatage) ne réémet pas de notification.

    Réutilise les helpers V3 partagés avec le commandant (``voyage_events``) :
    arrivée = EOSP (à quai), départ = SOSP (pilote départ).

    NB — la pose d'ATA/ATD n'altère ni l'ETD ni l'ETA du leg : la propagation
    des dates prévisionnelles aval (``cascade_from_leg``) relève du décalage
    d'ETA (déclaration capitaine / édition planning), pas de l'enregistrement
    du réel. On ne cascade donc pas ici — comportement aligné sur le flux SOF
    du commandant (``captain_router``), qui ne cascade pas non plus à l'arrivée
    ou au départ.
    """
    from app.services.finance_rollup import rollup_for_leg
    from app.services.notifications import notify_eosp, notify_sosp
    from app.services.voyage_events import on_vessel_arrived, on_vessel_departed

    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    _assert_escale_unlocked(leg)
    t = _to_utc(_parse_iso(status_time)) or datetime.now(UTC)

    if new_status == "a_quai":
        first_arrival = leg.ata is None  # garde d'idempotence (avant mutation)
        leg.ata = t
        leg.status = "in_progress"
        await on_vessel_arrived(db, leg)  # idempotent : ata déjà posée, avance bookings
        await rollup_for_leg(db, leg)
        if first_arrival:
            await notify_eosp(db, leg.leg_code, leg.id)
    elif new_status == "pilote_depart":
        if leg.ata is None:
            raise HTTPException(
                status_code=400,
                detail="Renseigner d'abord le statut « à quai » (ATA) avant le départ.",
            )
        first_departure = leg.atd is None  # garde d'idempotence (avant mutation)
        leg.atd = t
        leg.status = "completed"
        await on_vessel_departed(db, leg)
        await rollup_for_leg(db, leg)
        if first_departure:
            await notify_sosp(db, leg.leg_code, leg.id)
    else:
        raise HTTPException(status_code=400, detail="statut portuaire inconnu")

    await db.flush()
    await activity_record(
        db,
        action="port_status",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="escale",
        entity_type="leg",
        entity_id=leg.id,
        entity_label=leg.leg_code,
        detail=f"→ {new_status} @ {t.isoformat()}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/escale?leg_id={leg_id}", status_code=303)


@router.post("/legs/{leg_id}/lock")
async def lock_leg(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
):
    """Verrouille l'escale du leg (clôture administrative).

    Renseigne ``escale_locked_at`` / ``escale_locked_by`` ; dès lors les
    endpoints de modification d'escale refusent toute écriture
    (``_assert_escale_unlocked``).
    """
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    leg.escale_locked_at = datetime.now(UTC)
    leg.escale_locked_by = user.full_name or user.username
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="escale",
        entity_type="leg",
        entity_id=leg.id,
        entity_label=leg.leg_code,
        detail="escale locked",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/escale?leg_id={leg_id}", status_code=303)


@router.post("/legs/{leg_id}/unlock")
async def unlock_leg(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "S")),
):
    """Déverrouille l'escale du leg (permission S = Suppress)."""
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    leg.escale_locked_at = None
    leg.escale_locked_by = None
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="escale",
        entity_type="leg",
        entity_id=leg.id,
        entity_label=leg.leg_code,
        detail="escale unlocked",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/escale?leg_id={leg_id}", status_code=303)


@router.get("/legs/{leg_id}/sof.pdf")
async def escale_sof_pdf(
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "C")),
):
    """Génère le SOF escale (opérations + shifts dockers) en PDF WeasyPrint."""

    from fastapi.responses import Response
    from weasyprint import HTML  # local import — heavy native deps

    from app.config import settings

    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)

    pol = await db.get(Port, leg.departure_port_id) if leg.departure_port_id else None
    pod = await db.get(Port, leg.arrival_port_id) if leg.arrival_port_id else None
    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id else None

    operations = list(
        (
            await db.execute(
                select(EscaleOperation)
                .where(EscaleOperation.leg_id == leg_id)
                .order_by(EscaleOperation.planned_start.asc())
            )
        )
        .scalars()
        .all()
    )

    shifts = list(
        (
            await db.execute(
                select(DockerShift)
                .where(DockerShift.leg_id == leg_id)
                .order_by(DockerShift.planned_start.asc())
            )
        )
        .scalars()
        .all()
    )

    tpl = templates.get_template("pdf/sof_escale.html")
    html = tpl.render(
        leg=leg,
        pol=pol,
        pod=pod,
        vessel=vessel,
        operations=operations,
        shifts=shifts,
        issued_at=datetime.now(UTC),
        site_url=settings.site_url,
    )
    pdf = HTML(string=html, base_url=settings.site_url).write_pdf()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="SOF_{leg.leg_code}.pdf"'},
    )


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )
