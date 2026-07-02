"""Purge des clients commerciaux — reprise propre via la synchro Pipedrive filtrée.

Contexte : avant la mise en place du filtre « organisation avec au moins un
deal », la synchro avait importé toute la base Pipedrive. Ce script vide cette
ancienne liste pour que la prochaine synchro ne conserve que les organisations
réellement clientes.

OPÉRATION DESTRUCTIVE — dry-run par défaut.

Périmètre :
  - par défaut, ne vise que les clients issus de Pipedrive
    (``pipedrive_org_id`` non NULL) ;
  - ``--all`` vise TOUS les clients commerciaux.

Sécurité :
  - un client ayant des COMMANDES (``Order``) est CONSERVÉ (donnée
    commerciale réelle) — sauf ``--force`` ;
  - les grilles tarifaires et offres des clients supprimés sont supprimées
    (configuration régénérable ; lignes/options de grille en CASCADE DB) ;
  - les comptes plateforme liés sont DÉLIÉS (``commercial_client_id`` → NULL),
    jamais supprimés.

Tout est exécuté dans UNE transaction : rollback automatique si erreur.

Usage :
  python -m scripts.purge_clients                      # dry-run (compte)
  python -m scripts.purge_clients --yes                # purge les clients Pipedrive
  python -m scripts.purge_clients --all --yes          # purge TOUS les clients (hors ceux à commandes)
  python -m scripts.purge_clients --all --force --yes  # inclut les clients à commandes (+ leurs commandes)
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import delete, func, select, update

from app.database import SessionLocal
from app.models.client_account import ClientAccount
from app.models.commercial import Client, Order, OrderAssignment, RateGrid, RateOffer


async def run(*, execute: bool, scope_all: bool, force: bool) -> int:
    async with SessionLocal() as db:
        target = select(Client.id)
        if not scope_all:
            target = target.where(Client.pipedrive_org_id.is_not(None))

        n_target = await db.scalar(select(func.count()).select_from(target.subquery()))
        with_orders = select(Order.client_id).where(Order.client_id.in_(target)).distinct()
        n_with_orders = await db.scalar(select(func.count()).select_from(with_orders.subquery()))
        n_accounts = await db.scalar(
            select(func.count()).select_from(
                select(ClientAccount.id)
                .where(ClientAccount.commercial_client_id.in_(target))
                .subquery()
            )
        )
        n_grids = await db.scalar(
            select(func.count()).select_from(
                select(RateGrid.id).where(RateGrid.client_id.in_(target)).subquery()
            )
        )
        n_offers = await db.scalar(
            select(func.count()).select_from(
                select(RateOffer.id).where(RateOffer.client_id.in_(target)).subquery()
            )
        )

        scope = "TOUS les clients" if scope_all else "clients Pipedrive (pipedrive_org_id non NULL)"
        print(f"Périmètre          : {scope}")
        print(f"Clients ciblés     : {n_target}")
        print(
            f"  · avec commandes : {n_with_orders}  ({'supprimés (--force)' if force else 'CONSERVÉS'})"
        )
        print(f"Grilles liées      : {n_grids}  (supprimées)")
        print(f"Offres liées       : {n_offers}  (supprimées)")
        print(f"Comptes plateforme : {n_accounts}  (déliés, non supprimés)")

        if not execute:
            print("\n[DRY-RUN] Rien supprimé. Relancer avec --yes pour exécuter.")
            return 0
        if n_target == 0:
            print("Aucun client à purger.")
            return 0

        # Clients effectivement supprimés.
        del_ids = select(Client.id).where(Client.id.in_(target))
        if not force:
            del_ids = del_ids.where(Client.id.not_in(select(Order.client_id)))
        else:
            # --force : supprimer aussi les commandes des clients visés.
            orders_subq = select(Order.id).where(Order.client_id.in_(target))
            await db.execute(
                delete(OrderAssignment).where(OrderAssignment.order_id.in_(orders_subq))
            )
            await db.execute(delete(Order).where(Order.client_id.in_(target)))

        # Délier les comptes plateforme (FK ondelete SET NULL ; explicite par sûreté).
        await db.execute(
            update(ClientAccount)
            .where(ClientAccount.commercial_client_id.in_(del_ids))
            .values(commercial_client_id=None)
        )
        # Supprimer offres puis grilles (lignes/options en CASCADE DB).
        await db.execute(delete(RateOffer).where(RateOffer.client_id.in_(del_ids)))
        await db.execute(delete(RateGrid).where(RateGrid.client_id.in_(del_ids)))
        # Supprimer les clients.
        result = await db.execute(delete(Client).where(Client.id.in_(del_ids)))

        await db.commit()
        deleted = result.rowcount if result.rowcount is not None else "?"
        print(f"\n✓ Purge terminée : {deleted} client(s) supprimé(s).")
        if not force and n_with_orders:
            print(
                f"  ({n_with_orders} client(s) à commandes conservé(s) — relancer avec --force pour les inclure.)"
            )
        print("Relancez ensuite « Synchroniser Pipedrive » pour repeupler depuis le jeu filtré.")
        return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Purge des clients commerciaux (reprise via synchro).")
    p.add_argument("--yes", action="store_true", help="Exécute réellement (sinon dry-run).")
    p.add_argument(
        "--all", action="store_true", help="Vise tous les clients (pas seulement Pipedrive)."
    )
    p.add_argument(
        "--force", action="store_true", help="Inclut les clients à commandes (+ leurs commandes)."
    )
    args = p.parse_args()
    return asyncio.run(run(execute=args.yes, scope_all=args.all, force=args.force))


if __name__ == "__main__":
    sys.exit(main())
