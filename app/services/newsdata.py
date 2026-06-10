"""Client NewsData.io — agrégateur d'actualités tiers.

Phase 1 de la veille : ingestion *brute* (sans IA). On interroge l'endpoint
``/api/1/latest`` avec les mots-clés / pays / langues d'une ``NewsSource`` et
on normalise les articles renvoyés.

La clé d'API est lue depuis ``settings.newsdata_api_key`` (env
``NEWSDATA_API_KEY``). Si elle n'est pas configurée, le client lève
``NewsDataNotConfigured`` — l'appelant renvoie alors 503 (même politique que
l'API tracking).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("veille")

DEFAULT_BASE_URL = "https://newsdata.io/api/1/latest"
REQUEST_TIMEOUT = 15.0


class NewsDataError(RuntimeError):
    """Erreur d'appel à NewsData (réseau, quota, payload invalide)."""


class NewsDataNotConfigured(NewsDataError):
    """NEWSDATA_API_KEY absente du .env."""


def is_configured() -> bool:
    return bool((settings.newsdata_api_key or "").strip())


def _parse_pub_date(value: Any) -> datetime | None:
    """NewsData renvoie ``pubDate`` au format 'YYYY-MM-DD HH:MM:SS' (UTC)."""
    if not value:
        return None
    s = str(value).strip()
    iso = s.replace(" ", "T").replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _first(value: Any) -> str | None:
    """NewsData renvoie country/category/language tantôt en liste, tantôt en str."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else None
    return str(value)


def _external_id(article: dict) -> str:
    """Clé de dédup : article_id NewsData, sinon SHA-256 du lien."""
    aid = article.get("article_id")
    if aid:
        return str(aid)[:80]
    link = article.get("link") or article.get("title") or ""
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:80]


def normalize(article: dict) -> dict[str, Any]:
    """Transforme un article NewsData brut en dict prêt pour ``NewsItem``."""
    return {
        "external_id": _external_id(article),
        "title": (article.get("title") or "(sans titre)")[:500],
        "link": (article.get("link") or "")[:1000],
        "description": article.get("description"),
        "publisher": (article.get("source_id") or article.get("source_name") or "")[:200] or None,
        "image_url": (article.get("image_url") or None),
        "language": _first(article.get("language")),
        "country": _first(article.get("country")),
        "category": _first(article.get("category")),
        "pub_date": _parse_pub_date(article.get("pubDate")),
    }


async def fetch_latest(
    *,
    query: str,
    countries: str | None = None,
    languages: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Interroge NewsData et renvoie une liste d'articles normalisés.

    Lève ``NewsDataNotConfigured`` si la clé est absente, ``NewsDataError``
    en cas d'échec réseau ou de réponse non-``success``.
    """
    api_key = (settings.newsdata_api_key or "").strip()
    if not api_key:
        raise NewsDataNotConfigured("NEWSDATA_API_KEY non configurée dans .env")

    params: dict[str, str] = {"apikey": api_key}
    if query:
        params["q"] = query
    if countries:
        params["country"] = countries.replace(" ", "")
    if languages:
        params["language"] = languages.replace(" ", "")
    if category:
        params["category"] = category.strip()

    base_url = (settings.newsdata_base_url or DEFAULT_BASE_URL).strip()
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(base_url, params=params)
    except httpx.HTTPError as exc:
        raise NewsDataError(f"appel NewsData échoué : {exc}") from exc

    if resp.status_code != 200:
        raise NewsDataError(f"NewsData HTTP {resp.status_code} : {resp.text[:200]}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise NewsDataError("réponse NewsData non-JSON") from exc

    if payload.get("status") != "success":
        raise NewsDataError(f"NewsData status={payload.get('status')} : {payload.get('results')}")

    results = payload.get("results") or []
    return [normalize(a) for a in results if isinstance(a, dict)]
