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


#: Borne de sécurité du listing d'organisations — un garde-fou contre une
#: pagination qui ne s'arrêterait pas, **pas** un critère de sélection.
#:
#: ⚠️ Elle valait 1 000, et le CRM en compte davantage : la synchronisation
#: n'examinait donc que les 1 000 premières organisations et en ignorait le
#: reste **sans le dire** (constaté le 2026-09-10 : 51 organisations portant un
#: deal, 16 clients en base, `total` de sync exactement égal à 1 000).
#: L'exhaustivité ne repose plus sur cette borne : ``sync_clients`` complète le
#: listing en allant chercher par identifiant toute organisation portant un
#: deal qu'il n'a pas vue passer.
ORG_LIST_MAX_ITEMS = 50_000


async def list_organizations(*, max_items: int = ORG_LIST_MAX_ITEMS) -> list[dict]:
    """Liste paginée des organisations Pipedrive (≤ ``max_items``).

    Renvoie une liste de dicts org (clés Pipedrive : id, name, address,
    address_country, owner_id…). Liste vide si non configuré ou en erreur.

    Atteindre ``max_items`` est **journalisé en avertissement** : une troncature
    muette fait disparaître des clients sans qu'aucun compteur ne bouge.
    """
    out: list[dict] = []
    start = 0
    # 500 = plafond de `limit` de l'API, déjà utilisé par `list_deals` et
    # `list_persons`. À 100, parcourir un CRM de plusieurs milliers
    # d'organisations coûtait cinq fois plus de requêtes pour le même résultat.
    page = 500
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
    if len(out) >= max_items:
        logger.warning(
            "pipedrive: listing des organisations tronqué à %d — le CRM en compte davantage",
            max_items,
        )
    return out[:max_items]


async def get_organization(org_id: int) -> dict | None:
    """Une organisation par son identifiant Pipedrive, ou ``None``.

    Sert à **compléter** le listing : une organisation portant un deal doit
    remonter en client, qu'elle soit ou non passée dans la pagination.
    """
    if not org_id:
        return None
    data = await _request("GET", f"/organizations/{int(org_id)}")
    if not data or not data.get("success"):
        return None
    return data.get("data") or None


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


async def list_persons(*, max_items: int = 20000) -> list[dict]:
    """Liste paginée des personnes Pipedrive (contacts).

    Sert à remonter le **contact** d'une organisation (nom, e-mail, téléphone)
    dans la fiche client : sans elle, la fiche affichait un bloc « Contact »
    vide alors que le CRM le connaît. Une seule liste globale, groupée ensuite
    par ``org_id`` — un appel par organisation ferait des centaines de requêtes
    pour la même information.
    """
    out: list[dict] = []
    start = 0
    page = 500
    while len(out) < max_items:
        data = await _request("GET", "/persons", params={"start": start, "limit": page})
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


def primary_value(entries: Any) -> str | None:
    """Valeur principale d'un champ Pipedrive multi-valeurs (e-mail, téléphone).

    Pipedrive renvoie ``[{"value": …, "primary": true}, …]``, parfois une chaîne
    simple selon la version d'API. On retient l'entrée marquée principale, sinon
    la première non vide.
    """
    if isinstance(entries, str):
        return entries.strip() or None
    if not isinstance(entries, list):
        return None
    values = [e for e in entries if isinstance(e, dict) and (e.get("value") or "").strip()]
    if not values:
        return None
    for entry in values:
        if entry.get("primary"):
            return str(entry["value"]).strip()
    return str(values[0]["value"]).strip()


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
