"""Sonde MARAD — consulter les retours BRUTS de l'API (lecture seule).

S'appuie sur le client whitelisté ``app.utils.marad`` (mêmes endpoints, même
auth multi-header). N'écrit jamais côté MARAD ni en base. Affiche le JSON
retourné (tronqué par défaut pour les grosses listes ; ``--raw`` = complet).

⚠️ Rate limits MARAD : l'API renvoie 429 dès deux appels rapprochés (même sur
les endpoints « légers »). ``ping()`` interroge **le même** endpoint que
``vessels`` (``/api/vessels/getVessels``) — ne PAS les enchaîner. Bonne pratique :
**un seul endpoint par invocation** (le script appelle ``vessels`` par défaut).
Pour enchaîner plusieurs endpoints, ``--delay`` insère une pause (défaut 65 s).
``crew`` et ``schedules`` = 1 req/min stricte.

Usage (dans le conteneur app) :
  docker compose exec app python -m scripts.marad_probe                  # ping+vessels+ranks
  docker compose exec app python -m scripts.marad_probe vessels
  docker compose exec app python -m scripts.marad_probe ranks
  docker compose exec app python -m scripts.marad_probe crew             # ⚠ 1 req/min
  docker compose exec app python -m scripts.marad_probe schedules        # ⚠ 1 req/min
  docker compose exec app python -m scripts.marad_probe crew --since 2026-01-01T00:00:00
  docker compose exec app python -m scripts.marad_probe passports --ids 12,34
  docker compose exec app python -m scripts.marad_probe documents --ids 12,34
  docker compose exec app python -m scripts.marad_probe vessels --raw    # JSON complet
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from app.config import settings
from app.utils import marad

# Surface les tentatives d'auth multi-header (quel header a authentifié) + erreurs.
logging.basicConfig(level=logging.INFO, format="[marad] %(levelname)s %(message)s")

# NB : ping() appelle /api/vessels/getVessels — c'est donc le MÊME endpoint que
# `vessels`. On NE les enchaîne pas (sinon 429 immédiat). Défaut = un seul appel.
_DEFAULT = ("vessels",)


def _ids(value: str | None) -> list[int]:
    out: list[int] = []
    for part in (value or "").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def _show(name: str, result, *, raw: bool, limit: int) -> None:
    print(f"\n===== {name} =====")
    if result is None:
        print("→ None (non configuré, erreur réseau, ou réponse vide — voir logs ci-dessus).")
        return
    payload = result
    note = ""
    if not raw and isinstance(result, list) and len(result) > limit:
        payload = result[:limit]
        note = f"  (… {len(result)} éléments au total, {limit} affichés — --raw pour tout)"
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + note)


async def _call(endpoint: str, *, since: str | None, ids: list[int]):
    if endpoint == "ping":
        return await marad.ping()
    if endpoint == "vessels":
        return await marad.list_vessels()
    if endpoint == "ranks":
        return await marad.list_ranks()
    if endpoint == "crew":
        return await marad.list_crew(since)
    if endpoint == "schedules":
        return await marad.list_schedules(since)
    if endpoint == "passports":
        return await marad.get_passport_details(ids)
    if endpoint == "documents":
        return await marad.get_crew_documents(ids)
    if endpoint == "sync":
        return await marad.get_sync_details()
    raise SystemExit(f"Endpoint inconnu : {endpoint!r}")


async def run(*, endpoint: str | None, since: str | None, ids: list[int], raw: bool, limit: int, delay: float) -> int:
    if not marad.enabled():
        print("MARAD non configuré : MARAD_API_TOKEN absent du .env → no-op.")
        return 1
    print(f"Base URL : {settings.marad_base_url}")
    targets = [endpoint] if endpoint else list(_DEFAULT)
    for i, ep in enumerate(targets):
        if i > 0 and delay > 0:
            print(f"\n… pause {delay:.0f}s (anti rate-limit MARAD) …")
            await asyncio.sleep(delay)
        if ep in ("passports", "documents") and not ids:
            print(f"\n===== {ep} =====\n→ --ids requis (ex. --ids 12,34).")
            continue
        try:
            result = await _call(ep, since=since, ids=ids)
            _show(ep, result, raw=raw, limit=limit)
        except Exception as e:  # noqa: BLE001 — sonde : on montre l'erreur
            print(f"\n===== {ep} =====\n→ ERREUR : {type(e).__name__}: {e}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Sonde MARAD (lecture seule).")
    p.add_argument(
        "endpoint",
        nargs="?",
        choices=["ping", "vessels", "ranks", "crew", "schedules", "passports", "documents", "sync"],
        help="endpoint à interroger (défaut : ping+vessels+ranks).",
    )
    p.add_argument("--since", default=None, help="filtre modified_since (crew/schedules), ISO 8601.")
    p.add_argument("--ids", default=None, help="IDs crew séparés par des virgules (passports/documents).")
    p.add_argument("--raw", action="store_true", help="affiche le JSON complet (pas de troncature).")
    p.add_argument("--limit", type=int, default=3, help="nb d'éléments affichés pour une liste (défaut 3).")
    p.add_argument("--delay", type=float, default=65.0, help="pause (s) entre appels multiples (défaut 65 — rate-limit MARAD).")
    args = p.parse_args()
    return asyncio.run(
        run(
            endpoint=args.endpoint,
            since=args.since,
            ids=_ids(args.ids),
            raw=args.raw,
            limit=args.limit,
            delay=args.delay,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
