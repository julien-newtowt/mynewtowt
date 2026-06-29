"""Veille d'actualité — espace staff (transport maritime, voile, Brésil,
réglementation, international).

Phase 1 (flux brut, sans IA) :
- ``GET  /veille``                 fil d'actualité filtré par rôle + recherche
- ``GET  /veille/sources``         admin des sources (requêtes NewsData)
- ``POST /veille/sources``         création d'une source
- ``POST /veille/sources/{id}/toggle``   activer/désactiver
- ``POST /veille/sources/{id}/delete``   suppression (permission S)
- ``POST /veille/{id}/pin``        épingler / désépingler un article
- ``POST /veille/{id}/archive``    archiver un article
- ``POST /veille/refresh``         rafraîchissement manuel (permission M)

Endpoint machine (Power Automate, cron externe) :
- ``POST /api/veille/refresh``     header ``X-API-Token: <VEILLE_API_TOKEN>``
"""

from __future__ import annotations

import logging
import secrets as _secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.news_item import NewsItem
from app.models.news_source import NewsSource
from app.permissions import ROLES, can_edit, require_permission
from app.services import news_ingest, newsdata
from app.services.activity import record as activity_record
from app.templating import templates

logger = logging.getLogger("veille")

router = APIRouter(prefix="/veille", tags=["veille"])
api_router = APIRouter(prefix="/api/veille", tags=["veille-api"])


def _hx_or_redirect(request: Request, target: str):
    if request.headers.get("hx-request"):
        from fastapi.responses import Response

        return Response(status_code=200, headers={"HX-Redirect": target})
    return RedirectResponse(url=target, status_code=303)


# ────────────────────────────── Fil d'actualité ─────────────────────────


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def veille_index(
    request: Request,
    q: str | None = None,
    source_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("veille", "C")),
) -> HTMLResponse:
    # Sources visibles par ce rôle (ciblage NULL/"" = tout le staff).
    src_stmt = (
        select(NewsSource)
        .where(
            or_(
                NewsSource.target_roles.is_(None),
                NewsSource.target_roles == "",
                NewsSource.target_roles.like(f"%{user.role}%"),
            )
        )
        .order_by(NewsSource.name)
    )
    sources = list((await db.execute(src_stmt)).scalars().all())
    visible_ids = [s.id for s in sources]

    items: list[NewsItem] = []
    if visible_ids:
        stmt = (
            select(NewsItem)
            .where(NewsItem.source_id.in_(visible_ids))
            .where(NewsItem.is_archived.is_(False))
        )
        if source_id:
            stmt = stmt.where(NewsItem.source_id == source_id)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(NewsItem.title.ilike(like), NewsItem.description.ilike(like)))
        stmt = stmt.order_by(
            NewsItem.is_pinned.desc(), NewsItem.pub_date.desc().nulls_last()
        ).limit(120)
        items = list((await db.execute(stmt)).scalars().all())

    source_names = {s.id: s.name for s in sources}

    # EVO-04 — priorité par item : score IA (ai_score) si présent, sinon
    # repli sur le scoring heuristique (lot 70). ``ai`` signale l'origine.
    from app.services.news_scoring import priority_label, score_news_item

    scores = {}
    for it in items:
        if it.ai_score is not None:
            s = it.ai_score
            origin = True
        else:
            s = score_news_item(it.title, it.description)
            origin = False
        scores[it.id] = {"score": s, "label": priority_label(s), "ai": origin}

    # EVO-04 — synthèse du jour (digest IA), si générée au dernier cron.
    from datetime import UTC, datetime

    from app.models.news_digest import NewsDigest

    today = datetime.now(UTC).date()
    digest = (
        await db.execute(
            select(NewsDigest).where(NewsDigest.day == today, NewsDigest.lang == "fr").limit(1)
        )
    ).scalar_one_or_none()

    return templates.TemplateResponse(
        "staff/veille/index.html",
        {
            "request": request,
            "user": user,
            "items": items,
            "sources": sources,
            "source_names": source_names,
            "scores": scores,
            "digest": digest,
            "filter_source_id": source_id,
            "q": q or "",
            "configured": newsdata.is_configured(),
            "can_manage": can_edit(user.role, "veille"),
        },
    )


# ──────────────────────────────── Admin sources ─────────────────────────


@router.get("/sources", response_class=HTMLResponse)
async def veille_sources(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("veille", "M")),
) -> HTMLResponse:
    sources = list((await db.execute(select(NewsSource).order_by(NewsSource.name))).scalars().all())
    return templates.TemplateResponse(
        "staff/veille/sources.html",
        {
            "request": request,
            "user": user,
            "sources": sources,
            "roles": ROLES,
            "configured": newsdata.is_configured(),
        },
    )


@router.post("/sources")
async def veille_source_create(
    request: Request,
    name: str = Form(...),
    query: str = Form(...),
    countries: str | None = Form(None),
    languages: str | None = Form(None),
    category: str | None = Form(None),
    target_roles: list[str] = Form(default=[]),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("veille", "M")),
):
    src = NewsSource(
        name=name.strip()[:120],
        query=query.strip()[:500],
        countries=(countries or "").strip()[:120] or None,
        languages=(languages or "").strip()[:120] or None,
        category=(category or "").strip()[:60] or None,
        target_roles=",".join(r for r in target_roles if r in ROLES) or None,
        created_by_id=user.id,
    )
    db.add(src)
    await db.flush()
    await activity_record(
        db,
        action="veille_source_create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="veille",
        entity_type="news_source",
        entity_id=src.id,
        entity_label=src.name,
    )
    return _hx_or_redirect(request, "/veille/sources")


@router.post("/sources/{src_id}/toggle")
async def veille_source_toggle(
    request: Request,
    src_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("veille", "M")),
):
    src = (await db.execute(select(NewsSource).where(NewsSource.id == src_id))).scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail="Source introuvable")
    src.enabled = not src.enabled
    await db.flush()
    await activity_record(
        db,
        action="veille_source_toggle",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="veille",
        entity_type="news_source",
        entity_id=src.id,
        entity_label=src.name,
        detail=f"enabled={src.enabled}",
    )
    return _hx_or_redirect(request, "/veille/sources")


@router.post("/sources/{src_id}/delete")
async def veille_source_delete(
    request: Request,
    src_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("veille", "S")),
):
    src = (await db.execute(select(NewsSource).where(NewsSource.id == src_id))).scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail="Source introuvable")
    label = src.name
    await db.delete(src)
    await db.flush()
    await activity_record(
        db,
        action="veille_source_delete",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="veille",
        entity_type="news_source",
        entity_id=src_id,
        entity_label=label,
    )
    return _hx_or_redirect(request, "/veille/sources")


# ────────────────────────────── Actions article ─────────────────────────


@router.post("/{item_id}/pin")
async def veille_item_pin(
    request: Request,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("veille", "M")),
):
    item = (await db.execute(select(NewsItem).where(NewsItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Article introuvable")
    item.is_pinned = not item.is_pinned
    await db.flush()
    return _hx_or_redirect(request, "/veille")


@router.post("/{item_id}/archive")
async def veille_item_archive(
    request: Request,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("veille", "M")),
):
    item = (await db.execute(select(NewsItem).where(NewsItem.id == item_id))).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Article introuvable")
    item.is_archived = True
    await db.flush()
    return _hx_or_redirect(request, "/veille")


# ─────────────────────────── Rafraîchissement manuel ────────────────────


@router.post("/refresh")
async def veille_refresh_manual(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("veille", "M")),
):
    if not newsdata.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NEWSDATA_API_KEY non configurée dans .env",
        )
    result = await news_ingest.ingest_all(db)
    # EVO-04 — enrichissement IA (score affiné + digest du jour). No-op sans clé.
    from app.services import news_ai

    result["ai"] = await news_ai.enrich_after_ingest(db)
    await activity_record(
        db,
        action="veille_refresh",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="veille",
        entity_type="news",
        detail=result,
    )
    return _hx_or_redirect(request, "/veille")


# ─────────────────── Endpoint machine — Power Automate (cron) ────────────


def _expected_token() -> str | None:
    return (settings.veille_api_token or "").strip() or None


@api_router.post("/refresh")
async def veille_refresh_api(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Déclenché en cron externe (Power Automate). Auth par X-API-Token."""
    expected = _expected_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VEILLE_API_TOKEN non configuré dans .env",
        )
    received = request.headers.get("x-api-token") or ""
    if not _secrets.compare_digest(received.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=403, detail="X-API-Token invalide ou absent")

    if not newsdata.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NEWSDATA_API_KEY non configurée dans .env",
        )
    result = await news_ingest.ingest_all(db)
    # EVO-04 — enrichissement IA (score affiné + digest du jour). No-op sans clé.
    from app.services import news_ai

    result["ai"] = await news_ai.enrich_after_ingest(db)
    logger.info("Veille refresh (API): %s", result)
    return JSONResponse(result)
