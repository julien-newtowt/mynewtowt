"""Tests golden — dataset événementiel 2025 (MRV LOT 13).

Fixtures compactes extraites du ``Sample_Dataset_Architecture_Evenementielle_
2025.xlsx`` (cf. ``tests/fixtures/mrv_2025/README.md`` — génération, format,
et DÉRIVATION des valeurs attendues). Trois axes :

1. **Chaîne de calcul production** : le loader matérialise un voyage complet
   (événements ``valide`` + compteurs cumulatifs reconstruits) et
   ``inter_event_compute.compute_leg`` doit restituer les consommations
   ME/AE/totale et le ROB chaîné du dataset — propriété de télescopage des
   cumuls : Σ(deltas recalculés) = Σ(deltas source).
2. **Appariement soutages ↔ FLGO** (R24 service-level, lot 7) : les 2
   soutages appariés du dataset le restent via
   ``flgo_sync.flgo_matches_for_bunker`` (2/2), et le soutage BDN 433421
   (ligne R24 du journal ``Controles_Qualite``) reste NON apparié.
3. **Rejeu QC** (``test_qc_replay_2025``) : dormant tant que le registre
   ``validation_engine.RULES`` n'est pas complet (garde ``skipif < 31`` —
   s'activera automatiquement au merge du lot 8).

Les totaux annuels ANEMOS 2025 vs PDF DNV (±1,5 %) ne sont PAS re-parsés ici
(trop lourd pour pytest) : c'est le rôle de
``scripts/import_mrv_2025.py --reconcile`` (résultat chiffré au rapport du
lot) ; pytest garde les totaux du fixture-voyage vs constantes vérifiées.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import validation_engine
from app.services.flgo_sync import flgo_matches_for_bunker
from app.services.inter_event_compute import (
    compute_leg,
    finalized_events_for_leg,
    pair_anchorings,
)
from tests.fixtures.mrv_2025.loader import load_voyage

# Tolérance numérique : les compteurs litres sont stockés Numeric(14,3) —
# l'erreur de reconstruction télescopée reste < 1e-5 t, on garde 1e-3 t.
TOL_T = Decimal("0.001")
TOL_H = Decimal("0.01")


def _close(actual: Decimal | None, expected: Decimal, tol: Decimal = TOL_T) -> bool:
    return actual is not None and abs(actual - expected) <= tol


# ════════════════════════════════════════════════ 1. Voyage complet (1CLA5)


async def test_loader_structure_1cla5(db):
    """Le loader matérialise le voyage complet : leg clôturé + 5 événements
    ``valide`` + relevés (compteurs reconstruits, météo/voilure/cales)."""
    fixture = await load_voyage(db, "1CLA5")

    assert fixture.leg is not None
    assert fixture.leg.leg_code == "1CLA5"
    assert fixture.leg.status == "completed"
    assert fixture.leg.closure_approved_at is not None

    assert len(fixture.events) == fixture.expected["events_count"] == 5
    assert {e.status for e in fixture.events} == {"valide"}
    assert [e.event_type for e in fixture.events] == [
        "departure",
        "noon",
        "arrival",
        "noon",
        "noon",
    ]  # les 2 noons post-arrivée sont une réalité ASSUMÉE du dataset (README)

    # Rechargement par le chemin de PRODUCTION (polymorphe + relevés selectin).
    events = await finalized_events_for_leg(db, fixture.leg.id)
    assert len(events) == 5
    # 5 rôles moteurs vus dans le voyage × 5 événements (carry-forward).
    assert sum(len(e.engine_readings) for e in events) == 25
    noons = [e for e in events if e.event_type == "noon"]
    assert sum(len(n.weather_readings) for n in noons) == 18  # 3 noons × 6 créneaux
    assert sum(len(n.sail_readings) for n in noons) == 18
    assert sum(len(n.hold_readings) for n in noons) == 13

    # Le soutage R24 du voyage (BDN 433421, sans lecture FLGO correspondante).
    assert [b.bdn_number for b in fixture.bunkers] == ["433421"]
    assert fixture.qc_expected and fixture.qc_expected[0]["regle"] == "R24"

    # Idempotence du loader : rechargement = mêmes objets, aucun doublon.
    again = await load_voyage(db, "1CLA5")
    assert again.leg.id == fixture.leg.id
    assert len(again.events) == 5


async def test_compute_leg_1cla5_golden(db):
    """``compute_leg`` restitue les valeurs du dataset (dérivation README).

    Constantes verrouillées en dur (issues des deltas source, vérifiées à la
    main) : ME = 0,0083+0,54+1,53 = 2,0783 t ; AE = 0,60009+0,42+0,36
    = 1,38009 t ; ROB dernier point = 34,7088 − 3,45839 = 31,25041 t.
    """
    fixture = await load_voyage(db, "1CLA5")

    # Verrou anti-dérive : le bloc expected du JSON == constantes vérifiées.
    assert Decimal(fixture.expected["conso_me_t"]) == Decimal("2.0783")
    assert _close(Decimal(fixture.expected["conso_ae_t"]), Decimal("1.38009"))
    assert _close(Decimal(fixture.expected["conso_total_t"]), Decimal("3.45839"))
    assert Decimal(fixture.expected["rob_departure_t"]) == Decimal("34.7088")
    assert Decimal(fixture.expected["rob_arrival_declared_t"]) == Decimal("34.0032")

    computation = await compute_leg(db, fixture.leg)

    assert len(computation.events) == 5
    assert len(computation.intervals) == 4

    totals = computation.totals
    assert _close(totals.conso_me_t, Decimal(fixture.expected["conso_me_t"]))
    assert _close(totals.conso_ae_t, Decimal(fixture.expected["conso_ae_t"]))
    assert _close(totals.conso_total_t, Decimal(fixture.expected["conso_total_t"]))
    assert totals.distance_nm is not None and totals.distance_nm > 0
    assert totals.duration_h is not None and totals.duration_h > 0

    # ROB chaîné : ancré sur le ROB déclaré du Departure (source R14-v2)…
    rob_chain = computation.rob_chain
    assert rob_chain[0].rob_declared_t == Decimal(fixture.expected["rob_departure_t"])
    assert rob_chain[0].rob_calculated_t == Decimal(fixture.expected["rob_departure_t"])
    # …le ROB déclaré à l'Arrival est porté par le point correspondant…
    arrival_point = next(p for p in rob_chain if p.event_type == "arrival")
    assert arrival_point.rob_declared_t == Decimal(fixture.expected["rob_arrival_declared_t"])
    # …et le dernier point calculé vaut ROB départ − conso totale (pas de
    # soutage entre les événements — BDN livré à quai avant le Departure).
    assert _close(
        rob_chain[-1].rob_calculated_t, Decimal(fixture.expected["rob_last_calculated_t"])
    )

    # Aucune anomalie compteur sur ce voyage (deltas source tous ≥ 0).
    assert not any(i.counter_anomaly for i in computation.intervals)


# ═══════════════════════════════════════════ 2. Voyage avec mouillage (1EGB5)


async def test_compute_leg_1egb5_anchoring(db):
    """Voyage à mouillage : appariement Begin↔End (332,83 h) + totaux golden."""
    fixture = await load_voyage(db, "1EGB5")
    assert fixture.leg.leg_code == "1EGB5"
    assert len(fixture.events) == 15

    computation = await compute_leg(db, fixture.leg)
    pairs = pair_anchorings(computation.events)
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.begin_event_id != 0
    assert pair.sequence_no == 1
    # Durée dataset : 332,8333333 h (28/05 23:10 → 11/06 20:00 UTC).
    assert _close(pair.duration_h, Decimal("332.8333333"), TOL_H)

    totals = computation.totals
    assert _close(totals.conso_me_t, Decimal(fixture.expected["conso_me_t"]))
    assert _close(totals.conso_ae_t, Decimal(fixture.expected["conso_ae_t"]))
    assert _close(totals.conso_total_t, Decimal(fixture.expected["conso_total_t"]))

    # Chaîne ROB : le 1er événement du voyage est un Noon ANTÉRIEUR au
    # Departure (réalité du dataset, cf. README) — la chaîne s'ancre donc au
    # Departure (1er PortCall rencontré), pas au 1er événement.
    rob_chain = computation.rob_chain
    departure_point = next(p for p in rob_chain if p.event_type == "departure")
    assert departure_point.rob_declared_t == Decimal(fixture.expected["rob_departure_t"])
    assert departure_point.rob_calculated_t == Decimal(fixture.expected["rob_departure_t"])
    assert rob_chain[0].rob_calculated_t is None  # avant l'ancre : indéterminé
    assert _close(
        rob_chain[-1].rob_calculated_t, Decimal(fixture.expected["rob_last_calculated_t"])
    )


# ═══════════════════════════════════════════ 3. Appariement soutages ↔ FLGO


async def test_bunker_flgo_pairing_2_of_2(db):
    """Les 2 soutages appariés du dataset le restent : 2/2 via
    ``flgo_matches_for_bunker`` (fenêtre R24 = 5 j, écarts source 0,11/0,55 j)."""
    fixture = await load_voyage(db, "bunkers_flgo")
    assert len(fixture.bunkers) == 2
    assert len(fixture.flgo_readings) == 2
    assert {f.action_type for f in fixture.flgo_readings} == {"received"}

    matched = 0
    for bunker in fixture.bunkers:
        match = await flgo_matches_for_bunker(db, bunker)
        assert match.window_days == Decimal("5")
        if match.matched:
            matched += 1
    assert matched == len(fixture.bunkers) == 2


async def test_bunker_433421_stays_unmatched(db):
    """Le soutage de la ligne R24 du journal QC (BDN 433421, 17/02/2025) n'a
    AUCUNE lecture FLGO « Received » sous 5 j — même avec les lectures des
    autres fixtures chargées (la plus proche est à ~21 j)."""
    await load_voyage(db, "bunkers_flgo")  # charge les 2 Received de mars/avril
    fixture = await load_voyage(db, "1CLA5")

    bunker = fixture.bunkers[0]
    assert bunker.bdn_number == "433421"
    match = await flgo_matches_for_bunker(db, bunker)
    assert not match.matched
    assert match.candidates == ()


# ═══════════════════════════════════════════════════════════ 4. Rejeu QC


@pytest.mark.skipif(
    len(validation_engine.RULES) < 30,
    reason="règles complètes du lot 8 requises (registre RULES incomplet — "
    "30 règles au registre, R19 portée par draft_reminders hors registre)",
)
async def test_qc_replay_2025(db):
    """Rejeu du journal ``Controles_Qualite`` sur les fixtures (scope bunker).

    Anomalie choisie dans le journal QC du dataset : **R24 — BDN 433421
    (ANEMOS) sans FlgoReading « Received » sous 5 j** (les seules anomalies
    rejouables du journal 2025 sont R24/R02/Perimetre ; les exemples « ROB
    figé / doublon de date » du plan proviennent du classeur QC 2026, pas de
    ce dataset — les doublons de date source sont d'ailleurs inimportables
    par construction, contrainte ``uq_nav_event_leg_type_dt``).

    Attendu au merge du lot 8 : ``run_rules(scope="bunker")`` reproduit la
    ligne R24 du journal pour le BDN 433421 (fail) et ne signale PAS les 2
    soutages appariés (BDN 2691 / 3891).
    """
    # Catalogue de règles + seuils en base (FK de quality_check_results).
    await validation_engine.seed_reference_data(db)

    matched_fixture = await load_voyage(db, "bunkers_flgo")
    voyage_fixture = await load_voyage(db, "1CLA5")
    unmatched = voyage_fixture.bunkers[0]
    assert unmatched.bdn_number == "433421"
    assert voyage_fixture.qc_expected[0]["regle"] == "R24"  # ligne du journal rejouée

    subjects = [unmatched, *matched_fixture.bunkers]
    summary = await validation_engine.run_rules(
        db, "bunker", subjects, vessel=voyage_fixture.vessel
    )

    r24_fails = [r for r in summary.results if r.rule_id == "R24" and r.result == "fail"]
    assert [r.subject_id for r in r24_fails] == [unmatched.id], (
        "R24 doit signaler exactement le BDN 433421 (journal QC) : "
        f"obtenu {[(r.rule_id, r.subject_id, r.message) for r in r24_fails]}"
    )
