"""Reprise des commandes héritées (rail A) en réservations (rail B).

Bloc B2.2 — fusion des rails côté données. Chaque commande ``commercial_orders``
encore reliée à un leg et non annulée est convertie en ``Booking``
(``channel="operator"``), puis liée en retour via ``Order.booking_id``. Ce
back-link sert à la fois d'idempotence (une commande déjà reprise est ignorée
au prochain passage) et de dé-doublonnage capacité (cf.
``app.services.capacity._reserved_by_orders`` : une commande avec ``booking_id``
ne réserve plus directement, sa palette est portée par le booking).

Script SÉPARÉ, idempotent, DRY-RUN PAR DÉFAUT (comme ``scripts.purge_legs``).

Usage :
  python -m scripts.migrate_orders_to_bookings            # dry-run : affiche le plan
  python -m scripts.migrate_orders_to_bookings --commit   # exécute + commit

Critères de sélection :
  - ``booking_id IS NULL``  (pas déjà reprise → idempotence)
  - ``leg_id IS NOT NULL``  (un booking exige un leg)
  - ``status != "cancelled"`` (on ne migre pas les commandes annulées)

Une commande dont le client commercial n'a aucun ``ClientAccount`` rattaché
(``ClientAccount.commercial_client_id``) est IGNORÉE et listée dans le rapport
« bloquées (pas de compte client) » — le script ne plante pas.

Mapping des statuts commande → réservation :
  draft → submitted, confirmed → confirmed, loaded → loaded,
  delivered → delivered, (tout autre) → submitted.

Note : les commandes ne portent pas de poids → ``total_weight_kg = 0``.

Tout est exécuté dans UNE transaction (le script gère son commit, comme
purge_legs) : rollback automatique en cas d'erreur.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from app.database import SessionLocal
from app.models.booking import Booking
from app.models.client_account import ClientAccount
from app.models.commercial import Order
from app.services.booking import generate_reference

# Commande (ORDER_STATUSES) → réservation (statut booking). Tout statut non
# listé retombe sur "submitted".
_STATUS_MAP: dict[str, str] = {
    "draft": "submitted",
    "confirmed": "confirmed",
    "loaded": "loaded",
    "delivered": "delivered",
}

# Statut booking → champ horodatage à renseigner (présents sur le modèle).
_STATUS_TIMESTAMP: dict[str, str] = {
    "submitted": "submitted_at",
    "confirmed": "confirmed_at",
    "loaded": "loaded_at",
    "delivered": "delivered_at",
}


def _map_status(order_status: str) -> str:
    return _STATUS_MAP.get(order_status, "submitted")


async def run(*, execute: bool) -> int:
    async with SessionLocal() as db:
        # Commandes reprenables : pas déjà reprises, rattachées à un leg, non annulées.
        orders = (
            (
                await db.execute(
                    select(Order)
                    .where(
                        Order.booking_id.is_(None),
                        Order.leg_id.is_not(None),
                        Order.status != "cancelled",
                    )
                    .order_by(Order.id)
                )
            )
            .scalars()
            .all()
        )

        migrated: list[tuple[str, str, int, str]] = []  # (order_ref, booking_status, palettes, leg)
        blocked: list[tuple[str, int]] = []  # (order_ref, client_id) — pas de compte client

        for order in orders:
            account = (
                (
                    await db.execute(
                        select(ClientAccount).where(
                            ClientAccount.commercial_client_id == order.client_id
                        )
                    )
                )
                .scalars()
                .first()
            )

            if account is None:
                blocked.append((order.reference, order.client_id))
                continue

            booking_status = _map_status(order.status)
            booking = Booking(
                reference=generate_reference(),
                client_account_id=account.id,
                leg_id=order.leg_id,
                channel="operator",
                status=booking_status,
                total_palettes=order.booked_palettes or 0,
                total_weight_kg=0,  # les commandes ne portent pas de poids
                estimated_price_eur=order.total_eur,
                shipper_reference=order.reference,
                notes=f"Repris de la commande {order.reference}",
            )
            ts_field = _STATUS_TIMESTAMP.get(booking_status)
            if ts_field is not None:
                setattr(booking, ts_field, datetime.now(UTC))

            db.add(booking)
            await db.flush()  # matérialise booking.id pour le back-link
            order.booking_id = booking.id

            migrated.append(
                (
                    order.reference,
                    booking.reference,
                    booking.total_palettes,
                    booking_status,
                )
            )

        # --- Rapport ---
        n_candidates = len(orders)
        print(f"Commandes reprenables (booking_id NULL, leg, non annulée) : {n_candidates}")
        print()

        if migrated:
            print(f"À REPRENDRE ({len(migrated)}) :")
            print(f"  {'Commande':<16} {'→ Booking':<18} {'Palettes':>8}  Statut")
            for order_ref, booking_ref, palettes, status in migrated:
                print(f"  {order_ref:<16} {booking_ref:<18} {palettes:>8}  {status}")
            print()
        else:
            print("Aucune commande à reprendre.")
            print()

        if blocked:
            print(f"BLOQUÉES — pas de compte client ({len(blocked)}) :")
            for order_ref, client_id in blocked:
                print(f"  {order_ref:<16} (commercial_client_id={client_id})")
            print()

        print("Résumé :")
        print(f"  migrées          : {len(migrated)}")
        print(f"  bloquées (compte): {len(blocked)}")
        print("  ignorées (déjà liée / sans leg / annulée) : exclues en amont")

        if not execute:
            print("\n[DRY-RUN] Rien écrit. Relancer avec --commit pour exécuter.")
            await db.rollback()
            return 0

        await db.commit()
        print(f"\n✓ Reprise terminée : {len(migrated)} commande(s) → réservation(s).")
        return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Reprise des commandes héritées (rail A) en réservations (rail B)"
    )
    p.add_argument(
        "--commit",
        action="store_true",
        help="Exécute réellement et committe (sinon dry-run)",
    )
    args = p.parse_args()
    return asyncio.run(run(execute=args.commit))


if __name__ == "__main__":
    sys.exit(main())
