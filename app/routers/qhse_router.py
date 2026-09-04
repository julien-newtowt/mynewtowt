"""QHSE — hub minimal + import Excel (Phase 0.5, écran de test).

Écran volontairement minimal : la Phase 0 (fondations données, cf.
``app.services.qhse_ingestion``) ne prévoyait aucun écran, mais un point
d'entrée navigateur est nécessaire pour tester l'import sans script manuel.
Les workspaces par rôle (scorecard, CAPA, explorateur — cf.
``QHSE_Dashboard_UX_Wireframes.md``) restent Phase 1 MVP, pas construits ici.

Même séquence de sécurité qu'un upload MRV (``mrv_flgo_import`` dans
``mrv_router.py``) : ``content_length_exceeds_max`` sur l'en-tête AVANT de
lire le corps, puis ``validate_filename``/``validate_size`` sur le contenu.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.crew import CrewMember
from app.models.qhse import QhseReport
from app.models.user import User
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.qhse_ingestion import QhseIngestionError, import_qhse_xlsx
from app.services.qhse_kpi import build_dashboard, list_vessels_with_reports, trend_bars
from app.services.safe_files import content_length_exceeds_max
from app.templating import templates
from app.utils.file_validation import validate_filename, validate_size

router = APIRouter(prefix="/qhse", tags=["qhse"])

#: Nombre de lignes non importées détaillées dans la trace d'audit. Au-delà, la
#: troncature est **annoncée** dans le message : une liste coupée en silence ferait
#: croire à un inventaire complet des pertes.
_MAX_LOGGED_ROWS = 40


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def qhse_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("qhse", "C")),
) -> HTMLResponse:
    """Hub QHSE — compteur de rapports importés + formulaire d'import (qhse:M)."""
    total_reports = (await db.execute(select(func.count()).select_from(QhseReport))).scalar_one()
    return templates.TemplateResponse(
        "staff/qhse/index.html",
        {"request": request, "user": user, "total_reports": total_reports},
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def qhse_dashboard(
    request: Request,
    vessel_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("qhse", "C")),
) -> HTMLResponse:
    """Tableau de bord — premier lot visuel (Phase 1), cf. ``app.services.qhse_kpi``.

    Vue flotte par défaut ; ``?vessel_id=`` filtre sur un navire (parmi ceux
    qui ont au moins un signalement — inutile de lister un navire sans
    donnée dans le sélecteur).

    ⚠️ Route littérale déclarée AVANT ``/{report_id}`` : sinon FastAPI la
    capturerait comme un ``report_id`` invalide (même règle que le module
    vente à bord, verrouillée par un test là-bas).
    """
    vessels = await list_vessels_with_reports(db)
    if vessel_id is not None and vessel_id not in {v.id for v in vessels}:
        vessel_id = None  # filtre sur un navire sans donnée : repli silencieux sur la flotte
    dashboard = await build_dashboard(db, vessel_id=vessel_id)
    bars, chart = trend_bars(dashboard.trend)
    return templates.TemplateResponse(
        "staff/qhse/dashboard.html",
        {
            "request": request,
            "user": user,
            "dashboard": dashboard,
            "vessels": vessels,
            "selected_vessel_id": vessel_id,
            "trend_bars": bars,
            "trend_chart": chart,
        },
    )


@router.get("/import")
async def qhse_import_get() -> RedirectResponse:
    """Renvoie vers le hub — l'import est un POST, le formulaire vit sur ``/qhse``.

    Sans cette route, un GET sur ``/qhse/import`` (URL tapée, rechargement après
    envoi, lien collé) tombe sur ``/qhse/{report_id}`` et répond un 422 JSON
    « Input should be a valid integer » : illisible, et surtout impossible à
    distinguer d'une panne réelle de l'import — c'est ce qui a brouillé le
    diagnostic de l'incident du 2026-09-04. Déclarée **avant** ``/{report_id}``,
    même règle d'ordre que le module vente à bord.
    """
    return RedirectResponse(url="/qhse", status_code=303)


@router.post("/import", response_class=HTMLResponse)
async def qhse_import(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("qhse", "M")),
) -> HTMLResponse:
    """Import/réconciliation xlsx (un ou plusieurs navires par fichier, résolus par ligne).

    Jamais d'exception non gérée sur un contenu malformé — les anomalies
    (navire non résolu, dates incohérentes, motif de test) sont collectées
    dans le rapport (cf. ``qhse_ingestion.import_qhse_xlsx``).

    Une ligne déjà connue (``source_code``, D10) est **mise à jour**, pas
    dupliquée — un ré-import du même fichier, ou d'un export ultérieur dont le
    workflow CAPA a progressé, rafraîchit le registre au lieu de le doubler.
    """
    if content_length_exceeds_max(request.headers.get("content-length")):
        raise HTTPException(status_code=413, detail="fichier trop volumineux")

    name_check = validate_filename(file.filename or "")
    if not name_check.ok:
        raise HTTPException(status_code=400, detail=name_check.reason)
    content = await file.read()
    size_check = validate_size(content)
    if not size_check.ok:
        raise HTTPException(status_code=413, detail=size_check.reason)

    try:
        report = await import_qhse_xlsx(
            db, content, filename=file.filename, imported_by_user_id=user.id
        )
    except QhseIngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 🔴 La PERTE est persistée, pas seulement comptée.
    #
    # L'ancienne trace n'écrivait que des nombres (« ignorés=12 ») : le détail des
    # lignes écartées vivait dans la réponse HTTP et disparaissait avec l'onglet.
    # Une non-conformité ISM perdue devenait donc introuvable — impossible de
    # savoir, un mois plus tard, laquelle manquait ni pourquoi.
    #
    # `activity_logs` est append-only : c'est le bon support, et il évite une table
    # dédiée. Troncature **annoncée** si la liste est longue — jamais silencieuse.
    detail = (
        f"créés={report.imported} mis_à_jour={report.updated} ignorés={report.skipped} "
        f"marqués_test_présumé={report.flagged}"
    )
    lost = report.errors
    if lost:
        shown = lost[:_MAX_LOGGED_ROWS]
        detail += " | LIGNES NON IMPORTÉES : " + " ; ".join(shown)
        if len(lost) > _MAX_LOGGED_ROWS:
            detail += f" ; … et {len(lost) - _MAX_LOGGED_ROWS} autres (tronqué)"
    if report.warnings:
        detail += f" | {len(report.warnings)} importées à confirmer (test présumé)"
    await activity_record(
        db,
        action="import",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="qhse",
        entity_type="qhse_report",
        entity_label=file.filename or "qhse import",
        detail=detail,
        ip_address=_client_ip(request),
    )
    return templates.TemplateResponse(
        "staff/qhse/import_result.html",
        {"request": request, "user": user, "report": report},
    )


@router.get("/{report_id}", response_class=HTMLResponse)
async def qhse_detail(
    report_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("qhse", "C")),
) -> HTMLResponse:
    """Fiche d'un signalement — lecture seule (Phase 1). Point d'arrivée de
    la liste « ouverts » du tableau de bord (§10.2 : 1 clic jusqu'au détail).

    ``selectinload`` sur les deux workflows 1:0..1 : un accès paresseux sur
    une relation ORM échouerait en async hors contexte de requête déjà
    ouverte (``MissingGreenlet``) — même précaution que partout ailleurs
    dans le dépôt sur ce pattern.
    """
    stmt = (
        select(QhseReport)
        .options(
            selectinload(QhseReport.corrective_action),
            selectinload(QhseReport.root_cause_evaluation),
        )
        .where(QhseReport.id == report_id)
    )
    report = (await db.execute(stmt)).scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=404, detail="signalement introuvable")

    vessel = (
        await db.execute(select(Vessel).where(Vessel.id == report.vessel_id))
    ).scalar_one_or_none()

    async def _person(user_id: int | None, crew_id: int | None = None) -> str | None:
        if user_id is not None:
            u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if u:
                return u.full_name or u.username
        if crew_id is not None:
            c = (
                await db.execute(select(CrewMember).where(CrewMember.id == crew_id))
            ).scalar_one_or_none()
            if c:
                return c.full_name
        return None

    reporter = await _person(report.reporter_user_id, report.reporter_crew_member_id)
    description_added_by = await _person(report.description_added_by_user_id)
    corrective_responsible = None
    if report.corrective_action:
        corrective_responsible = await _person(report.corrective_action.responsible_user_id)
    evaluation_responsible = None
    if report.root_cause_evaluation:
        evaluation_responsible = await _person(report.root_cause_evaluation.responsible_user_id)

    return templates.TemplateResponse(
        "staff/qhse/detail.html",
        {
            "request": request,
            "user": user,
            "report": report,
            "vessel": vessel,
            "reporter": reporter or report.issued_by_raw,
            "description_added_by": description_added_by,
            "corrective_responsible": corrective_responsible,
            "evaluation_responsible": evaluation_responsible,
        },
    )
