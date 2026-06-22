"""Synchronisation Pipedrive → clients commerciaux.

Remonte les **organisations** Pipedrive dans la table ``commercial_clients``
(une organisation = un client). Rapprochement par ``pipedrive_org_id`` :
- org déjà liée → mise à jour du nom / adresse (les champs saisis à la main
  comme le contact ne sont pas écrasés) ;
- org inconnue → création d'un client (type par défaut ``freight_forwarder``).

Déclenché par le bouton « Synchroniser Pipedrive » sur /commercial/clients.
No-op propre si ``PIPEDRIVE_API_TOKEN`` n'est pas configuré.
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial import Client
from app.utils import pipedrive

logger = logging.getLogger(__name__)

# Compteurs de deals présents sur l'objet organisation Pipedrive (fallback si
# la liste des deals est indisponible). Une org est « cliente » dès qu'elle a
# au moins un deal (ouvert ou clos), sur n'importe quel pipeline.
_DEAL_COUNT_KEYS = (
    "open_deals_count",
    "closed_deals_count",
    "related_open_deals_count",
    "related_closed_deals_count",
    "won_deals_count",
    "lost_deals_count",
)

# Classification du type de client à partir de l'« activité » de l'organisation
# Pipedrive : une activité commençant par « IFF » => transitaire (freight
# forwarder), sinon chargeur (shipper / client direct).
_FF_ACTIVITY_PREFIX = "IFF"
# Clé du champ personnalisé Pipedrive portant l'« activité » de l'org. Si non
# renseignée, on repère par balayage une valeur de champ commençant par IFF.
_ACTIVITY_FIELD_KEY = (os.getenv("PIPEDRIVE_ORG_ACTIVITY_KEY") or "").strip() or None


def _org_has_deal(org: dict) -> bool:
    """True si l'organisation Pipedrive porte au moins un deal (via compteurs)."""
    return any((org.get(k) or 0) for k in _DEAL_COUNT_KEYS)


def _deal_org_id(deal: dict) -> int | None:
    """Extrait l'``org_id`` d'un deal Pipedrive (int ou objet ``{value}``)."""
    raw = deal.get("org_id")
    if isinstance(raw, dict):
        raw = raw.get("value")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _org_activity(org: dict) -> str:
    """Récupère l'« activité » de l'organisation Pipedrive.

    Priorité au champ personnalisé configuré (``PIPEDRIVE_ORG_ACTIVITY_KEY``) ;
    à défaut, on repère une valeur de champ commençant par « IFF » (le seul
    motif qui nous intéresse pour distinguer un transitaire).
    """
    if _ACTIVITY_FIELD_KEY:
        return str(org.get(_ACTIVITY_FIELD_KEY) or "").strip()
    for value in org.values():
        if isinstance(value, str) and value.strip().upper().startswith(_FF_ACTIVITY_PREFIX):
            return value.strip()
    return ""


def _client_type_for(org: dict) -> str:
    """``freight_forwarder`` si l'activité commence par IFF, sinon ``shipper``."""
    if _org_activity(org).upper().startswith(_FF_ACTIVITY_PREFIX):
        return "freight_forwarder"
    return "shipper"


def is_configured() -> bool:
    return pipedrive.enabled()


async def sync_clients(db: AsyncSession) -> dict:
    """Upsert des organisations Pipedrive **ayant un deal** dans ``commercial_clients``.

    Seules les organisations avec au moins un deal (ouvert ou clos) sont
    remontées — les autres sont ignorées (``skipped``) pour ne pas polluer la
    liste clients.

    Renvoie ``{configured, created, updated, skipped, total, errors}``.
    """
    if not pipedrive.enabled():
        return {
            "configured": False,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "total": 0,
            "errors": 0,
        }

    orgs = await pipedrive.list_organizations()
    by_pd = {
        c.pipedrive_org_id: c
        for c in (await db.execute(select(Client))).scalars().all()
        if c.pipedrive_org_id is not None
    }

    # Source de vérité « a un deal sur n'importe quel pipeline » : on liste TOUS
    # les deals (tous pipelines, ouverts/gagnés/perdus) et on en déduit les org
    # qui en portent au moins un. C'est plus fiable que les compteurs parfois
    # absents de la liste des organisations (cause des clients manquants).
    deals = await pipedrive.list_deals()
    org_ids_with_deal: set[int] = {oid for d in deals if (oid := _deal_org_id(d)) is not None}

    created = 0
    updated = 0
    skipped = 0
    errors = 0
    for org in orgs:
        try:
            pd_id = org.get("id")
            name = (org.get("name") or "").strip()
            if not pd_id or not name:
                continue
            # Règle métier : on remonte une organisation dès qu'elle a un deal
            # sur n'importe quel pipeline (liste des deals OU compteurs de
            # secours si la liste est indisponible).
            has_deal = int(pd_id) in org_ids_with_deal or _org_has_deal(org)
            if not has_deal:
                skipped += 1
                continue
            address = (org.get("address") or "").strip() or None
            client_type = _client_type_for(org)
            existing = by_pd.get(int(pd_id))
            if existing is None:
                db.add(
                    Client(
                        name=name[:200],
                        client_type=client_type,
                        address=address,
                        pipedrive_org_id=int(pd_id),
                        is_active=True,
                    )
                )
                created += 1
            else:
                # Mise à jour douce : nom + adresse + type (dérivé de l'activité
                # Pipedrive). On préserve les coordonnées saisies manuellement.
                existing.name = name[:200]
                existing.client_type = client_type
                if address:
                    existing.address = address
                updated += 1
        except (ValueError, TypeError) as e:  # données Pipedrive inattendues
            errors += 1
            logger.warning("pipedrive sync: org ignorée (%s): %s", org.get("id"), e)

    await db.flush()

    # Rapprochement auto des comptes plateforme non liés (par e-mail) avec les
    # clients fraîchement remontés.
    linked = 0
    try:
        from app.services.client_linking import link_unlinked_accounts

        linked = await link_unlinked_accounts(db)
    except Exception:
        logger.warning("post-sync account linking failed", exc_info=True)

    result = {
        "configured": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "linked": linked,
        "total": len(orgs),
        "errors": errors,
    }
    logger.info("Pipedrive sync clients: %s", result)
    return result
