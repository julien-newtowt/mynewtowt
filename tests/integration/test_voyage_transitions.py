"""PLN-SEQ — séquence déclarative départ/arrivée (``services.voyage_transitions``).

Couvre la chaîne complète : gardes de séquence (départ avant arrivée, pas de
chevauchement), inscription SOF, re-ancrage d'ETA sur l'ATD, recalage des legs
suivants (cascade), activation du leg suivant à l'arrivée, historisation de
tous les mouvements (``schedule_revisions``), idempotence des re-déclarations.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel

BASE = datetime(2026, 4, 1, tzinfo=UTC)


async def _setup_two_legs(db):
    """Deux legs enchaînés du même navire : A→B puis B→A (48 h d'escale)."""
    db.add(Vessel(id=1, code="1", name="Anemos"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    leg1 = Leg(
        id=1,
        leg_code="1AFRBR6",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=BASE,
        eta_ref=BASE + timedelta(days=20),
        etd=BASE,
        eta=BASE + timedelta(days=20),
        port_stay_planned_hours=48,
    )
    leg2 = Leg(
        id=2,
        leg_code="1BBRFR6",
        vessel_id=1,
        departure_port_id=2,
        arrival_port_id=1,
        etd_ref=BASE + timedelta(days=24),
        eta_ref=BASE + timedelta(days=44),
        etd=BASE + timedelta(days=24),
        eta=BASE + timedelta(days=44),
    )
    db.add_all([leg1, leg2])
    await db.flush()
    return leg1, leg2


def _naive(dt):
    return dt.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_arrival_requires_departure(db):
    """Séquence : pas d'arrivée sans départ déclaré."""
    from app.services.voyage_transitions import VoyageSequenceError, declare_arrival

    leg1, _ = await _setup_two_legs(db)
    with pytest.raises(VoyageSequenceError):
        await declare_arrival(db, leg1, at=BASE + timedelta(days=20))
    assert leg1.ata is None


@pytest.mark.asyncio
async def test_departure_declares_sof_recalcs_eta_and_cascades(db):
    from app.models.schedule_revision import ScheduleRevision
    from app.models.sof_event import SofEvent
    from app.services.voyage_transitions import declare_departure

    leg1, leg2 = await _setup_two_legs(db)
    # Départ 3 jours après l'ETD prévisionnel.
    at = BASE + timedelta(days=3)
    summary = await declare_departure(db, leg1, at=at, actor_name="ops")

    assert summary["first"] is True and summary["changed"] is True
    # Réel posé, navigation ouverte, statut/phase.
    assert _naive(leg1.atd) == _naive(at)
    assert leg1.status == "in_progress"
    assert leg1.phase == "en_mer"
    # SOF : SOSP inscrit au registre.
    assert summary["sof_created"] is True
    sosp = (await db.execute(SofEvent.__table__.select())).fetchall()
    assert len(sosp) == 1 and sosp[0].event_type == "SOSP"
    # ETA re-ancrée : durée de transit conservée (20 j) depuis l'ATD.
    assert _naive(leg1.eta) == _naive(at + timedelta(days=20))
    assert summary["eta_shift_hours"] == pytest.approx(72.0)
    # Cascade : leg2 démarrait J+24, la nouvelle disponibilité est
    # J+23 (ETA) + 48 h d'escale = J+25 → leg2 repoussé à J+25 (durée conservée).
    assert _naive(leg2.etd) == _naive(BASE + timedelta(days=25))
    assert _naive(leg2.eta) == _naive(BASE + timedelta(days=45))
    # Historisation : mouvement du réel sur leg1 + cascade sur leg2.
    revs = (await db.execute(ScheduleRevision.__table__.select())).fetchall()
    by_source = {}
    for r in revs:
        by_source.setdefault(r.source, []).append(r)
    dep = by_source["departure_declared"]
    assert len(dep) == 1 and dep[0].leg_id == leg1.id
    assert dep[0].old_atd is None and _naive(dep[0].new_atd) == _naive(at)
    casc = by_source["cascade"]
    assert len(casc) == 1 and casc[0].leg_id == leg2.id
    assert casc[0].trigger_leg_id == leg1.id
    # Les deux lignes partagent le même lot (chaîne causale reconstituable).
    assert dep[0].batch_id == casc[0].batch_id


@pytest.mark.asyncio
async def test_departure_redeclaration_is_idempotent_then_correctable(db):
    from app.models.schedule_revision import ScheduleRevision
    from app.services.voyage_transitions import declare_departure

    leg1, _ = await _setup_two_legs(db)
    at = BASE + timedelta(days=1)
    await declare_departure(db, leg1, at=at)

    async def _rev_count() -> int:
        return len((await db.execute(ScheduleRevision.__table__.select())).fetchall())

    n = await _rev_count()
    # Même horodatage → aucun nouvel enregistrement.
    summary = await declare_departure(db, leg1, at=at)
    assert summary["changed"] is False
    assert await _rev_count() == n
    # Horodatage différent → correction tracée (ancienne → nouvelle valeur).
    summary = await declare_departure(db, leg1, at=at + timedelta(hours=6))
    assert summary["changed"] is True and summary["first"] is False
    revs = (await db.execute(ScheduleRevision.__table__.select())).fetchall()
    corr = [r for r in revs if r.source == "departure_declared" and r.old_atd is not None]
    assert len(corr) == 1
    assert _naive(corr[0].old_atd) == _naive(at)
    assert _naive(corr[0].new_atd) == _naive(at + timedelta(hours=6))


@pytest.mark.asyncio
async def test_arrival_closes_navigation_and_activates_next_leg(db):
    from app.models.notification import Notification
    from app.models.sof_event import SofEvent
    from app.services.voyage_transitions import declare_arrival, declare_departure

    leg1, leg2 = await _setup_two_legs(db)
    await declare_departure(db, leg1, at=BASE)
    # Arrivée 2 jours après l'ETA (J+22) → leg2 re-ancré sur ATA + 48 h (J+24…
    # déjà à J+24 : pas de décalage) ; on prend J+23 pour forcer le recalage :
    at = BASE + timedelta(days=23)
    summary = await declare_arrival(db, leg1, at=at, actor_name="ops")

    assert _naive(leg1.ata) == _naive(at)
    assert leg1.status == "in_progress"  # la clôture de voyage reste un workflow
    assert leg1.phase == "a_quai"
    # SOF : EOSP inscrit.
    types = {r.event_type for r in (await db.execute(SofEvent.__table__.select())).fetchall()}
    assert types == {"SOSP", "EOSP"}
    # Legs suivants recalés sur le réel : disponibilité = ATA + 48 h = J+25.
    assert _naive(leg2.etd) == _naive(BASE + timedelta(days=25))
    # Activation du leg suivant : signalée + notifiée aux Opérations.
    assert summary["next_leg_id"] == leg2.id
    notif_types = [r.type for r in (await db.execute(Notification.__table__.select())).fetchall()]
    assert "leg_activated" in notif_types
    assert "eosp" in notif_types and "sosp" in notif_types


@pytest.mark.asyncio
async def test_arrival_cannot_precede_departure(db):
    from app.services.voyage_transitions import (
        VoyageSequenceError,
        declare_arrival,
        declare_departure,
    )

    leg1, _ = await _setup_two_legs(db)
    await declare_departure(db, leg1, at=BASE + timedelta(days=2))
    with pytest.raises(VoyageSequenceError):
        await declare_arrival(db, leg1, at=BASE + timedelta(days=1))
    assert leg1.ata is None


@pytest.mark.asyncio
async def test_sof_channel_does_not_duplicate_sof(db):
    """Canal bord : le SOF est le déclencheur (``create_sof=False``) — la
    déclaration n'inscrit pas de doublon et pose le réel à l'heure de
    l'événement (plus jamais « maintenant »)."""
    from app.models.sof_event import SofEvent
    from app.services.voyage_transitions import declare_departure

    leg1, _ = await _setup_two_legs(db)
    at = BASE + timedelta(hours=8)
    await declare_departure(db, leg1, at=at, create_sof=False)
    assert (await db.execute(SofEvent.__table__.select())).fetchall() == []
    assert _naive(leg1.atd) == _naive(at)


@pytest.mark.asyncio
async def test_cascade_blocked_by_departed_downstream_is_visible(db):
    """Incident de reprogrammation : un leg aval déjà appareillé bloque le
    recalage — rien n'est écrasé, l'incident est notifié aux Opérations."""
    from app.models.notification import Notification
    from app.services.voyage_transitions import declare_departure

    leg1, leg2 = await _setup_two_legs(db)
    # leg2 a (incohéremment) déjà appareillé : la cascade ne doit pas y toucher.
    leg2.atd = leg2.etd
    await db.flush()
    etd2, eta2 = leg2.etd, leg2.eta

    summary = await declare_departure(db, leg1, at=BASE + timedelta(days=10))
    assert any(s.startswith("downstream_legs:") for s in summary["cascade"]["skipped"])
    assert (leg2.etd, leg2.eta) == (etd2, eta2)  # fait réalisé jamais réécrit
    notif_types = [r.type for r in (await db.execute(Notification.__table__.select())).fetchall()]
    assert "cascade_blocked" in notif_types
