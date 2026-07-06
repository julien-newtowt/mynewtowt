"""Marad (MaraSoft « Generic API ») — client HTTP **LECTURE SEULE**.

Intégration des données crew de Marad vers mynewtowt, en lecture seule :
ce module n'expose **aucune** fonction d'écriture (pas de create/update/delete)
et refuse tout endpoint hors d'une **whitelist** de lecture. mynewtowt n'écrit
jamais dans Marad.

Configuration (.env, cf. app/config) :
- ``MARAD_API_TOKEN``      : clé d'API (envoyée en header). Sans elle → no-op.
- ``MARAD_BASE_URL``       : défaut ``https://external.marad.ms``.
- ``MARAD_API_KEY_HEADER`` : nom du header d'auth (défaut ``X-Api-Key``). Ce nom
  est **épinglé et essayé en premier** (un seul appel, pas de cascade). Laissé
  au défaut, le client essaie les headers usuels (``X-Api-Key``, ``ApiKey``,
  ``ApiToken``, ``Authorization``) puis, en repli, la query string, et mémorise
  le schéma qui authentifie. Fixez cette variable pour forcer un header précis.

⚠️ Rate limits Marad (confirmés) : ``GET /api/Crewing`` et
``GET /api/CrewingSchedule`` = **1 req/min** ; autres = 15 req/min. À appeler
depuis un cron périodique (pas à la volée).

NOTE : le schéma de ``GET /api/Crewing`` est **confirmé** (échantillon éditeur
2026-06-17, mappé dans ``services.marad_sync``). Les autres schémas (documents,
schedules) restent à confirmer ; les fonctions haut niveau renvoient le JSON
brut.
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


# Endpoints à quota SERRÉ (1 req/min côté Marad). Sur ceux-là, on ne sonde
# JAMAIS plusieurs schémas d'auth : une cascade de 401→429 y brûlerait tout le
# quota de la minute et garantirait l'échec. Si l'auth n'est pas encore amorcée
# (``prime_auth`` via getVessels, 15 req/min), on tente un SEUL schéma (le
# meilleur candidat) plutôt que la liste complète.
RATE_LIMITED_ENDPOINTS: frozenset[str] = frozenset({"/api/Crewing", "/api/CrewingSchedule"})


def enabled() -> bool:
    """True si une clé d'API Marad est configurée."""
    return bool((settings.marad_api_token or "").strip())


def _assert_allowed(path: str) -> None:
    if path not in READ_ENDPOINTS:
        raise ValueError(
            f"marad: endpoint '{path}' hors whitelist de lecture — refusé "
            f"(intégration strictement read-only)"
        )


# Schéma d'auth non confirmé par l'éditeur (Swagger en 403). On essaie plusieurs
# stratégies (noms de header au token brut + « Authorization: Bearer ») et on
# **mémorise** celle qui authentifie, pour ne pas gaspiller le quota
# (GET /api/Crewing = 1 req/min). MARAD_API_KEY_HEADER force un nom de header.
_DEFAULT_HEADER = "X-Api-Key"
_working_strategy: str | None = None

# Type d'une stratégie d'auth : (label, headers, query_params).
_Strategy = tuple[str, dict[str, str], dict[str, str]]

# Dernier statut observé par endpoint (200, 401, 429, "network"…) — purement
# diagnostic : permet à sync_all d'expliquer un « 0 reçu » (ex. 429 = quota
# consommé par un appel récent ou un refresh Power BI partageant la clé) SANS
# re-sonder l'API, donc sans dépenser de quota supplémentaire.
_last_status: dict[str, int | str | None] = {}


def last_status(path: str) -> int | str | None:
    """Dernier statut vu sur ``path`` lors d'un appel réel, ou None."""
    return _last_status.get(path)


def _auth_strategies() -> list[_Strategy]:
    """Schémas d'auth à essayer ``(label, headers, query_params)``.

    Marasoft (API ASP.NET Core, lib mihirdilip/aspnetcore-authentication-apikey)
    accepte la clé **en header OU en query string**, sous un même nom — historiquement
    ``apiKey`` en query string (cf. release notes). On essaie donc le query param
    ``apiKey`` ET plusieurs noms de header. ``MARAD_API_KEY_HEADER`` force un nom
    précis (utilisé en header ET en query). Le schéma gagnant est mémorisé.
    """
    token = (settings.marad_api_token or "").strip()
    explicit = (settings.marad_api_key_header or "").strip()
    candidates: list[_Strategy] = []
    # Header explicitement configuré → prioritaire, **même s'il vaut le défaut**
    # (X-Api-Key). Auparavant le défaut n'était jamais épinglé → si le contrat
    # réel est « X-Api-Key: <token> » en header, l'auth ne pouvait pas aboutir
    # et l'opérateur ne pouvait pas la forcer.
    if explicit:
        candidates.append((f"header:{explicit}", {explicit: token}, {}))
    candidates += [
        # Header d'ABORD : Marasoft a retiré l'auth par query string en v5.5.24
        # (« API Key must be in request headers »). On essaie donc les headers
        # avant les query params, pour que la stratégie gagnante soit l'essai #1
        # (évite la cascade de 429 sur les endpoints à 1 req/min).
        ("header:X-Api-Key", {"X-Api-Key": token}, {}),
        ("header:ApiKey", {"ApiKey": token}, {}),
        ("header:X-API-KEY", {"X-API-KEY": token}, {}),
        ("header:ApiToken", {"ApiToken": token}, {}),
        ("Authorization:Bearer", {"Authorization": f"Bearer {token}"}, {}),
        ("Authorization:raw", {"Authorization": token}, {}),
        # Query string en dernier (rétro-compat < v5.5.24 ; sensible à la casse).
        ("query:apikey", {}, {"apikey": token}),
        ("query:apiKey", {}, {"apiKey": token}),
    ]
    if _working_strategy:  # schéma mémorisé en tête
        candidates.sort(key=lambda c: c[0] != _working_strategy)
    seen: set[str] = set()
    return [c for c in candidates if not (c[0] in seen or seen.add(c[0]))]


def _strategies_for_request() -> list[_Strategy]:
    """Stratégies à essayer pour un appel réel.

    Si un schéma est **épinglé** (``MARAD_API_KEY_HEADER`` configuré) ou déjà
    **mémorisé** (``_working_strategy``), on n'essaie que **celui-là** : un seul
    appel HTTP, donc pas de cascade de 401→429 sur les endpoints à 1 req/min
    (c'était la cause n°1 des « auth refusée » / « synchro sans données »
    intermittentes). Sinon, on renvoie la liste complète à sonder (header-first).
    """
    strategies = _auth_strategies()
    if _working_strategy:
        pinned = [s for s in strategies if s[0] == _working_strategy]
        if pinned:
            return pinned
    explicit = (settings.marad_api_key_header or "").strip()
    if explicit:
        label = f"header:{explicit}"
        pinned = [s for s in strategies if s[0] == label]
        if pinned:
            return pinned
    return strategies


async def prime_auth() -> str | None:
    """Découvre & mémorise le schéma d'auth via ``getVessels`` (15 req/min).

    À appeler AVANT les endpoints à quota serré (``/api/Crewing`` = 1 req/min) :
    on évite ainsi de gâcher leur quota en essayant plusieurs schémas dessus.

    No-op quand un header est **épinglé** (``MARAD_API_KEY_HEADER``) : il n'y a
    rien à découvrir (un seul schéma sera essayé partout) et l'appel getVessels
    économisé compte — les quotas Marad réels se déclenchent parfois dès deux
    appels rapprochés, surtout si la clé est partagée avec d'autres
    consommateurs (refresh Power BI…).
    """
    if not enabled() or _working_strategy:
        return _working_strategy
    if (settings.marad_api_key_header or "").strip():
        return None  # schéma épinglé : pas d'amorçage, on économise un appel
    await _get("/api/vessels/getVessels")  # met à jour _working_strategy si succès
    return _working_strategy


async def _request(
    method: str, path: str, *, params: dict | None = None, json: dict | None = None
) -> Any | None:
    """Appel HTTP read-only borné à la whitelist. None si non configuré / erreur.

    Essaie les schémas d'auth jusqu'à ce que l'un authentifie (pas de 401/403) ;
    le schéma gagnant est mémorisé pour les appels suivants.
    """
    global _working_strategy
    if not enabled():
        return None
    _assert_allowed(path)
    if method.upper() not in ("GET", "POST"):  # double garde-fou : pas de PUT/DELETE
        raise ValueError(f"marad: méthode {method} interdite (read-only)")
    url = f"{settings.marad_base_url.rstrip('/')}{path}"
    strategies = _strategies_for_request()
    # Sur un endpoint à 1 req/min non encore authentifié, on ne tente qu'UN
    # schéma : sonder les 8 y provoquerait 7×429 et épuiserait le quota. Le
    # schéma retenu par ``prime_auth`` (getVessels, 15 req/min) est réutilisé
    # dès qu'il est mémorisé — la cascade complète reste réservée à getVessels.
    if path in RATE_LIMITED_ENDPOINTS and len(strategies) > 1:
        strategies = strategies[:1]
        logger.info(
            "marad %s : auth non amorcée, essai d'un seul schéma (%s) pour "
            "préserver le quota 1 req/min",
            path,
            strategies[0][0],
        )
    last_auth_status: int | None = None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for label, headers, auth_params in strategies:
                merged_params = {**(params or {}), **auth_params}
                r = await client.request(
                    method, url, params=merged_params or None, json=json, headers=headers
                )
                _last_status[path] = r.status_code
                if r.status_code in (401, 403):
                    last_auth_status = r.status_code
                    if label == _working_strategy:
                        _working_strategy = None  # le schéma mémorisé ne marche plus
                    continue
                if r.status_code == 429:
                    logger.warning("marad %s %s → 429 rate-limited", method, path)
                    return None
                if r.status_code >= 400:
                    logger.warning("marad %s %s → %d %s", method, path, r.status_code, r.text[:200])
                    return None
                if _working_strategy != label:
                    logger.info("marad: schéma d'auth retenu = '%s'", label)
                    _working_strategy = label
                return r.json() if r.content else None
    except httpx.HTTPError as e:
        _last_status[path] = "network"
        logger.warning("marad %s %s failed: %s", method, path, e)
        return None
    logger.warning(
        "marad %s %s → auth refusée (%s) sur tous les schémas", method, path, last_auth_status
    )
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


async def list_schedules(modified_since: str | None = None) -> Any | None:
    """GET /api/CrewingSchedule (1 req/min). Plannings d'embarquement.

    Chez Marad, un « voyage » correspond à notre ``leg``. ``modified_since`` :
    filtre delta éventuel (format ❓ à confirmer).
    """
    params = {"modifiedDate": modified_since} if modified_since else None
    return await _get("/api/CrewingSchedule", params=params)


async def list_ranks() -> Any | None:
    return await _get("/api/ranks/getranks")


async def list_vessels() -> Any | None:
    """GET /api/vessels/getVessels (15 req/min). Schéma confirmé : ``[{number, name}]``."""
    return await _get("/api/vessels/getVessels")


async def get_passport_details(crew_ids: list[int]) -> Any | None:
    return await _post_read("/api/CrewingDocuments/GetPassportDetails", json={"ids": crew_ids})


async def get_crew_documents(crew_ids: list[int]) -> Any | None:
    return await _post_read("/api/CrewingDocuments/GetCrewMembersDocuments", json={"ids": crew_ids})


async def get_sync_details() -> Any | None:
    """GET /api/Synchronization/getSyncDetails — métadonnées de compte/synchro
    (utile pour diagnostiquer un compte/tenant vide ou mal provisionné)."""
    return await _get("/api/Synchronization/getSyncDetails")


async def diagnose() -> dict:
    """Sonde détaillée pour expliquer un « rien ne remonte ».

    Teste ``GET /api/vessels/getVessels`` (15 req/min) avec chaque schéma d'auth
    et capture, pour chacun, le code HTTP **ou** l'erreur réseau. Permet de
    distinguer : hôte injoignable (DNS/firewall/URL), authentification refusée
    (401/403 partout), chemin inexistant (404), ou succès.

    Renvoie ``{configured, reachable, authenticated, classification, base_url,
    working_strategy, attempts: [{strategy, status|error}], vessels_count}``.
    """
    if not enabled():
        return {
            "configured": False,
            "reachable": False,
            "authenticated": False,
            "classification": "not_configured",
            "base_url": settings.marad_base_url,
        }

    url = f"{settings.marad_base_url.rstrip('/')}/api/vessels/getVessels"
    attempts: list[dict] = []
    any_response = False  # au moins une réponse HTTP reçue (hôte joignable)
    authed = False
    vessels_count: int | None = None
    saw_401_403 = False
    saw_404 = False
    saw_429 = False
    auth_error_body: str | None = None  # corps du 1er 401/403 (message serveur)
    www_authenticate: str | None = None
    rate_limit_body: str | None = None  # corps du 429 (quota réel vs auth masquée)
    retry_after: str | None = None  # en-tête Retry-After du 429 (secondes/date)
    # Si un schéma d'auth est déjà connu (mémorisé ou épinglé), on ne sonde
    # QU'UN appel getVessels — indispensable ici : diagnose() est appelé juste
    # après prime_auth()+crew+schedules, et re-sonder les 8 schémas ferait
    # sauter le quota de getVessels (→ faux « rate_limited »). La cascade
    # complète n'a lieu que si l'auth n'a jamais été établie.
    strategies = _strategies_for_request()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for label, headers, auth_params in strategies:
                try:
                    r = await client.get(url, headers=headers, params=auth_params or None)
                    any_response = True
                    attempts.append({"strategy": label, "status": r.status_code})
                    if r.status_code in (401, 403):
                        saw_401_403 = True
                        # Capture le message d'erreur du serveur (1re fois) — il
                        # indique souvent « clé invalide » vs « clé manquante ».
                        if auth_error_body is None and r.content:
                            auth_error_body = r.text[:300]
                        if www_authenticate is None:
                            www_authenticate = r.headers.get("www-authenticate")
                        continue
                    if r.status_code == 404:
                        saw_404 = True
                        continue
                    if r.status_code == 429:
                        # Endpoint saturé : inutile de continuer à sonder (les
                        # essais suivants tomberaient dans la même fenêtre). On
                        # CAPTURE la réponse serveur : un vrai quota renvoie un
                        # Retry-After et/ou un corps « rate limit » ; une clé
                        # invalide renvoyée en 429 (anti-bruteforce) se trahit
                        # par un corps « invalid/unauthorized ».
                        saw_429 = True
                        retry_after = r.headers.get("retry-after")
                        if r.content:
                            rate_limit_body = r.text[:300]
                        break
                    if r.status_code < 400:
                        authed = True
                        data = r.json() if r.content else None
                        if isinstance(data, list):
                            vessels_count = len(data)
                        break
                except httpx.HTTPError as e:
                    attempts.append({"strategy": label, "error": type(e).__name__})
    except Exception as e:  # pragma: no cover - garde-fou
        attempts.append({"strategy": "*", "error": type(e).__name__})

    if authed:
        classification = "ok"
    elif not any_response:
        classification = "unreachable"  # aucune réponse HTTP → réseau/DNS/URL
    elif saw_401_403:
        classification = "auth_refused"  # hôte répond mais refuse l'auth
    elif saw_404:
        classification = "wrong_path"  # hôte répond mais endpoint inconnu
    elif saw_429:
        classification = "rate_limited"  # quota 1 req/min épuisé — réessayer plus tard
    else:
        classification = "http_error"

    # Confirme qu'un token est bien chargé (sans l'exposer en clair).
    token = (settings.marad_api_token or "").strip()
    token_preview = (token[:6] + "…") if token else None
    return {
        "configured": True,
        "reachable": any_response,
        "authenticated": authed,
        "classification": classification,
        "base_url": settings.marad_base_url,
        "working_strategy": _working_strategy,
        "attempts": attempts,
        "tried_strategies": [a.get("strategy") for a in attempts],
        "vessels_count": vessels_count,
        "auth_error_body": auth_error_body,
        "www_authenticate": www_authenticate,
        "rate_limit_body": rate_limit_body,
        "retry_after": retry_after,
        "token_set": bool(token),
        "token_preview": token_preview,
        "token_len": len(token),
    }


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
