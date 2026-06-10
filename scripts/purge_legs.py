"""Purge des legs et de toutes leurs données dépendantes.

Table rase du planning avant reprise (nouveau format de leg_code +
contrôles d'intégrité). OPÉRATION DESTRUCTIVE — dry-run par défaut.

Usage :
  python -m scripts.purge_legs                 # dry-run : compte seulement
  python -m scripts.purge_legs --yes           # exécute (TOUS les legs)
  python -m scripts.purge_legs --vessel ANE --yes   # seulement le navire ANE
  python -m scripts.purge_legs --before 2026-01-01 --yes  # legs ETD < date

Ordre FK-safe :
  1. Délier (set NULL) les liens optionnels booking → invoices /
     certificats / claims.
  2. Supprimer les bookings (booking_items en CASCADE DB).
  3. Supprimer les données opérationnelles propres au leg (escale,
     dockers, SOF, ETA shifts, messages, docs cargo, noon reports,
     quarts, checklists, visiteurs, MRV, finance, KPI, affectations
     équipage, assignations commande, plans d'arrimage).
  4. Délier (set NULL) les FK nullables qui doivent survivre (claims,
     tickets, mouvements caisse, tickets équipage, certificats Anemos,
     commandes, offres tarifaires).
  5. Supprimer les legs.

Tout est exécuté dans UNE transaction : rollback automatique si erreur.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update

from app.database import SessionLocal
from app.models.anemos_certificate import AnemosCertificate
from app.models.booking import Booking
from app.models.claim import Claim
from app.models.client_invoice import ClientInvoice
from app.models.commercial import Order, OrderAssignment, RateOffer
from app.models.crew import CrewAssignment
from app.models.crew_ticket import CrewTicket
from app.models.escale import DockerShift, EscaleOperation
from app.models.finance import LegFinance, LegKPI
from app.models.leg import Leg
from app.models.mrv import MRVEvent
from app.models.noon_report import NoonReport
from app.models.onboard_cashbox import CashboxMovement
from app.models.sof_event import CargoDocument, EtaShift, OnboardMessage, SofEvent
from app.models.stowage import StowagePlan
from app.models.ticket import Ticket
from app.models.vessel import Vessel
from app.models.watch_log import OnboardChecklist, VisitorLog, WatchLog


# Modèles dont les lignes liées au leg sont SUPPRIMÉES.
LEG_OWNED_DELETE = [
    EscaleOperation, DockerShift,
    SofEvent, EtaShift, OnboardMessage, CargoDocument,
    NoonReport, WatchLog, OnboardChecklist, VisitorLog,
    MRVEvent, LegFinance, LegKPI,
    CrewAssignment, OrderAssignment, StowagePlan,
]

# Modèles dont leg_id est mis à NULL (la ligne survit).
LEG_NULLABLE = [Claim, Ticket, CashboxMovement, CrewTicket, AnemosCertificate, Order, RateOffer]


async def _target_leg_ids(db, *, vessel_code: str | None, before: datetime | None):
    stmt = select(Leg.id)
    if vessel_code:
        v = (await db.execute(select(Vessel).where(Vessel.code == vessel_code))).scalar_one_or_none()
        if v is None:
            raise SystemExit(f"Navire {vessel_code!r} introuvable.")
        stmt = stmt.where(Leg.vessel_id == v.id)
    if before:
        stmt = stmt.where(Leg.etd < before)
    return stmt


async def run(*, execute: bool, vessel_code: str | None, before: datetime | None) -> int:
    async with SessionLocal() as db:
        leg_ids_subq = await _target_leg_ids(db, vessel_code=vessel_code, before=before)

        n_legs = await db.scalar(
            select(func.count()).select_from(select(Leg.id).where(Leg.id.in_(leg_ids_subq)).subquery())
        )
        bookings_subq = select(Booking.id).where(Booking.leg_id.in_(leg_ids_subq))
        n_bookings = await db.scalar(
            select(func.count()).select_from(bookings_subq.subquery())
        )

        print(f"Legs ciblés        : {n_legs}")
        print(f"Bookings impactés  : {n_bookings}")
        if not execute:
            print("\n[DRY-RUN] Rien supprimé. Relancer avec --yes pour exécuter.")
            return 0
        if n_legs == 0:
            print("Aucun leg à purger.")
            return 0

        # 1. Délier les liens optionnels booking → invoices / certifs / claims
        for model in (ClientInvoice, AnemosCertificate, Claim):
            await db.execute(
                update(model).where(model.booking_id.in_(bookings_subq)).values(booking_id=None)
            )

        # 2. Supprimer les bookings (booking_items en CASCADE DB)
        await db.execute(delete(Booking).where(Booking.leg_id.in_(leg_ids_subq)))

        # 3. Supprimer les données opérationnelles propres au leg
        for model in LEG_OWNED_DELETE:
            await db.execute(delete(model).where(model.leg_id.in_(leg_ids_subq)))

        # 4. Délier les FK nullables qui survivent
        for model in LEG_NULLABLE:
            await db.execute(
                update(model).where(model.leg_id.in_(leg_ids_subq)).values(leg_id=None)
            )

        # 5. Supprimer les legs
        await db.execute(delete(Leg).where(Leg.id.in_(leg_ids_subq)))

        await db.commit()
        print(f"\n✓ Purge terminée : {n_legs} legs + {n_bookings} bookings supprimés.")
        return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Purge des legs et données dépendantes")
    p.add_argument("--yes", action="store_true", help="Exécute réellement (sinon dry-run)")
    p.add_argument("--vessel", default=None, help="Limiter à un code navire (ex. ANE)")
    p.add_argument("--before", default=None, help="Limiter aux legs ETD < AAAA-MM-JJ")
    args = p.parse_args()

    before = None
    if args.before:
        before = datetime.fromisoformat(args.before).replace(tzinfo=timezone.utc)

    return asyncio.run(run(execute=args.yes, vessel_code=args.vessel, before=before))


if __name__ == "__main__":
    sys.exit(main())
