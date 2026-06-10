"""Veille — orchestration de l'ingestion des sources NewsData.

``ingest_all`` parcourt les sources actives, interroge l'agrégateur, et
upsert les articles neufs (dédup sur ``external_id``). Appelé soit par le
bouton « Rafraîchir » de l'admin (permission M), soit par l'endpoint
``POST /api/veille/refresh`` (déclenché en cron par Power Automate).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.news_item import NewsItem
from app.models.news_source import NewsSource
from app.services import newsdata

logger = logging.getLogger("veille")


async def ingest_source(db: AsyncSession, source: NewsSource) -> dict:
    """Ingère une source. Renvoie {inserted, skipped, fetched}."""
    articles = await newsdata.fetch_latest(
        query=source.query,
        countries=source.countries,
        languages=source.languages,
        category=source.category,
    )
    if not articles:
        return {"source": source.name, "fetched": 0, "inserted": 0, "skipped": 0}

    ext_ids = [a["external_id"] for a in articles]
    existing = set(
        (await db.execute(select(NewsItem.external_id).where(NewsItem.external_id.in_(ext_ids))))
        .scalars()
        .all()
    )

    inserted = 0
    skipped = 0
    seen: set[str] = set()
    for art in articles:
        eid = art["external_id"]
        if eid in existing or eid in seen:
            skipped += 1
            continue
        seen.add(eid)
        if not art.get("link"):
            skipped += 1
            continue
        db.add(NewsItem(source_id=source.id, **art))
        inserted += 1

    await db.flush()
    return {
        "source": source.name,
        "fetched": len(articles),
        "inserted": inserted,
        "skipped": skipped,
    }


async def ingest_all(db: AsyncSession) -> dict:
    """Ingère toutes les sources actives. Tolérant : une source en échec
    n'interrompt pas les autres (l'erreur est consignée et remontée)."""
    sources = list(
        (await db.execute(select(NewsSource).where(NewsSource.enabled.is_(True)))).scalars().all()
    )

    per_source: list[dict] = []
    errors: list[str] = []
    total_inserted = 0
    for src in sources:
        try:
            res = await ingest_source(db, src)
            per_source.append(res)
            total_inserted += res["inserted"]
        except newsdata.NewsDataError as exc:
            logger.warning("Veille: source %s en échec : %s", src.name, exc)
            errors.append(f"{src.name}: {exc}")

    return {
        "sources": len(sources),
        "inserted": total_inserted,
        "details": per_source,
        "errors": errors,
    }
