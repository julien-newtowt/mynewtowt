"""Planification provisoire — scénarios what-if isolés du planning réel.

Routes montées sous ``/planning/scenarios``. Permission module ``planning``
(C consult / M modify / S suppress).

L'outil est **consultatif**, à une exception près et une seule :
``POST /{id}/apply`` reporte le scénario sur la planification réelle. Cette
route délègue à ``planning.update_leg`` — mêmes contrôles, même cascade, même
refus de déplacer un leg appareillé que l'écran de planification (cf.
``services.scenario.apply_to_active_planning``).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.planning_scenario import PlanningScenario, ScenarioLeg
from app.models.port import Port
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services import scenario as svc
from app.services.activity import record as activity_record
from app.services.planning import InvalidLegDates, PlanningError
from app.templating import templates

router = APIRouter(prefix="/planning/scenarios", tags=["planning-scenarios"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_scenario_or_404(db: AsyncSession, scenario_id: int) -> PlanningScenario:
    scenario = await svc.get_scenario(db, scenario_id)
    if not scenario:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scénario introuvable")
    return scenario


async def _get_leg_or_404(db: AsyncSession, scenario_id: int, leg_id: int) -> ScenarioLeg:
    leg = await svc.get_scenario_leg(db, leg_id)
    if not leg or leg.scenario_id != scenario_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leg introuvable")
    return leg


async def _vessels(db: AsyncSession) -> list[Vessel]:
    return list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())


#: Nombre de traversées proposées au « Chaîner après » (même valeur que le
#: formulaire réel : au-delà, la liste cesse d'aider).
CHAIN_OPTIONS_LIMIT = 8


async def _chain_option(db: AsyncSession, ref: ScenarioLeg) -> dict | None:
    """Suggestion de traversée suivante dérivée d'une traversée provisoire.

    Même contrat que ``planning_router._chain_option`` — c'est
    ``leg-form-suggest.js`` qui consomme ce dictionnaire, et il est partagé
    avec le formulaire réel. Deux différences assumées, propres au provisoire :

    * la référence est une **traversée du scénario**, pas un leg réel : on
      construit une séquence hypothétique, elle doit s'enchaîner sur elle-même ;
    * il n'y a ni ATA ni rang de ``leg_code`` à prévoir — un scénario porte des
      étiquettes libres, pas la numérotation officielle. Les clés existent
      quand même (``from_ata``, ``next_rank_letter``) pour que le script n'ait
      pas à connaître deux formats ; elles valent ``None`` / ``"—"``.
    """
    from math import ceil

    from app.services.planning import closed_weekdays_for_port, next_working_departure

    if ref.eta is None:
        return None
    stay = ref.port_stay_planned_hours or 48
    closed = await closed_weekdays_for_port(db, ref.arrival_port_id)
    suggested = next_working_departure(ref.eta, stay, closed)
    pol = await db.get(Port, ref.departure_port_id)
    pod = await db.get(Port, ref.arrival_port_id)
    return {
        "ref_leg_id": ref.id,
        "etd": suggested.strftime("%Y-%m-%d"),
        "pol_id": ref.arrival_port_id,
        "port_stay_hours": stay,
        "port_stay_days": max(1, ceil(stay / 24)),
        "from_leg_code": ref.label or f"#{ref.id}",
        "from_phase": "planifie",
        "from_pol_locode": pol.locode if pol else None,
        "from_pod_locode": pod.locode if pod else None,
        "from_pod_name": pod.name if pod else None,
        "from_eta": ref.eta.strftime("%Y-%m-%d"),
        "from_ata": None,
        "next_rank_letter": "—",
        "year_digit": str(suggested.year)[-1],
    }


async def _chain_suggestions(db: AsyncSession, scenario_id: int) -> dict[int, dict]:
    """Séquence **du scénario** par navire → pré-remplissage de la traversée
    suivante (POL, ETD, escale).

    On chaîne sur les traversées du scénario et non sur les legs réels : une
    hypothèse se construit sur les hypothèses déjà posées. Un scénario cloné du
    réel contient de toute façon ces legs, donc le cas courant est couvert.

    Comme au réel (PLN-BUGS), le défaut est la traversée d'ETD le plus tardif
    mais il **reste un défaut** : ``chain_options`` expose les dernières
    traversées pour que l'opérateur désigne celle après laquelle il enchaîne.
    """
    from sqlalchemy import desc

    suggestions: dict[int, dict] = {}
    for vessel in await _vessels(db):
        entry: dict = {"vessel_code": vessel.code, "vessel_name": vessel.name, "no_legs": True}
        suggestions[vessel.id] = entry
        candidates = list(
            (
                await db.execute(
                    select(ScenarioLeg)
                    .where(ScenarioLeg.scenario_id == scenario_id)
                    .where(ScenarioLeg.vessel_id == vessel.id)
                    .where(ScenarioLeg.status != "cancelled")
                    .order_by(desc(ScenarioLeg.etd), desc(ScenarioLeg.id))
                    .limit(CHAIN_OPTIONS_LIMIT)
                )
            )
            .scalars()
            .all()
        )
        options = [o for o in [await _chain_option(db, c) for c in candidates] if o]
        if not options:
            continue
        for option in options:
            option["vessel_code"] = vessel.code
            option["vessel_name"] = vessel.name
        entry.update(options[0])
        entry["no_legs"] = False
        entry["chain_options"] = options
    return suggestions


def _stay_hours_from_form(form) -> int | None:
    """Escale saisie en JOURS (``port_stay_planned_days``) → heures stockées.

    Le formulaire provisoire travaille à la journée depuis PLN-08, comme le
    réel. Le champ historique en heures reste accepté (anciens signets, appels
    directs) ; la saisie en jours prime quand elle est fournie.
    """
    days = _maybe_int(form.get("port_stay_planned_days"))
    if days is not None:
        return max(0, days) * 24
    return _maybe_int(form.get("port_stay_planned_hours"))


async def _ports(db: AsyncSession) -> list[Port]:
    return list((await db.execute(select(Port).order_by(Port.locode))).scalars().all())


def _parse_dt(value, allow_empty: bool = False) -> datetime:
    if value is None or value == "":
        if allow_empty:
            return None  # type: ignore[return-value]
        raise InvalidLegDates("Date requise")
    s = str(value).replace("T", " ")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise InvalidLegDates(f"Format de date invalide : {value}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _maybe_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Liste des scénarios
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def scenarios_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> HTMLResponse:
    scenarios = await svc.list_scenarios(db)
    counts = {sc.id: await svc.count_legs(db, sc.id) for sc in scenarios}
    return templates.TemplateResponse(
        "staff/planning/scenarios/index.html",
        {"request": request, "user": user, "scenarios": scenarios, "counts": counts},
    )


# ---------------------------------------------------------------------------
# Création (vide ou clone)
# ---------------------------------------------------------------------------


@router.get("/new", response_class=HTMLResponse)
async def new_scenario_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    vessels = await _vessels(db)
    return templates.TemplateResponse(
        "staff/planning/scenarios/new.html",
        {
            "request": request,
            "user": user,
            "vessels": vessels,
            "current_year": datetime.now(UTC).year,
            "error": None,
        },
    )


@router.post("/new", response_class=HTMLResponse)
async def create_scenario_action(
    request: Request,
    name: str = Form(""),
    description: str = Form(""),
    seed: str = Form("blank"),
    clone_vessel_id: str = Form(""),
    clone_year: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    try:
        scenario = await svc.create_scenario(
            db,
            name=name,
            description=description,
            created_by_id=user.id,
            created_by_name=user.username,
        )
    except svc.ScenarioError as e:
        vessels = await _vessels(db)
        return templates.TemplateResponse(
            "staff/planning/scenarios/new.html",
            {
                "request": request,
                "user": user,
                "vessels": vessels,
                "current_year": datetime.now(UTC).year,
                "error": str(e),
                "form": {"name": name, "description": description},
            },
            status_code=400,
        )

    cloned = 0
    if seed == "clone":
        year = _maybe_int(clone_year)
        date_from = datetime(year, 1, 1, tzinfo=UTC) if year else None
        date_to = datetime(year, 12, 31, 23, 59, tzinfo=UTC) if year else None
        cloned = await svc.clone_real_legs_into(
            db,
            scenario,
            vessel_id=_maybe_int(clone_vessel_id),
            date_from=date_from,
            date_to=date_to,
        )

    await activity_record(
        db,
        action="scenario_create",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="planning",
        entity_type="planning_scenario",
        entity_id=scenario.id,
        entity_label=scenario.name,
        detail=f"seed={seed} cloned={cloned}",
    )
    return RedirectResponse(url=f"/planning/scenarios/{scenario.id}", status_code=303)


# ---------------------------------------------------------------------------
# Détail (Gantt + table + comparaison)
# ---------------------------------------------------------------------------


@router.get("/{scenario_id}", response_class=HTMLResponse)
async def scenario_detail(
    request: Request,
    scenario_id: int,
    vessel_id: int | None = None,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> HTMLResponse:
    scenario = await _get_scenario_or_404(db, scenario_id)
    now = datetime.now(UTC)
    selected_year = year or now.year
    window_start = datetime(selected_year, 1, 1, tzinfo=UTC)
    window_end = datetime(selected_year, 12, 31, 23, 59, tzinfo=UTC)

    all_legs = await svc.list_scenario_legs(db, scenario_id)
    legs = [
        li
        for li in all_legs
        if (vessel_id is None or li.vessel_id == vessel_id)
        and li.eta >= window_start
        and li.etd <= window_end
    ]

    vessels = await _vessels(db)
    vessels_by_id = {v.id: v for v in vessels}
    port_ids = {li.departure_port_id for li in all_legs} | {li.arrival_port_id for li in all_legs}
    ports = (
        {
            p.id: p
            for p in (await db.execute(select(Port).where(Port.id.in_(port_ids)))).scalars().all()
        }
        if port_ids
        else {}
    )

    gantt_rows = svc.build_gantt_rows(
        vessels=vessels,
        legs=legs,
        window_start=window_start,
        window_end=window_end,
        ports=ports,
    )

    total_s = (window_end - window_start).total_seconds()
    month_marks = []
    for m in range(1, 13):
        ms = datetime(selected_year, m, 1, tzinfo=UTC)
        left = ((ms - window_start).total_seconds() / total_s) * 100
        month_marks.append({"label": ms.strftime("%b"), "left_pct": round(left, 3)})
    today_pct = None
    if window_start <= now <= window_end:
        today_pct = round(((now - window_start).total_seconds() / total_s) * 100, 3)

    # Années disponibles (à partir des ETD du scénario + année courante/sélectionnée).
    years: set[int] = {now.year, selected_year}
    for li in all_legs:
        years.add(li.etd.year)

    warnings = svc.scenario_warnings(legs, ports)
    comparison = await svc.compare_to_real(
        db,
        all_legs,
        window_start=window_start,
        window_end=window_end,
        vessel_id=vessel_id,
    )

    # Table triée par ETD.
    table_rows = []
    for li in sorted(legs, key=lambda x: x.etd):
        v = vessels_by_id.get(li.vessel_id)
        pol = ports.get(li.departure_port_id)
        pod = ports.get(li.arrival_port_id)
        transit_h = (li.eta - li.etd).total_seconds() / 3600 if li.eta and li.etd else None
        table_rows.append(
            {
                "leg": li,
                "vessel_name": v.name if v else "—",
                "pol_locode": pol.locode if pol else "?",
                "pod_locode": pod.locode if pod else "?",
                "transit_days": round(transit_h / 24, 1) if transit_h else None,
            }
        )

    return templates.TemplateResponse(
        "staff/planning/scenarios/detail.html",
        {
            "request": request,
            "user": user,
            "scenario": scenario,
            "vessels": vessels,
            "gantt_rows": gantt_rows,
            "month_marks": month_marks,
            "today_pct": today_pct,
            "window_start_ms": int(window_start.timestamp() * 1000),
            "window_end_ms": int(window_end.timestamp() * 1000),
            "selected_year": selected_year,
            "years": sorted(years),
            "filter_vessel_id": vessel_id,
            "table_rows": table_rows,
            "warnings": warnings,
            "comparison": comparison,
            "leg_count": len(all_legs),
        },
    )


# ---------------------------------------------------------------------------
# Édition / suppression de l'en-tête
# ---------------------------------------------------------------------------


@router.post("/{scenario_id}/edit")
async def edit_scenario_action(
    scenario_id: int,
    name: str = Form(""),
    description: str = Form(""),
    status_value: str = Form("draft"),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> RedirectResponse:
    scenario = await _get_scenario_or_404(db, scenario_id)
    try:
        await svc.update_scenario(
            db, scenario, name=name, description=description, status=status_value
        )
    except svc.ScenarioError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return RedirectResponse(url=f"/planning/scenarios/{scenario.id}", status_code=303)


@router.post("/{scenario_id}/delete")
async def delete_scenario_action(
    scenario_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "S")),
) -> RedirectResponse:
    scenario = await _get_scenario_or_404(db, scenario_id)
    label = scenario.name
    await svc.delete_scenario(db, scenario)
    await activity_record(
        db,
        action="scenario_delete",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="planning",
        entity_type="planning_scenario",
        entity_id=scenario_id,
        entity_label=label,
    )
    return RedirectResponse(url="/planning/scenarios", status_code=303)


@router.post("/{scenario_id}/apply")
async def apply_scenario_action(
    scenario_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> RedirectResponse:
    scenario = await _get_scenario_or_404(db, scenario_id)
    try:
        result = await svc.apply_to_active_planning(
            db,
            scenario,
            user_id=user.id,
            user_name=user.full_name or user.username,
        )
    except svc.ScenarioError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PlanningError as e:
        # Appliquer un scénario passe désormais par ``planning.update_leg`` :
        # ses refus (chevauchement, continuité, vitesse, archive TOWT) doivent
        # remonter à l'opérateur avec leur message, pas en 500.
        raise HTTPException(status_code=400, detail=str(e)) from e
    await activity_record(
        db,
        action="scenario_apply",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="planning",
        entity_type="planning_scenario",
        entity_id=scenario.id,
        entity_label=scenario.name,
        detail=(
            f"updated={result.updated_legs} changed={result.changed_legs} "
            f"renumbered={len(result.renumbered)} batch={result.batch_id}"
        ),
    )
    return RedirectResponse(url="/planning", status_code=303)


# ---------------------------------------------------------------------------
# Export CSV
# ---------------------------------------------------------------------------


@router.get("/{scenario_id}/export.csv")
async def export_scenario_csv(
    scenario_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> Response:
    scenario = await _get_scenario_or_404(db, scenario_id)
    legs = await svc.list_scenario_legs(db, scenario_id)
    vessels = {v.id: v for v in await _vessels(db)}
    port_ids = {li.departure_port_id for li in legs} | {li.arrival_port_id for li in legs}
    ports = (
        {
            p.id: p
            for p in (await db.execute(select(Port).where(Port.id.in_(port_ids)))).scalars().all()
        }
        if port_ids
        else {}
    )
    body = svc.to_csv(scenario, legs, vessels, ports)
    filename = f"scenario_{scenario_id}.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Legs provisoires (CRUD)
# ---------------------------------------------------------------------------


@router.get("/{scenario_id}/legs/new", response_class=HTMLResponse)
async def new_leg_form(
    request: Request,
    scenario_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    scenario = await _get_scenario_or_404(db, scenario_id)
    return templates.TemplateResponse(
        "staff/planning/scenarios/leg_form.html",
        {
            "request": request,
            "user": user,
            "scenario": scenario,
            "leg": None,
            "vessels": await _vessels(db),
            "ports": await _ports(db),
            "suggestions": await _chain_suggestions(db, scenario_id),
            "error": None,
        },
    )


@router.post("/{scenario_id}/legs/new", response_class=HTMLResponse)
async def create_leg_action(
    request: Request,
    scenario_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    scenario = await _get_scenario_or_404(db, scenario_id)
    form = await request.form()
    try:
        leg = await svc.add_scenario_leg(
            db,
            scenario,
            vessel_id=int(form["vessel_id"]),
            departure_port_id=int(form["departure_port_id"]),
            arrival_port_id=int(form["arrival_port_id"]),
            etd=_parse_dt(form.get("etd")),
            eta=_parse_dt(form.get("eta")),
            label=form.get("label"),
            status=form.get("status") or "planned",
            port_stay_planned_hours=_stay_hours_from_form(form),
            transit_speed_kn=_maybe_float(form.get("transit_speed_kn")),
            elongation_coef=_maybe_float(form.get("elongation_coef")),
            notes=form.get("notes"),
        )
    except (InvalidLegDates, PlanningError, svc.ScenarioError, KeyError, ValueError) as e:
        return templates.TemplateResponse(
            "staff/planning/scenarios/leg_form.html",
            {
                "request": request,
                "user": user,
                "scenario": scenario,
                "leg": None,
                "vessels": await _vessels(db),
                "ports": await _ports(db),
                "suggestions": await _chain_suggestions(db, scenario_id),
                "error": f"Ajout impossible : {e}",
                "form": dict(form),
            },
            status_code=400,
        )
    await activity_record(
        db,
        action="scenario_leg_create",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="planning",
        entity_type="scenario_leg",
        entity_id=leg.id,
        entity_label=leg.label or f"#{leg.id}",
    )
    return RedirectResponse(url=f"/planning/scenarios/{scenario_id}", status_code=303)


@router.get("/{scenario_id}/legs/{leg_id}/edit", response_class=HTMLResponse)
async def edit_leg_form(
    request: Request,
    scenario_id: int,
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    scenario = await _get_scenario_or_404(db, scenario_id)
    leg = await _get_leg_or_404(db, scenario_id, leg_id)
    return templates.TemplateResponse(
        "staff/planning/scenarios/leg_form.html",
        {
            "request": request,
            "user": user,
            "scenario": scenario,
            "leg": leg,
            "vessels": await _vessels(db),
            "ports": await _ports(db),
            "suggestions": await _chain_suggestions(db, scenario_id),
            "error": None,
        },
    )


@router.post("/{scenario_id}/legs/{leg_id}/edit", response_class=HTMLResponse)
async def update_leg_action(
    request: Request,
    scenario_id: int,
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    scenario = await _get_scenario_or_404(db, scenario_id)
    leg = await _get_leg_or_404(db, scenario_id, leg_id)
    form = await request.form()
    try:
        await svc.update_scenario_leg(
            db,
            leg,
            vessel_id=_maybe_int(form.get("vessel_id")),
            departure_port_id=_maybe_int(form.get("departure_port_id")),
            arrival_port_id=_maybe_int(form.get("arrival_port_id")),
            etd=_parse_dt(form.get("etd"), allow_empty=True),
            eta=_parse_dt(form.get("eta"), allow_empty=True),
            label=form.get("label"),
            status=form.get("status"),
            port_stay_planned_hours=_stay_hours_from_form(form),
            transit_speed_kn=_maybe_float(form.get("transit_speed_kn")),
            elongation_coef=_maybe_float(form.get("elongation_coef")),
            notes=form.get("notes"),
        )
    except (InvalidLegDates, PlanningError, svc.ScenarioError) as e:
        return templates.TemplateResponse(
            "staff/planning/scenarios/leg_form.html",
            {
                "request": request,
                "user": user,
                "scenario": scenario,
                "leg": leg,
                "vessels": await _vessels(db),
                "ports": await _ports(db),
                "suggestions": await _chain_suggestions(db, scenario_id),
                "error": str(e),
            },
            status_code=400,
        )
    return RedirectResponse(url=f"/planning/scenarios/{scenario_id}", status_code=303)


@router.post("/{scenario_id}/legs/{leg_id}/move")
async def move_leg_action(
    request: Request,
    scenario_id: int,
    leg_id: int,
    etd: str = Form(...),
    eta: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> JSONResponse:
    """Déplace une traversée provisoire par glisser-déposer dans le Gantt.

    Reçoit les nouvelles dates ETD/ETA (issues du drag) et met à jour le leg.
    Renvoie du JSON (le front recharge la vue en cas de succès).
    """
    await _get_scenario_or_404(db, scenario_id)
    leg = await _get_leg_or_404(db, scenario_id, leg_id)
    try:
        await svc.update_scenario_leg(
            db,
            leg,
            etd=_parse_dt(etd, allow_empty=True),
            eta=_parse_dt(eta, allow_empty=True),
        )
    except (InvalidLegDates, PlanningError, svc.ScenarioError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "leg_id": leg.id,
            "etd": leg.etd.strftime("%Y-%m-%dT%H:%M"),
            "eta": leg.eta.strftime("%Y-%m-%dT%H:%M"),
        }
    )


@router.post("/{scenario_id}/legs/{leg_id}/delete")
async def delete_leg_action(
    scenario_id: int,
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> RedirectResponse:
    await _get_scenario_or_404(db, scenario_id)
    leg = await _get_leg_or_404(db, scenario_id, leg_id)
    await svc.delete_scenario_leg(db, leg)
    return RedirectResponse(url=f"/planning/scenarios/{scenario_id}", status_code=303)
