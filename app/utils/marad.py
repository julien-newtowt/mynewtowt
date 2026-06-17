"""Marad (MaraSoft « Generic API ») — client HTTP **LECTURE SEULE**.

Intégration des données crew de Marad vers mynewtowt, en lecture seule :
ce module n'expose **aucune** fonction d'écriture (pas de create/update/delete)
et refuse tout endpoint hors d'une **whitelist** de lecture. mynewtowt n'écrit
jamais dans Marad.

Configuration (.env, cf. app/config) :
- ``MARAD_API_TOKEN``      : clé d'API (envoyée en header). Sans elle → no-op.
- ``MARAD_BASE_URL``       : défaut ``https://external.marad.ms``.
- ``MARAD_API_KEY_HEADER`` : nom du header d'auth (défaut ``X-Api-Key`` — à
  confirmer auprès de l'éditeur, cf. docs/integrations/marad-crew-readonly.md).

⚠️ Rate limits Marad (confirmés) : ``GET /api/Crewing`` et
``GET /api/CrewingSchedule`` = **1 req/min** ; autres = 15 req/min. À appeler
depuis un cron périodique (pas à la volée).

NOTE : les schémas JSON réels de Marad ne sont pas encore confirmés ; les
fonctions haut niveau renvoient le JSON brut (le mapping de champs est finalisé
par ``services.marad_sync`` une fois un échantillon réel obtenu).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("marad")

_TIMEOUT = 20.0

# Whitelist stricte des endpoints de LECTURE autorisés. Tout appel hors de
# cette liste lève une erreur → garde-fou anti-écriture / anti-régression.
# NB : certains POST Marad sont des *lectures* (passage d'une liste d'IDs en
# body) — ils sont listés ici explicitement. Les endpoints mutatifs
# (POST/PUT/DELETE /api/CrewingSchedule, /api/CrewingDocuments…) sont absents
# et ne doivent JAMAIS être ajoutés ici.
READ_ENDPOINTS: frozenset[str] = frozenset(
    {
        "/api/Crewing",
        "/api/Crewing/CrewMember",
        "/api/CrewingSchedule",
        "/api/CrewingRestHours",
        "/api/CrewingDocuments/GetPassportDetails",  # POST de lecture (batch d'IDs)
        "/api/CrewingDocuments/GetCrewMembersDocuments",  # POST de lecture (batch d'IDs)
        "/api/ranks/getranks",
        "/api/vessels/getVessels",
        "/api/Synchronization/getSyncDetails",
    }
)


def enabled() -> bool:
    """True si une clé d'API Marad est configurée."""
    return bool((settings.marad_api_token or "").strip())


def _assert_allowed(path: str) -> None:
    if path not in READ_ENDPOINTS:
        raise ValueError(
            f"marad: endpoint '{path}' hors whitelist de lecture — refusé "
            f"(intégration strictement read-only)"
        )


def _headers() -> dict[str, str]:
    return {settings.marad_api_key_header: (settings.marad_api_token or "").strip()}


async def _request(
    method: str, path: str, *, params: dict | None = None, json: dict | None = None
) -> Any | None:
    """Appel HTTP read-only borné à la whitelist. None si non configuré / erreur."""
    if not enabled():
        return None
    _assert_allowed(path)
    if method.upper() not in ("GET", "POST"):  # double garde-fou : pas de PUT/DELETE
        raise ValueError(f"marad: méthode {method} interdite (read-only)")
    url = f"{settings.marad_base_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.request(method, url, params=params, json=json, headers=_headers())
            if r.status_code == 429:
                logger.warning("marad %s %s → 429 rate-limited", method, path)
                return None
            if r.status_code >= 400:
                logger.warning("marad %s %s → %d %s", method, path, r.status_code, r.text[:200])
                return None
            return r.json() if r.content else None
    except httpx.HTTPError as e:
        logger.warning("marad %s %s failed: %s", method, path, e)
        return None


async def _get(path: str, *, params: dict | None = None) -> Any | None:
    return await _request("GET", path, params=params)


async def _post_read(path: str, *, json: dict) -> Any | None:
    """POST de **lecture** uniquement (endpoint dans la whitelist)."""
    return await _request("POST", path, json=json)


# ───────────────────────── Lectures haut niveau ──────────────────────────
# Renvoient le JSON brut Marad (shape à confirmer : liste ou {data:[...]}).


async def ping() -> bool:
    """Test de connectivité léger (endpoint 15 req/min). True si l'API répond."""
    return (await _get("/api/vessels/getVessels")) is not None


async def list_crew(modified_since: str | None = None) -> Any | None:
    """GET /api/Crewing (1 req/min). ``modified_since`` : filtre delta (format ❓)."""
    params = {"modifiedDate": modified_since} if modified_since else None
    return await _get("/api/Crewing", params=params)


async def list_ranks() -> Any | None:
    return await _get("/api/ranks/getranks")


async def list_vessels() -> Any | None:
    return await _get("/api/vessels/getVessels")


async def get_passport_details(crew_ids: list[int]) -> Any | None:
    return await _post_read("/api/CrewingDocuments/GetPassportDetails", json={"ids": crew_ids})


async def get_crew_documents(crew_ids: list[int]) -> Any | None:
    return await _post_read("/api/CrewingDocuments/GetCrewMembersDocuments", json={"ids": crew_ids})


def vessel_map() -> dict[str, str]:
    """Mapping ``marad_vessel_id -> vessel_id`` depuis MARAD_VESSEL_MAP."""
    raw = (settings.marad_vessel_map or "").strip()
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out
