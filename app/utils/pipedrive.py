"""Pipedrive CRM sync — light HTTP client.

Configuration via env :
- PIPEDRIVE_API_TOKEN
- PIPEDRIVE_BASE_URL (default: https://api.pipedrive.com/v1)

Si le token n'est pas configuré, les fonctions sont des no-ops. Cela
permet à l'ERP de tourner en local sans dépendance externe.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PIPEDRIVE_BASE_URL = os.getenv("PIPEDRIVE_BASE_URL", "https://api.pipedrive.com/v1").rstrip("/")
PIPEDRIVE_API_TOKEN = (os.getenv("PIPEDRIVE_API_TOKEN") or "").strip() or None
_TIMEOUT = 8.0


def _enabled() -> bool:
    return PIPEDRIVE_API_TOKEN is not None


def enabled() -> bool:
    """Public : True si un token Pipedrive est configuré (.env)."""
    return _enabled()


async def list_organizations(*, max_items: int = 1000) -> list[dict]:
    """Liste paginée des organisations Pipedrive (≤ ``max_items``).

    Renvoie une liste de dicts org (clés Pipedrive : id, name, address,
    address_country, owner_id…). Liste vide si non configuré ou en erreur.
    """
    out: list[dict] = []
    start = 0
    page = 100
    while len(out) < max_items:
        data = await _request("GET", "/organizations", params={"start": start, "limit": page})
        if not data or not data.get("success"):
            break
        rows = data.get("data") or []
        if not rows:
            break
        out.extend(rows)
        pagination = (data.get("additional_data") or {}).get("pagination") or {}
        if not pagination.get("more_items_in_collection"):
            break
        start = pagination.get("next_start") or (start + page)
    return out[:max_items]


async def list_deals(*, max_items: int = 10000) -> list[dict]:
    """Liste paginée de TOUS les deals (tous pipelines, tous statuts).

    ``status=all_not_deleted`` couvre les deals ouverts, gagnés et perdus,
    quel que soit le pipeline — ce qui permet de remonter toute organisation
    ayant au moins un deal. Liste vide si non configuré ou en erreur.
    """
    out: list[dict] = []
    start = 0
    page = 500
    while len(out) < max_items:
        data = await _request(
            "GET",
            "/deals",
            params={"start": start, "limit": page, "status": "all_not_deleted"},
        )
        if not data or not data.get("success"):
            break
        rows = data.get("data") or []
        if not rows:
            break
        out.extend(rows)
        pagination = (data.get("additional_data") or {}).get("pagination") or {}
        if not pagination.get("more_items_in_collection"):
            break
        start = pagination.get("next_start") or (start + page)
    return out[:max_items]


async def _request(
    method: str, path: str, *, json: dict | None = None, params: dict | None = None
) -> dict | None:
    if not _enabled():
        return None
    p = dict(params or {})
    p["api_token"] = PIPEDRIVE_API_TOKEN
    url = f"{PIPEDRIVE_BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.request(method, url, params=p, json=json)
            if r.status_code >= 400:
                logger.warning("pipedrive %s %s → %d %s", method, path, r.status_code, r.text[:200])
                return None
            return r.json() if r.content else None
    except httpx.HTTPError as e:
        logger.warning("pipedrive %s %s failed: %s", method, path, e)
        return None


async def find_organization(name: str) -> dict | None:
    """Search Pipedrive for an org by exact-name. Returns dict or None."""
    if not name:
        return None
    data = await _request(
        "GET", "/organizations/search", params={"term": name, "exact_match": "true"}
    )
    if not data or not data.get("success"):
        return None
    items = (data.get("data") or {}).get("items") or []
    return items[0].get("item") if items else None


async def create_organization(name: str, **extra: Any) -> dict | None:
    payload: dict[str, Any] = {"name": name}
    payload.update(extra or {})
    data = await _request("POST", "/organizations", json=payload)
    return (data or {}).get("data")


async def find_or_create_organization(name: str, **extra: Any) -> dict | None:
    org = await find_organization(name)
    if org:
        return org
    return await create_organization(name, **extra)


async def create_deal(
    title: str,
    *,
    org_id: int | None = None,
    value: float | None = None,
    currency: str = "EUR",
    pipeline_id: int | None = None,
    stage_id: int | None = None,
) -> dict | None:
    payload: dict[str, Any] = {"title": title, "currency": currency}
    if org_id:
        payload["org_id"] = org_id
    if value is not None:
        payload["value"] = value
    if pipeline_id:
        payload["pipeline_id"] = pipeline_id
    if stage_id:
        payload["stage_id"] = stage_id
    data = await _request("POST", "/deals", json=payload)
    return (data or {}).get("data")


async def find_pipeline_id(name: str) -> int | None:
    """Résout l'``id`` d'un pipeline Pipedrive par son nom (insensible à la casse).

    Renvoie ``None`` si non configuré, introuvable ou en erreur.
    """
    if not name:
        return None
    data = await _request("GET", "/pipelines")
    if not data or not data.get("success"):
        return None

    # Comparaison tolérante aux espaces/casse : « Deals from web » doit
    # matcher « Dealsfromweb », « deals from web », etc.
    def _norm(s: str) -> str:
        return "".join((s or "").split()).lower()

    target = _norm(name)
    for p in data.get("data") or []:
        if _norm(p.get("name") or "") == target:
            return p.get("id")
    return None


async def first_stage_id(pipeline_id: int) -> int | None:
    """Premier étage (plus petit ``order_nr``) d'un pipeline."""
    if not pipeline_id:
        return None
    data = await _request("GET", "/stages", params={"pipeline_id": pipeline_id})
    if not data or not data.get("success"):
        return None
    stages = [s for s in (data.get("data") or []) if s.get("id")]
    if not stages:
        return None
    stages.sort(key=lambda s: s.get("order_nr") or 0)
    return stages[0].get("id")


async def add_note(
    content: str, *, deal_id: int | None = None, org_id: int | None = None
) -> dict | None:
    """Crée une note Pipedrive rattachée à un deal et/ou une organisation.

    ``content`` accepte du HTML simple (Pipedrive l'affiche tel quel).
    """
    if not (content or "").strip():
        return None
    payload: dict[str, Any] = {"content": content}
    if deal_id:
        payload["deal_id"] = deal_id
    if org_id:
        payload["org_id"] = org_id
    data = await _request("POST", "/notes", json=payload)
    return (data or {}).get("data")


async def ping() -> bool:
    """Quick connectivity check for the admin Settings page."""
    if not _enabled():
        return False
    data = await _request("GET", "/users/me")
    return bool(data and data.get("success"))
