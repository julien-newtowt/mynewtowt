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


async def _request(method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> dict | None:
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
    data = await _request("GET", "/organizations/search", params={"term": name, "exact_match": "true"})
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


async def create_deal(title: str, *, org_id: int | None = None, value: float | None = None, currency: str = "EUR") -> dict | None:
    payload: dict[str, Any] = {"title": title, "currency": currency}
    if org_id:
        payload["org_id"] = org_id
    if value is not None:
        payload["value"] = value
    data = await _request("POST", "/deals", json=payload)
    return (data or {}).get("data")


async def ping() -> bool:
    """Quick connectivity check for the admin Settings page."""
    if not _enabled():
        return False
    data = await _request("GET", "/users/me")
    return bool(data and data.get("success"))
