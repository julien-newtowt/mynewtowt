"""Synchronisation Marad → crew (LECTURE SEULE) — squelette.

État : **plomberie + découverte de schéma**. Le mapping de champs définitif
(noms exacts des champs JSON Marad) sera implémenté une fois un échantillon de
réponse réel obtenu (cf. docs/integrations/marad-crew-readonly.md, R3).

Pour l'instant, ``sync_crew`` :
- est un no-op propre si Marad n'est pas configuré (pas de clé) ;
- sinon lit la liste crew (read-only) et renvoie un **résumé de découverte**
  (nb d'enregistrements + noms de champs du 1er enregistrement), **sans rien
  écrire** dans les tables crew — pour éviter d'injecter des données mal
  mappées. Le upsert idempotent (clé ``marad_id``, préservation des champs ERP)
  sera branché ici dès que le schéma est confirmé.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.utils import marad

logger = logging.getLogger("marad")


def is_configured() -> bool:
    return marad.enabled()


def _records(payload: Any) -> list[dict]:
    """Normalise une réponse Marad en liste de dicts (shape à confirmer)."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "value", "records"):
            v = payload.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        # dict simple → 1 enregistrement
        return [payload]
    return []


async def sync_crew(db: AsyncSession) -> dict:
    """Découverte read-only du crew Marad (mapping non encore branché).

    Renvoie ``{configured, fetched, sample_fields, mapped, note}``.
    NB : ``db`` est pris en argument pour la future étape d'upsert ; aucune
    écriture n'est faite tant que le mapping n'est pas confirmé.
    """
    if not marad.enabled():
        return {
            "configured": False,
            "fetched": 0,
            "sample_fields": [],
            "mapped": 0,
            "note": "MARAD_API_TOKEN non configuré — intégration inactive.",
        }

    payload = await marad.list_crew()
    records = _records(payload)
    sample_fields = sorted(records[0].keys()) if records else []
    logger.info(
        "Marad sync (discovery): %d enregistrement(s), champs du 1er = %s",
        len(records),
        sample_fields,
    )
    return {
        "configured": True,
        "fetched": len(records),
        "sample_fields": sample_fields,
        "mapped": 0,
        "note": (
            "Découverte read-only : mapping de champs en attente d'un schéma "
            "Marad confirmé (cf. docs/integrations/marad-crew-readonly.md §3)."
        ),
    }
