"""Annulation exceptionnelle d'une arrivée déclarée par erreur (leg 3BREBR6).

Cas réel : une ATA saisie un jour après le départ d'un voyage de 6287 NM. Il
n'existe **délibérément aucun bouton** d'annulation dans l'interface — une
déclaration d'arrivée engage le SOF, la finance, les bookings et le planning
aval. La réparation passe par un script nommant explicitement le leg.

Ces tests verrouillent les deux moitiés du contrat : ce qui est défait, et
surtout ce qui ne l'est pas.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.anemos_certificate import AnemosCertificate
from app.models.booking import Booking
from app.models.client_account import ClientAccount
from app.models.leg import Leg
from app.models.port import Port
from app.models.schedule_revision import ScheduleRevision
from app.models.sof_event import SofEvent
from app.models.vessel import Vessel
from scripts.cancel_arrival import (
    REVERTED_BOOKING_STATUS,
    ArrivalCancelError,
    apply_cancellation,
    inspect,
)

ATD = datetime(2026, 9, 3, 8, 19, tzinfo=UTC)
ATA = datetime(2026, 9, 4, 6, 20, tzinfo=UTC)


async def _leg_arrive(db, *, code="3BREBR6") -> Leg:
    vessel = Vessel(code="ANE", name="Anemos")
    pol = Port(locode="RERUN", name="Saint-Denis", country="RE")
    pod = Port(locode="BRSSO", name="São Sebastião", country="BR")
    db.add_all([vessel, pol, pod])
    await db.flush()
    leg = Leg(
        leg_code=code,
        vessel_id=vessel.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd_ref=ATD,
        eta_ref=ATD + timedelta(days=33),
        etd=ATD,
        eta=ATD + timedelta(days=33),
        atd=ATD,
        ata=ATA,
        status="in_progress",
    )
    db.add(leg)
    await db.flush()
    return leg


@pytest.mark.asyncio
async def test_l_arrivee_est_annulee_et_le_leg_repart_en_mer(db):
    leg = await _leg_arrive(db)
    db.add(SofEvent(leg_id=leg.id, event_type="EOSP", occurred_at=ATA))
    await db.flush()

    impact = await inspect(db, leg)
    assert len(impact.eosp) == 1

    await apply_cancellation(db, impact)

    assert leg.ata is None
    assert leg.atd == ATD  # le départ, lui, a bien eu lieu
    assert leg.status == "in_progress"
    assert (await db.execute(select(SofEvent))).scalars().all() == []


@pytest.mark.asyncio
async def test_les_bookings_debarques_par_erreur_repassent_en_mer(db):
    leg = await _leg_arrive(db)
    db.add_all(
        [
            Booking(reference="BK-1", leg_id=leg.id, status="discharged"),
            Booking(reference="BK-2", leg_id=leg.id, status="delivered"),
        ]
    )
    await db.flush()

    impact = await inspect(db, leg)
    await apply_cancellation(db, impact)

    par_ref = {b.reference: b.status for b in (await db.execute(select(Booking))).scalars().all()}
    assert par_ref["BK-1"] == REVERTED_BOOKING_STATUS
    # `delivered` est au-delà du débarquement : la livraison est un fait
    # postérieur, l'annulation d'arrivée n'a pas à la défaire.
    assert par_ref["BK-2"] == "delivered"


@pytest.mark.asyncio
async def test_un_eosp_signe_bloque_l_annulation(db):
    """Un SOF signé est immuable — c'est tout son objet. On refuse, on n'écrase pas."""
    leg = await _leg_arrive(db)
    db.add(SofEvent(leg_id=leg.id, event_type="EOSP", occurred_at=ATA, is_locked=True))
    await db.flush()

    impact = await inspect(db, leg)

    with pytest.raises(ArrivalCancelError):
        await apply_cancellation(db, impact)
    assert leg.ata == ATA  # rien n'a bougé


@pytest.mark.asyncio
async def test_les_certificats_emis_sont_releves_et_jamais_touches(db):
    """Un certificat Anemos est opposable et vérifiable publiquement.

    Le script doit les faire remonter pour qu'un humain tranche, et ne jamais
    en disposer lui-même.
    """
    leg = await _leg_arrive(db)
    compte = ClientAccount(email="c@example.test", hashed_password="x", company_name="Belco")
    db.add(compte)
    await db.flush()
    booking = Booking(reference="BK-3", leg_id=leg.id, status="discharged")
    db.add(booking)
    await db.flush()
    db.add(
        AnemosCertificate(
            reference="ANM-2026-0001",
            booking_id=booking.id,
            client_account_id=compte.id,
            leg_id=leg.id,
            tonnage_transported_t=Decimal("12.500"),
            distance_nm=Decimal("6287.00"),
            co2_emitted_kg=Decimal("240.000"),
            co2_conventional_kg=Decimal("2400.000"),
            co2_avoided_kg=Decimal("2160.000"),
        )
    )
    await db.flush()

    impact = await inspect(db, leg)
    assert [c.reference for c in impact.certificates] == ["ANM-2026-0001"]

    await apply_cancellation(db, impact)

    restants = (await db.execute(select(AnemosCertificate))).scalars().all()
    assert [c.reference for c in restants] == ["ANM-2026-0001"]


@pytest.mark.asyncio
async def test_l_annulation_s_inscrit_au_registre_sans_rien_effacer(db):
    """`schedule_history` est append-only : l'annulation s'y ajoute."""
    leg = await _leg_arrive(db)

    await apply_cancellation(db, await inspect(db, leg))

    entrees = (await db.execute(select(ScheduleRevision))).scalars().all()
    annulation = [e for e in entrees if e.source == "arrival_cancelled"]
    assert len(annulation) == 1
    assert annulation[0].old_ata is not None
    assert annulation[0].new_ata is None


@pytest.mark.asyncio
async def test_inspecter_ne_modifie_rien(db):
    """Le dry-run du script repose sur cette garantie."""
    leg = await _leg_arrive(db)
    db.add(SofEvent(leg_id=leg.id, event_type="EOSP", occurred_at=ATA))
    await db.flush()

    await inspect(db, leg)

    assert leg.ata == ATA
    assert len((await db.execute(select(SofEvent))).scalars().all()) == 1
