#!/usr/bin/env python3
"""Annule EXCEPTIONNELLEMENT une arrivée (ATA) déclarée par erreur, sur UN leg nommé.

⚠️ Ceci n'est pas une fonctionnalité. L'interface ne propose délibérément aucun
bouton d'annulation d'arrivée (décision du 2026-09-04) : une déclaration
d'arrivée est un fait opérationnel qui engage le SOF, la finance, les bookings
et le planning aval. Cet outil existe pour réparer une saisie erronée
identifiée, sous contrôle humain, pas pour ouvrir un geste courant.

CE QU'IL DÉFAIT
  • ``leg.ata`` → NULL, statut recalculé par ``refresh_leg_status`` (le leg
    redevient « en mer », l'ATD restant posé) ;
  • l'événement SOF **EOSP** créé par la déclaration — refusé si le commandant
    l'a signé (``is_locked``) : un SOF signé est immuable, c'est tout son objet ;
  • les bookings passés à ``discharged`` par l'arrivée → ramenés à ``at_sea``.

CE QU'IL NE DÉFAIT PAS, ET POURQUOI
  • **Les certificats Anemos déjà émis.** Le passage d'un booking à
    ``discharged`` déclenche l'émission (cf. ``services/anemos.py``). Un
    certificat est un document opposable, publiquement vérifiable sur
    ``/verify`` : le retirer est une décision de direction, pas une opération de
    maintenance. Le script les LISTE et s'arrête si l'on ne confirme pas.
  • **L'historique de planning** (``schedule_history``) : registre append-only.
    L'annulation y ajoute une entrée, elle n'efface pas la précédente.
  • **Les notifications déjà parties** (EOSP, activation du leg suivant). Elles
    sont sorties du système ; prévenir les destinataires est un geste humain.
  • **Le recalage des legs aval**, s'il a eu lieu. Le script le détecte et le
    signale : le rejouer à l'envers demande de savoir ce que l'opérateur veut,
    pas ce que le code peut.

SÉCURITÉ : DRY-RUN par défaut. ``--commit`` pour appliquer, et
``--i-know-certificates-were-issued`` en plus si des certificats existent.

Usage (sur le serveur) :
  docker compose exec app python -m scripts.cancel_arrival --leg 3BREBR6
  docker compose exec app python -m scripts.cancel_arrival --leg 3BREBR6 --commit
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models.anemos_certificate import AnemosCertificate
from app.models.booking import Booking
from app.models.leg import Leg
from app.models.sof_event import SofEvent
from app.services.planning import ensure_utc, refresh_leg_status

# Statut rendu aux bookings que l'arrivée avait fait passer à « débarqué ».
# L'ATD reste posé : la marchandise est en mer, pas au port de chargement.
REVERTED_BOOKING_STATUS = "at_sea"


async def _load_leg(db: AsyncSession, leg_code: str) -> Leg:
    leg = (
        await db.execute(select(Leg).where(Leg.leg_code == leg_code.upper()))
    ).scalar_one_or_none()
    if leg is None:
        raise SystemExit(f"Leg inconnu : {leg_code}")
    if leg.ata is None:
        raise SystemExit(f"{leg.leg_code} ne porte aucune ATA — rien à annuler.")
    return leg


@dataclass(frozen=True)
class ArrivalImpact:
    """Ce qu'une annulation d'arrivée toucherait, relevé avant d'écrire."""

    leg: Leg
    eosp: list[SofEvent]
    locked_eosp: list[SofEvent]
    discharged: list[Booking]
    certificates: list[AnemosCertificate]


async def inspect(db: AsyncSession, leg: Leg) -> ArrivalImpact:
    """Relève l'empreinte de l'arrivée déclarée, sans rien modifier."""
    eosp = list(
        (
            await db.execute(
                select(SofEvent).where(SofEvent.leg_id == leg.id, SofEvent.event_type == "EOSP")
            )
        )
        .scalars()
        .all()
    )
    discharged = list(
        (
            await db.execute(
                select(Booking).where(Booking.leg_id == leg.id, Booking.status == "discharged")
            )
        )
        .scalars()
        .all()
    )
    certificates: list[AnemosCertificate] = []
    if discharged:
        certificates = list(
            (
                await db.execute(
                    select(AnemosCertificate).where(
                        AnemosCertificate.booking_id.in_([b.id for b in discharged])
                    )
                )
            )
            .scalars()
            .all()
        )
    return ArrivalImpact(
        leg=leg,
        eosp=eosp,
        locked_eosp=[e for e in eosp if getattr(e, "is_locked", False)],
        discharged=discharged,
        certificates=certificates,
    )


class ArrivalCancelError(Exception):
    """Annulation refusée — la donnée s'y oppose."""


async def apply_cancellation(
    db: AsyncSession, impact: ArrivalImpact, *, actor_name: str = "script:cancel_arrival"
) -> None:
    """Annule l'arrivée. Refuse si un EOSP est signé : un SOF signé est immuable."""
    from app.services import schedule_history
    from app.services.activity import record as activity_record
    from app.services.finance_rollup import rollup_for_leg

    if impact.locked_eosp:
        raise ArrivalCancelError(
            "Un EOSP signé par le commandant est immuable — faites lever la signature d'abord."
        )

    leg = impact.leg
    old_ata = ensure_utc(leg.ata)
    for e in impact.eosp:
        await db.delete(e)
    for b in impact.discharged:
        b.status = REVERTED_BOOKING_STATUS
    leg.ata = None
    refresh_leg_status(leg)
    await db.flush()

    # Registre append-only : on ajoute l'annulation, on n'efface rien.
    await schedule_history.record(
        db,
        leg=leg,
        old_etd=ensure_utc(leg.etd),
        new_etd=ensure_utc(leg.etd),
        old_eta=ensure_utc(leg.eta),
        new_eta=ensure_utc(leg.eta),
        old_ata=old_ata,
        new_ata=None,
        source="arrival_cancelled",
        batch_id=uuid.uuid4().hex[:12],
        detail="Arrivée déclarée par erreur — annulation exceptionnelle (script).",
        user_name=actor_name,
    )
    # Le rollup est dérivé : son échec ne doit pas retenir l'annulation, qui
    # est le geste demandé. Il se relance depuis l'écran finance.
    with contextlib.suppress(Exception):
        await rollup_for_leg(db, leg)
    await activity_record(
        db,
        action="arrival_cancelled",
        user_name=actor_name,
        module="escale",
        entity_type="leg",
        entity_id=leg.id,
        entity_label=leg.leg_code,
        detail=(
            f"ATA {old_ata} annulée ; {len(impact.eosp)} EOSP supprimé(s) ; "
            f"{len(impact.discharged)} booking(s) → {REVERTED_BOOKING_STATUS} ; "
            f"{len(impact.certificates)} certificat(s) laissé(s) en place."
        ),
    )


async def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--leg", required=True, help="Code du leg (ex. 3BREBR6). Un seul, explicite.")
    ap.add_argument("--commit", action="store_true", help="Applique (sinon aperçu).")
    ap.add_argument(
        "--i-know-certificates-were-issued",
        action="store_true",
        help="Confirme avoir traité le cas des certificats Anemos déjà émis.",
    )
    ap.add_argument("--actor", default="script:cancel_arrival", help="Nom porté au journal.")
    args = ap.parse_args()

    async with SessionLocal() as db:
        leg = await _load_leg(db, args.leg)
        impact = await inspect(db, leg)

        print(f"Leg {leg.leg_code} — statut « {leg.status} »")
        print(f"  ATD : {ensure_utc(leg.atd)}")
        print(f"  ATA : {ensure_utc(leg.ata)}   ← à annuler")
        print(
            f"  SOF EOSP : {len(impact.eosp)} événement(s), "
            f"dont {len(impact.locked_eosp)} signé(s)"
        )
        print(
            f"  Bookings « discharged » : {len(impact.discharged)} " f"→ {REVERTED_BOOKING_STATUS}"
        )

        if impact.certificates:
            print(
                f"\n🔴 {len(impact.certificates)} certificat(s) Anemos déjà émis pour ces "
                "bookings — documents opposables, vérifiables publiquement sur /verify :"
            )
            for c in impact.certificates:
                print(f"     {c.reference}  (booking {c.booking_id})")
            print("     Le script n'y touche pas. Leur sort est une décision de direction.\n")

        if impact.locked_eosp:
            raise SystemExit(
                "\nARRÊT — un EOSP signé par le commandant est immuable (c'est son objet). "
                "Faites lever la signature avant d'annuler l'arrivée."
            )
        if not args.commit:
            print("\nDRY-RUN — rien n'a été écrit. Relancez avec --commit pour appliquer.")
            return
        if impact.certificates and not args.i_know_certificates_were_issued:
            raise SystemExit(
                "ARRÊT — des certificats sont émis. Relancez avec "
                "--i-know-certificates-were-issued une fois leur sort tranché."
            )

        try:
            await apply_cancellation(db, impact, actor_name=args.actor)
        except ArrivalCancelError as exc:
            raise SystemExit(f"ARRÊT — {exc}") from exc
        await db.commit()

        print(f"\n{leg.leg_code} : arrivée annulée — statut « {leg.status} ».")
        print("  Restent à traiter à la main : notifications EOSP et activation du leg")
        print("  suivant déjà parties, et le sort des certificats listés ci-dessus.")


if __name__ == "__main__":
    asyncio.run(main())
