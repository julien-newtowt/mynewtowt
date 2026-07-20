"""Crew — équipage, assignments, calendrier, compliance Schengen, billets.

Reprise des écrans riches de la V3.0.0 :
- Liste avec stats (total/active/en_repos).
- Bordée par navire (qui est embarqué où, postes vacants).
- Page compliance Schengen (alertes <30 j, passport/visa expirants).
- Assignments embarquement/débarquement.
- Billets transport (uploads).
"""

from __future__ import annotations

import contextlib
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from datetime import date as _date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.crew import (
    CrewAssignment,
    CrewCertification,
    CrewLeave,
    CrewMember,
    MaradCrewSchedule,
)
from app.models.crew_ticket import TRANSPORT_MODES, CrewTicket
from app.models.leg import Leg
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.crew_compliance import (
    REQUIRED_ROLES,
    ROLE_LABELS,
    current_embarkations,
    embarked_days_by_member,
    is_non_schengen_national,
    normalize_role,
    refresh_schengen_for_members,
    vessel_readiness,
)
from app.templating import brand_for_lang, templates

router = APIRouter(prefix="/crew", tags=["crew"])


CREW_ROLES = (
    "capitaine",
    "second",
    "chef_mecanicien",
    "cook",
    "lieutenant",
    "bosco",
    "marin",
    "eleve_officier",
    # Ajoutés pour le trombinoscope Armement (cf.
    # docs/strategy/CAHIER_DES_CHARGES_TROMBINOSCOPE.md §11) — fonctions
    # observées sur le gabarit réel sans équivalent jusqu'ici.
    "electricien",
    "ajusteur",
    "matelot_cuisinier",
)
# REQUIRED_ROLES (armement réglementaire) : source unique dans
# services.crew_compliance — importé ci-dessus (FLX-06).


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def crew_index(
    request: Request,
    role: str | None = None,
    vessel: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "C")),
) -> HTMLResponse:
    stmt = select(CrewMember).where(CrewMember.is_active.is_(True))
    if role:
        stmt = stmt.where(CrewMember.role == role)
    members = list((await db.execute(stmt.order_by(CrewMember.full_name))).scalars().all())

    # FLX-06 : snapshot persisté — recalcule le statut Schengen et l'écrit
    # sur les lignes CrewMember (status / days / window_end) + flush, pour
    # que le statut affiché soit persisté et historisable.
    await refresh_schengen_for_members(db, members)

    total = len(members)

    now = datetime.now(UTC)
    today = now.date()

    # ── Bordées actuelles ─────────────────────────────────────────────────
    # Source réelle : les plannings Marad EN COURS (un navire + fenêtre de dates
    # contenant aujourd'hui). On complète avec les affectations ERP en cours
    # (CrewAssignment) pour ne pas perdre une éventuelle saisie manuelle.
    # ``active_member_ids`` = marins actuellement embarqués (indicateur « En activité »).
    bordees: dict[str, dict] = defaultdict(lambda: {"crew": [], "missing": [], "vessel_id": None})
    active_member_ids: set[int] = set()

    current_scheds = await current_embarkations(db, on=today)
    sched_member_ids = {s.crew_member_id for s in current_scheds}
    crew_by_id: dict[int, CrewMember] = {}
    if sched_member_ids:
        for m in (
            await db.execute(
                select(CrewMember).where(
                    CrewMember.id.in_(sched_member_ids), CrewMember.is_active.is_(True)
                )
            )
        ).scalars():
            crew_by_id[m.id] = m
    sched_vessel_ids = {s.vessel_id for s in current_scheds if s.vessel_id}
    vessels_by_id: dict[int, Vessel] = {}
    if sched_vessel_ids:
        for v in (
            await db.execute(select(Vessel).where(Vessel.id.in_(sched_vessel_ids)))
        ).scalars():
            vessels_by_id[v.id] = v
    for s in current_scheds:
        m = crew_by_id.get(s.crew_member_id)
        if m is None:
            continue  # marin désactivé / inconnu
        v = vessels_by_id.get(s.vessel_id) if s.vessel_id else None
        vname = (v.name if v else None) or s.marad_vessel_name or "—"
        bordees[vname]["crew"].append({"m": m, "role": s.rank_label or m.role})
        if v is not None:
            bordees[vname]["vessel_id"] = v.id
        active_member_ids.add(m.id)

    # Affectations ERP en cours (complément aux plannings Marad).
    active_assigns = list(
        (
            await db.execute(
                select(CrewAssignment)
                .where(CrewAssignment.embark_at.is_not(None))
                .where(
                    (CrewAssignment.disembark_at.is_(None)) | (CrewAssignment.disembark_at > now)
                )
            )
        )
        .scalars()
        .all()
    )
    all_member_by_id = {m.id: m for m in members}
    for a in active_assigns:
        if a.crew_member_id in active_member_ids:
            continue  # déjà listé via Marad
        leg = await db.get(Leg, a.leg_id) if a.leg_id else None
        vsl = await db.get(Vessel, leg.vessel_id) if (leg and leg.vessel_id) else None
        vname = vsl.name if vsl else "—"
        m = all_member_by_id.get(a.crew_member_id) or await db.get(CrewMember, a.crew_member_id)
        if m is None or not m.is_active:
            continue
        bordees[vname]["crew"].append({"m": m, "role": a.role_on_board or m.role})
        if vsl is not None:
            bordees[vname]["vessel_id"] = vsl.id
        active_member_ids.add(m.id)

    for info in bordees.values():
        roles_present = {normalize_role(c["m"].role) for c in info["crew"]}
        info["missing"] = [ROLE_LABELS.get(r, r) for r in REQUIRED_ROLES if r not in roles_present]

    # Compliance alerts (passport / visa within 30 days)
    soon = _date.today() + timedelta(days=30)
    compliance_alerts = [
        m
        for m in members
        if (m.passport_expires_at and m.passport_expires_at <= soon)
        or (m.visa_us_expires_at and m.visa_us_expires_at <= soon)
        or (m.visa_br_expires_at and m.visa_br_expires_at <= soon)
        or m.schengen_status == "warning"
        or m.schengen_status == "non_compliant"
    ]

    stats = {
        "total": total,
        "active": len(active_member_ids),
        "repos": max(0, total - len(active_member_ids)),
    }

    # CREW-09 — marqueur « étranger » (hors Schengen) + jours embarqués sur l'année.
    embarked_days = await embarked_days_by_member(db, now.year, now=now)
    foreigner_ids = {m.id for m in members if is_non_schengen_national(m.nationality)}

    from app.utils import marad

    return templates.TemplateResponse(
        "staff/crew/index.html",
        {
            "request": request,
            "user": user,
            "members": members,
            "roles": CREW_ROLES,
            "selected_role": role,
            "bordees": dict(bordees),
            "compliance_alerts": compliance_alerts,
            "stats": stats,
            "embarked_days": embarked_days,
            "foreigner_ids": foreigner_ids,
            "current_year": now.year,
            "marad_configured": marad.enabled(),
        },
    )


@router.post("/sync-marad")
async def crew_sync_marad(
    request: Request,
    only: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "M")),
) -> RedirectResponse:
    """Synchronise l'équipage et/ou les plannings depuis Marad (LECTURE SEULE).

    ``only`` (champ de formulaire) : ``crew`` ou ``schedules`` pour ne remonter
    qu'une partie, sinon les deux. On expose DEUX boutons distincts sur /crew car
    ``/api/Crewing`` et ``/api/CrewingSchedule`` (1 req/min, fenêtre partagée) ne
    peuvent être appelés coup sur coup : les enchaîner (bouton unique) force un
    429 sur les plannings. Boutons séparés = un endpoint par clic, espaçables.
    """
    from app.services.marad_sync import sync_all, sync_crew, sync_schedules
    from app.utils import marad

    part = (only or "").strip().lower()
    if part == "crew":
        r = await sync_crew(db)
        result = {
            "configured": r["configured"],
            "crew_created": r["created"],
            "crew_updated": r["updated"],
            "crew_fetched": r["fetched"],
            "sched_created": 0,
            "sched_updated": 0,
            "sched_fetched": 0,
            "errors": r["errors"],
            "diagnostic": None,
        }
    elif part in ("schedules", "plannings"):
        r = await sync_schedules(db)
        diag = None
        if r["fetched"] == 0 and marad.last_status("/api/CrewingSchedule") == 429:
            diag = (
                "Plannings : /api/CrewingSchedule a renvoyé 429 (quota 1 req/min). "
                "Attendez ~1 min APRÈS toute autre synchro Marad, puis recliquez "
                "« Synchroniser plannings » (ne pas synchroniser l'équipage juste avant)."
            )
        result = {
            "configured": r["configured"],
            "crew_created": 0,
            "crew_updated": 0,
            "crew_fetched": 0,
            "sched_created": r["created"],
            "sched_updated": r["updated"],
            "sched_fetched": r["fetched"],
            "errors": r["errors"],
            "diagnostic": diag,
        }
    else:
        result = await sync_all(db)
    if not result["configured"]:
        return RedirectResponse(url="/crew?marad=disabled", status_code=303)
    await activity_record(
        db,
        action="sync",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="crew",
        entity_type="crew_member",
        entity_label=(
            f"marad: marins +{result['crew_created']}/~{result['crew_updated']}, "
            f"plannings +{result['sched_created']}/~{result['sched_updated']}"
        ),
        ip_address=_client_ip(request),
    )
    total_fetched = result.get("crew_fetched", 0) + result.get("sched_fetched", 0)
    base = (
        f"/crew?marad=ok&cc={result['crew_created']}&cu={result['crew_updated']}"
        f"&sc={result['sched_created']}&su={result['sched_updated']}"
        f"&cf={result.get('crew_fetched', 0)}&sf={result.get('sched_fetched', 0)}"
        f"&err={result['errors']}"
    )
    # On propage le diagnostic dès qu'il existe (ex. plannings en 429 alors que
    # le crew a réussi) — pas seulement quand tout est à 0, sinon un échec
    # partiel est masqué par le succès du crew. ``empty=1`` distingue le cas
    # « rien remonté » (bandeau d'erreur) du cas partiel (bandeau succès + avert.).
    if result.get("diagnostic"):
        from urllib.parse import quote

        base += f"&diag={quote(result['diagnostic'])}"
        if total_fetched == 0:
            base += "&empty=1"
    return RedirectResponse(url=base, status_code=303)


@router.get("/compliance", response_class=HTMLResponse)
async def crew_compliance(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "C")),
) -> HTMLResponse:
    members = list(
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
    today = _date.today()

    # FLX-06 : snapshot persisté — recalcule + écrit le statut Schengen
    # sur les lignes CrewMember avant affichage (flush dans le service).
    await refresh_schengen_for_members(db, members, today=today)

    # Armement réglementaire par navire (lecture seule, V1).
    vessels = list(
        (await db.execute(select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.name)))
        .scalars()
        .all()
    )
    readiness = []
    for v in vessels:
        r = await vessel_readiness(db, v.id, today)
        r["vessel_name"] = v.name
        readiness.append(r)

    rows = []
    for m in members:
        warnings: list[str] = []
        if m.passport_expires_at:
            days = (m.passport_expires_at - today).days
            if days <= 30:
                warnings.append(f"Passeport expire dans {days} j")
        if m.visa_us_expires_at:
            days = (m.visa_us_expires_at - today).days
            if days <= 30:
                warnings.append(f"Visa US expire dans {days} j")
        if m.visa_br_expires_at:
            days = (m.visa_br_expires_at - today).days
            if days <= 30:
                warnings.append(f"Visa BR expire dans {days} j")
        if m.seaman_book_expires_at:
            days = (m.seaman_book_expires_at - today).days
            if days <= 30:
                warnings.append(f"Seaman book expire dans {days} j")
        if m.schengen_status != "compliant":
            warnings.append(
                f"Schengen {m.schengen_status}"
                + (f" ({m.schengen_days_in_window} j)" if m.schengen_days_in_window else "")
            )
        rows.append({"member": m, "warnings": warnings})
    return templates.TemplateResponse(
        "staff/crew/compliance.html",
        {"request": request, "user": user, "rows": rows, "readiness": readiness},
    )


@router.get("/calendar", response_class=HTMLResponse)
async def crew_calendar(
    request: Request,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "C")),
) -> HTMLResponse:
    """Heatmap annuelle : jours embarqués vs au repos par membre."""
    y = year or _date.today().year
    start = _date(y, 1, 1)
    end = _date(y, 12, 31)
    members = list(
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
    assigns = list((await db.execute(select(CrewAssignment))).scalars().all())
    # Build per-member day map
    days_by_member: dict[int, set[_date]] = defaultdict(set)

    def _fill(member_id: int, d0: _date | None, d1: _date | None) -> None:
        """Marque chaque jour de [d0, d1] (borné à l'année) comme embarqué."""
        if member_id is None or d0 is None:
            return
        cur = max(d0, start)
        last = min(d1 or _date.today(), end)
        while cur <= last:
            days_by_member[member_id].add(cur)
            cur += timedelta(days=1)

    for a in assigns:
        if not a.embark_at:
            continue
        _fill(
            a.crew_member_id,
            a.embark_at.date(),
            (a.disembark_at or datetime.now(UTC)).date(),
        )
    # Activité importée de Marad (lecture seule) — plannings rattachés à un marin.
    marad_scheds = list(
        (
            await db.execute(
                select(MaradCrewSchedule).where(MaradCrewSchedule.crew_member_id.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    for s in marad_scheds:
        # Ne compter que les EMBARQUEMENTS (navire renseigné) : les plannings
        # Marad incluent aussi des périodes à terre (congés, indisponibilités,
        # ex. Status="Congés" avec Vessel=null) — ce ne sont pas des jours
        # embarqués et ils fausseraient la heatmap. Ils restent visibles sur la
        # fiche marin (section « Planning Marad »).
        if not (s.marad_vessel_name or s.vessel_id):
            continue
        _fill(s.crew_member_id, s.start_date, s.end_date)
    return templates.TemplateResponse(
        "staff/crew/calendar.html",
        {
            "request": request,
            "user": user,
            "year": y,
            "members": members,
            "days_by_member": {m.id: sorted(days_by_member.get(m.id, ())) for m in members},
        },
    )


def _pdate(value: str | None) -> _date | None:
    """Parse tolérant d'une date ISO de formulaire."""
    if not value or not str(value).strip():
        return None
    try:
        return _date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _naive(dt: datetime | None) -> datetime | None:
    """Normalise en datetime naïf (UTC supposé) pour comparer sans erreur tz.

    Les dates de formulaire sont naïves ; celles en base peuvent être aware
    (Postgres) — comparer directement lèverait un TypeError. On retire le tz.
    """
    return dt.replace(tzinfo=None) if dt is not None else None


async def _find_overlap(
    db: AsyncSession,
    *,
    member_id: int,
    embark: datetime | None,
    disembark: datetime | None,
    exclude_id: int | None = None,
) -> CrewAssignment | None:
    """CREW-08 — renvoie une affectation chevauchante du marin, ou None.

    Comparaison datetime‑granulaire à bornes STRICTES : deux périodes qui se
    touchent (débarquement == ré‑embarquement) ne se chevauchent pas — une
    relève le même jour reste permise. Période ouverte = ±infini.
    """
    lo = _naive(embark) or datetime.min
    hi = _naive(disembark) or datetime.max
    existing = list(
        (
            await db.execute(
                select(CrewAssignment)
                .where(CrewAssignment.crew_member_id == member_id)
                .where(CrewAssignment.embark_at.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    for ex in existing:
        if exclude_id is not None and ex.id == exclude_id:
            continue
        ex_lo = _naive(ex.embark_at) or datetime.min
        ex_hi = _naive(ex.disembark_at) or datetime.max
        if lo < ex_hi and ex_lo < hi:
            return ex
    return None


# CREW-03 — champs de la fiche marin pilotés par formulaire (create + edit).
_MEMBER_STR_FIELDS = ("passport_number", "seaman_book_number", "email", "phone", "notes")
_MEMBER_DATE_FIELDS = (
    "date_of_birth",
    "passport_expires_at",
    "visa_us_expires_at",
    "visa_br_expires_at",
    "seaman_book_expires_at",
)


def _apply_member_form(m: CrewMember, form: dict) -> None:
    """Applique les champs présents d'un formulaire à la fiche marin."""
    if "full_name" in form and (form.get("full_name") or "").strip():
        m.full_name = form["full_name"].strip()
    if "role" in form:
        m.role = (form.get("role") or "").strip()
    if "nationality" in form:
        m.nationality = (form.get("nationality") or "").strip().upper()[:2] or None
    for f in _MEMBER_STR_FIELDS:
        if f in form:
            setattr(m, f, (form.get(f) or "").strip() or None)
    for f in _MEMBER_DATE_FIELDS:
        if f in form:
            setattr(m, f, _pdate(form.get(f)))


@router.get("/members/{member_id}/edit", response_class=HTMLResponse)
async def crew_edit_form(
    member_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "M")),
) -> HTMLResponse:
    m = await db.get(CrewMember, member_id)
    if m is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "staff/crew/new.html",
        {"request": request, "user": user, "roles": CREW_ROLES, "member": m},
    )


@router.post("/members/{member_id}/edit")
async def crew_edit(
    member_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "M")),
):
    """CREW-01/03 — édition d'une fiche marin."""
    m = await db.get(CrewMember, member_id)
    if m is None:
        raise HTTPException(status_code=404)
    form = dict(await request.form())
    role = (form.get("role") or m.role or "").strip()
    if role not in CREW_ROLES:
        raise HTTPException(status_code=400, detail="invalid role")
    _apply_member_form(m, form)
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="crew",
        entity_type="crew_member",
        entity_id=m.id,
        entity_label=m.full_name,
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/crew/members/{member_id}", status_code=303)


async def _has_active_embarkation(db: AsyncSession, member_id: int) -> bool:
    """True si le marin a un embarquement en cours (non débarqué)."""
    now = datetime.now(UTC)
    return bool(
        await db.scalar(
            select(CrewAssignment.id)
            .where(CrewAssignment.crew_member_id == member_id)
            .where(CrewAssignment.embark_at.is_not(None))
            .where((CrewAssignment.disembark_at.is_(None)) | (CrewAssignment.disembark_at > now))
            .limit(1)
        )
    )


@router.post("/members/{member_id}/toggle-active")
async def crew_member_toggle_active(
    member_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "M")),
):
    """CREW-08 — désactive/réactive un marin. Désactivation refusée tant qu'un
    embarquement est en cours (un marin embarqué ne peut pas être retiré).
    """
    m = await db.get(CrewMember, member_id)
    if m is None:
        raise HTTPException(status_code=404)
    if m.is_active and await _has_active_embarkation(db, member_id):
        raise HTTPException(
            status_code=400,
            detail="Marin embarqué : débarquez-le avant de le désactiver.",
        )
    m.is_active = not m.is_active
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="crew",
        entity_type="crew_member",
        entity_id=m.id,
        entity_label=m.full_name,
        detail="réactivé" if m.is_active else "désactivé",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/crew/members/{member_id}", status_code=303)


@router.get("/api/by-vessel/{vessel_id}")
async def crew_api_by_vessel(
    vessel_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "C")),
):
    """CREW-06 — API JSON : équipage actuellement embarqué sur un navire."""
    from fastapi.responses import JSONResponse

    now = datetime.now(UTC)
    rows = (
        await db.execute(
            select(
                CrewMember.id,
                CrewMember.full_name,
                CrewMember.role,
                CrewMember.nationality,
                CrewAssignment.leg_id,
                CrewAssignment.embark_at,
                CrewAssignment.disembark_at,
            )
            .join(CrewAssignment, CrewAssignment.crew_member_id == CrewMember.id)
            .where(
                (CrewAssignment.vessel_id == vessel_id)
                | (CrewAssignment.leg_id.in_(select(Leg.id).where(Leg.vessel_id == vessel_id)))
            )
            .where(CrewAssignment.embark_at.is_not(None))
            .where((CrewAssignment.disembark_at.is_(None)) | (CrewAssignment.disembark_at > now))
            .order_by(CrewMember.full_name)
        )
    ).all()
    crew = [
        {
            "member_id": r.id,
            "full_name": r.full_name,
            "role": r.role,
            "nationality": r.nationality,
            "leg_id": r.leg_id,
            "embark_at": r.embark_at.isoformat() if r.embark_at else None,
            "disembark_at": r.disembark_at.isoformat() if r.disembark_at else None,
        }
        for r in rows
    ]
    return JSONResponse({"vessel_id": vessel_id, "count": len(crew), "crew": crew})


def _embarkation_timeline(schedules: list, today: _date) -> dict | None:
    """Barres de planning positionnées pour la vue timeline de la fiche marin.

    Chaque planning Marad (embarquement = navire, ou période à terre = congé)
    devient une barre positionnée en % sur l'axe [min début, max fin]. Renvoie
    None si aucun planning daté.
    """
    dated = [s for s in schedules if s.start_date]
    if not dated:
        return None
    lo = min(s.start_date for s in dated)
    hi = max(max((s.end_date or today) for s in dated), lo)
    if hi <= lo:
        hi = lo + timedelta(days=1)
    span = (hi - lo).days or 1
    periods = []
    for s in sorted(dated, key=lambda x: x.start_date):
        end = s.end_date or today
        if end < s.start_date:
            end = s.start_date
        left = (s.start_date - lo).days / span * 100
        width = max(1.5, (end - s.start_date).days / span * 100)
        periods.append(
            {
                "label": s.marad_vessel_name or s.status or "—",
                "role": s.rank_label,
                "start": s.start_date,
                "end": s.end_date,
                "status": s.status,
                "is_embarkation": bool(s.vessel_id or s.marad_vessel_name),
                "left": round(left, 2),
                "width": round(min(width, max(0.0, 100 - left)), 2),
            }
        )
    return {"start": lo, "end": hi, "periods": periods}


async def _member_detail_context(
    request: Request,
    db: AsyncSession,
    user,
    member: CrewMember,
    *,
    error: str | None = None,
) -> dict:
    """Contexte commun de la fiche marin (GET + re-render erreur POST)."""
    member_id = member.id
    assigns = list(
        (
            await db.execute(
                select(CrewAssignment)
                .where(CrewAssignment.crew_member_id == member_id)
                .order_by(CrewAssignment.embark_at.desc())
            )
        )
        .scalars()
        .all()
    )
    certs = list(
        (
            await db.execute(
                select(CrewCertification).where(CrewCertification.crew_member_id == member_id)
            )
        )
        .scalars()
        .all()
    )
    leaves = list(
        (
            await db.execute(
                select(CrewLeave)
                .where(CrewLeave.crew_member_id == member_id)
                .order_by(CrewLeave.start_date.desc())
            )
        )
        .scalars()
        .all()
    )
    tickets = list(
        (
            await db.execute(
                select(CrewTicket)
                .where(CrewTicket.crew_member_id == member_id)
                .order_by(CrewTicket.departure_at.desc())
            )
        )
        .scalars()
        .all()
    )
    from app.services.leg_filter import leg_select_options

    legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()))).scalars().all())
    leg_options = await leg_select_options(db)
    # Plannings importés de Marad (lecture seule) pour ce marin.
    marad_schedules = list(
        (
            await db.execute(
                select(MaradCrewSchedule)
                .where(MaradCrewSchedule.crew_member_id == member_id)
                .order_by(MaradCrewSchedule.start_date.desc())
            )
        )
        .scalars()
        .all()
    )
    # CREW-07 — garde-fou cohérence billet/escale par affectation (alertes
    # embarquement manquant / postérieur à l'ETD / billet non chargé).
    from app.services.escale_crew import crew_assignment_alerts

    assignment_alerts = await crew_assignment_alerts(db, assigns)

    return {
        "request": request,
        "user": user,
        "member": member,
        "assignments": assigns,
        "assignment_alerts": assignment_alerts,
        "certifications": certs,
        "leaves": leaves,
        "tickets": tickets,
        "legs": legs,
        "leg_options": leg_options,
        "roles": CREW_ROLES,
        "transport_modes": TRANSPORT_MODES,
        "marad_schedules": marad_schedules,
        "embarkation_timeline": _embarkation_timeline(marad_schedules, _date.today()),
        "error": error,
    }


@router.get("/members/{member_id}", response_class=HTMLResponse)
async def crew_detail(
    member_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "C")),
) -> HTMLResponse:
    m = await db.get(CrewMember, member_id)
    if m is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "staff/crew/detail.html",
        await _member_detail_context(request, db, user, m),
    )


@router.post("/assignments/{assignment_id}/ticket")
async def crew_assignment_ticket_upload(
    assignment_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "M")),
):
    """CREW-05 — joint le billet (titre de transport) à l'embarquement."""
    from app.services.safe_files import (
        UploadRejected,
        content_length_exceeds_max,
        resolve_path,
        save_upload,
    )

    if content_length_exceeds_max(request.headers.get("content-length")):
        raise HTTPException(status_code=413, detail="fichier trop volumineux")
    a = await db.get(CrewAssignment, assignment_id)
    if a is None:
        raise HTTPException(status_code=404)
    content = await file.read()
    try:
        rel_path, mime = save_upload(content, file.filename or "billet", subdir="crew_tickets")
    except UploadRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if a.ticket_path:
        with contextlib.suppress(Exception):
            resolve_path(a.ticket_path).unlink(missing_ok=True)
    a.ticket_path = rel_path
    a.ticket_filename = file.filename
    a.ticket_mime = mime
    await db.flush()
    await activity_record(
        db,
        action="upload",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="crew",
        entity_type="crew_assignment",
        entity_id=a.id,
        entity_label=f"billet {file.filename}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/crew/members/{a.crew_member_id}", status_code=303)


@router.get("/assignments/{assignment_id}/ticket")
async def crew_assignment_ticket_download(
    assignment_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "C")),
):
    from app.services.safe_files import UploadRejected, resolve_path

    a = await db.get(CrewAssignment, assignment_id)
    if a is None or not a.ticket_path:
        raise HTTPException(status_code=404)
    try:
        path = resolve_path(a.ticket_path)
    except (UploadRejected, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="fichier introuvable") from exc
    return FileResponse(
        path,
        media_type=a.ticket_mime or "application/octet-stream",
        filename=a.ticket_filename or path.name,
    )


@router.post("/assignments/{assignment_id}/ticket/delete")
async def crew_assignment_ticket_delete(
    assignment_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "S")),
):
    from app.services.safe_files import resolve_path

    a = await db.get(CrewAssignment, assignment_id)
    if a is None:
        raise HTTPException(status_code=404)
    if a.ticket_path:
        with contextlib.suppress(Exception):
            resolve_path(a.ticket_path).unlink(missing_ok=True)
    a.ticket_path = None
    a.ticket_filename = None
    a.ticket_mime = None
    await db.flush()
    await activity_record(
        db,
        action="delete",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="crew",
        entity_type="crew_assignment",
        entity_id=a.id,
        entity_label="billet supprimé",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/crew/members/{a.crew_member_id}", status_code=303)


# ─────────────────────────── Photo d'identité marin ───────────────────────────
# Enrichissement ERP local (hors périmètre Marad, jamais écrasé par la sync).


@router.post("/members/{member_id}/photo")
async def crew_member_photo_upload(
    member_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "M")),
):
    """Ajoute / remplace la photo d'identité d'un marin (image uniquement)."""
    from app.services.safe_files import (
        UploadRejected,
        content_length_exceeds_max,
        resolve_path,
        save_upload,
    )

    if content_length_exceeds_max(request.headers.get("content-length")):
        raise HTTPException(status_code=413, detail="fichier trop volumineux")
    m = await db.get(CrewMember, member_id)
    if m is None:
        raise HTTPException(status_code=404)
    content = await file.read()
    try:
        rel_path, mime = save_upload(content, file.filename or "photo", subdir="crew_photos")
    except UploadRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not (mime or "").startswith("image/"):
        with contextlib.suppress(Exception):
            resolve_path(rel_path).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="La photo doit être une image (JPEG/PNG/WebP).")
    if m.photo_path:  # remplace l'ancienne
        with contextlib.suppress(Exception):
            resolve_path(m.photo_path).unlink(missing_ok=True)
    m.photo_path = rel_path
    m.photo_filename = file.filename
    m.photo_mime = mime
    await db.flush()
    await activity_record(
        db,
        action="upload",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="crew",
        entity_type="crew_member",
        entity_id=m.id,
        entity_label=f"photo {file.filename}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/crew/members/{member_id}", status_code=303)


@router.get("/members/{member_id}/photo")
async def crew_member_photo(
    member_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "C")),
):
    from app.services.safe_files import UploadRejected, resolve_path

    m = await db.get(CrewMember, member_id)
    if m is None or not m.photo_path:
        raise HTTPException(status_code=404)
    try:
        path = resolve_path(m.photo_path)
    except (UploadRejected, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="photo introuvable") from exc
    return FileResponse(path, media_type=m.photo_mime or "application/octet-stream")


@router.post("/members/{member_id}/photo/delete")
async def crew_member_photo_delete(
    member_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "M")),
):
    from app.services.safe_files import resolve_path

    m = await db.get(CrewMember, member_id)
    if m is None:
        raise HTTPException(status_code=404)
    if m.photo_path:
        with contextlib.suppress(Exception):
            resolve_path(m.photo_path).unlink(missing_ok=True)
    m.photo_path = None
    m.photo_filename = None
    m.photo_mime = None
    await db.flush()
    return RedirectResponse(url=f"/crew/members/{member_id}", status_code=303)


@router.post("/members/{member_id}/tickets")
async def crew_ticket_create(
    member_id: int,
    request: Request,
    mode: str = Form(...),
    reference: str | None = Form(None),
    carrier: str | None = Form(None),
    departure_at: str | None = Form(None),
    arrival_at: str | None = Form(None),
    departure_location: str | None = Form(None),
    arrival_location: str | None = Form(None),
    cost_eur: float | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "M")),
):
    if mode not in TRANSPORT_MODES:
        raise HTTPException(status_code=400, detail="invalid mode")
    if not await db.get(CrewMember, member_id):
        raise HTTPException(status_code=404)
    t = CrewTicket(
        crew_member_id=member_id,
        mode=mode,
        reference=reference,
        carrier=carrier,
        departure_at=datetime.fromisoformat(departure_at) if departure_at else None,
        arrival_at=datetime.fromisoformat(arrival_at) if arrival_at else None,
        departure_location=departure_location,
        arrival_location=arrival_location,
        cost_eur=cost_eur,
    )
    db.add(t)
    await db.flush()
    return RedirectResponse(url=f"/crew/members/{member_id}", status_code=303)


@router.post("/assignments/{assignment_id}/edit")
async def crew_assignment_edit(
    assignment_id: int,
    request: Request,
    leg_id: int = Form(...),
    role_on_board: str | None = Form(None),
    embark_at: str | None = Form(None),
    disembark_at: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "M")),
):
    """CREW-04 — édition d'une affectation (navire/leg, dates, poste)."""
    a = await db.get(CrewAssignment, assignment_id)
    if a is None:
        raise HTTPException(status_code=404)
    if await db.get(Leg, leg_id) is None:
        raise HTTPException(status_code=404, detail="leg inconnu")
    try:
        new_embark = datetime.fromisoformat(embark_at) if embark_at else None
        new_disembark = datetime.fromisoformat(disembark_at) if disembark_at else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="dates invalides") from exc
    if new_embark and new_disembark and new_embark > new_disembark:
        raise HTTPException(status_code=400, detail="débarquement avant embarquement")
    # CREW-08 — anti-overlap aussi à l'édition (en s'excluant soi-même).
    overlap = await _find_overlap(
        db,
        member_id=a.crew_member_id,
        embark=new_embark,
        disembark=new_disembark,
        exclude_id=a.id,
    )
    if overlap is not None:
        raise HTTPException(
            status_code=400, detail=f"chevauchement avec l'affectation {overlap.id}"
        )
    a.leg_id = leg_id
    a.role_on_board = (role_on_board or "").strip() or None
    a.embark_at = new_embark
    a.disembark_at = new_disembark
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="crew",
        entity_type="crew_assignment",
        entity_id=a.id,
        entity_label=f"member={a.crew_member_id} leg={leg_id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/crew/members/{a.crew_member_id}", status_code=303)


@router.post("/assignments/{assignment_id}/delete")
async def crew_assignment_delete(
    assignment_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "S")),
):
    """CREW-04 — suppression d'une affectation."""
    a = await db.get(CrewAssignment, assignment_id)
    if a is None:
        raise HTTPException(status_code=404)
    member_id = a.crew_member_id
    await db.delete(a)
    await db.flush()
    await activity_record(
        db,
        action="delete",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="crew",
        entity_type="crew_assignment",
        entity_id=assignment_id,
        entity_label=f"assignment {assignment_id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/crew/members/{member_id}", status_code=303)


@router.get("/border-police/{vessel_id}")
async def crew_border_police_pdf(
    vessel_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "C")),
):
    """CREW-02 — liste d'équipage bilingue (FR/EN) pour la police aux frontières.

    Recense les marins embarqués sur le navire (affectations en cours, sur un
    leg du navire) et produit un PDF WeasyPrint à présenter à la PAF / autorités.
    """
    from fastapi.responses import Response
    from weasyprint import HTML

    from app.config import settings

    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404)

    now = datetime.now(UTC)
    legs = {
        leg.id: leg
        for leg in (await db.execute(select(Leg).where(Leg.vessel_id == vessel_id))).scalars().all()
    }
    assigns = list(
        (
            await db.execute(
                select(CrewAssignment)
                .where(CrewAssignment.leg_id.in_(list(legs.keys()) or [-1]))
                .where(CrewAssignment.embark_at.is_not(None))
                .where(CrewAssignment.embark_at <= now)  # déjà à bord
                .where(
                    (CrewAssignment.disembark_at.is_(None)) | (CrewAssignment.disembark_at > now)
                )
            )
        )
        .scalars()
        .all()
    )
    seen: set[int] = set()
    rows = []
    for a in assigns:
        if a.crew_member_id in seen:
            continue
        seen.add(a.crew_member_id)
        m = await db.get(CrewMember, a.crew_member_id)
        if m is not None:
            rows.append({"member": m, "assignment": a})
    rows.sort(key=lambda r: r["member"].full_name)
    foreign_count = sum(
        1 for r in rows if (r["member"].nationality or "").upper() not in ("FR", "")
    )

    tpl = templates.get_template("pdf/crew_list.html")
    html = tpl.render(
        vessel=vessel,
        rows=rows,
        foreign_count=foreign_count,
        issued_at=now,
        brand=brand_for_lang("fr"),
        site_url=settings.site_url,
    )
    pdf = HTML(string=html, base_url=settings.site_url).write_pdf()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="CrewList_{vessel.code}.pdf"'},
    )


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )
