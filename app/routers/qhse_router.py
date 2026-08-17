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
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.qhse import QhseReport
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.qhse_ingestion import QhseIngestionError, import_qhse_xlsx
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


@router.post("/import", response_class=HTMLResponse)
async def qhse_import(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("qhse", "M")),
) -> HTMLResponse:
    """Import xlsx (un ou plusieurs navires par fichier, résolus par ligne).

    Jamais d'exception non gérée sur un contenu malformé — les anomalies
    (navire non résolu, dates incohérentes, motif de test) sont collectées
    dans le rapport (cf. ``qhse_ingestion.import_qhse_xlsx``).

    Limite Phase 0 assumée : pas de déduplication — ré-importer le même
    fichier crée de nouveaux rapports plutôt que de les mettre à jour
    (contrairement à l'upsert idempotent de ``flgo_sync``). Affiché dans le
    template de résultat.
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
        report = await import_qhse_xlsx(db, content)
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
        f"import={report.imported} ignorés={report.skipped} "
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
