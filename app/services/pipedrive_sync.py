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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial import Client
from app.utils import pipedrive

logger = logging.getLogger(__name__)

_DEFAULT_CLIENT_TYPE = "freight_forwarder"

# Compteurs de deals présents sur l'objet organisation Pipedrive. Une org est
# considérée « cliente » dès qu'elle a au moins un deal (ouvert ou clos).
_DEAL_COUNT_KEYS = (
    "open_deals_count",
    "closed_deals_count",
    "related_open_deals_count",
    "related_closed_deals_count",
    "won_deals_count",
    "lost_deals_count",
)


def _org_has_deal(org: dict) -> bool:
    """True si l'organisation Pipedrive porte au moins un deal."""
    return any((org.get(k) or 0) for k in _DEAL_COUNT_KEYS)


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
            # Filtre métier : on ne remonte que les organisations avec un deal.
            if not _org_has_deal(org):
                skipped += 1
                continue
            address = (org.get("address") or "").strip() or None
            existing = by_pd.get(int(pd_id))
            if existing is None:
                db.add(
                    Client(
                        name=name[:200],
                        client_type=_DEFAULT_CLIENT_TYPE,
                        address=address,
                        pipedrive_org_id=int(pd_id),
                        is_active=True,
                    )
                )
                created += 1
            else:
                # Mise à jour douce : nom + adresse seulement (on préserve le
                # type et les coordonnées saisis manuellement dans l'ERP).
                existing.name = name[:200]
                if address:
                    existing.address = address
                updated += 1
        except (ValueError, TypeError) as e:  # données Pipedrive inattendues
            errors += 1
            logger.warning("pipedrive sync: org ignorée (%s): %s", org.get("id"), e)

    await db.flush()
    result = {
        "configured": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total": len(orgs),
        "errors": errors,
    }
    logger.info("Pipedrive sync clients: %s", result)
    return result
