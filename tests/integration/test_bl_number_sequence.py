"""Séquence de numéros de connaissement — non recyclable. §4.4.

Le numéro était calculé comme *nombre de BL émis sur le leg + 1*. Deux défauts, tous
deux graves sur un registre opposable :

1. **🔴 recyclage** — supprimer un lot faisait baisser le compteur, et le numéro
   suivant réattribuait un numéro **déjà consommé**. Deux documents différents
   pouvaient porter le même numéro à deux moments de l'histoire ;
2. **🔴 blocage** — si le lot supprimé n'était pas le dernier (001, 002, 003 avec 002
   supprimé), le compteur valait 2, le code retentait 003, entrait en collision avec
   l'unicité et **échouait après 5 tentatives**. L'émission devenait impossible sur ce
   voyage.

Les **trous** dans la numérotation sont désormais normaux et attendus : ils sont la
trace d'un numéro consommé puis abandonné, ce qu'un registre doit conserver.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.packing_list import assign_bl_number


async def _leg(db, *, code="1CFRBR6"):
    from app.models.leg import Leg
    from app.models.port import Port
    from app.models.vessel import Vessel

    v = Vessel(name=f"Navire {code}", code=code[:1])
    pol = Port(locode=f"FR{code[:3]}", name="Fécamp", country="FR")
    pod = Port(locode=f"BR{code[:3]}", name="Santos", country="BR")
    db.add_all([v, pol, pod])
    await db.flush()
    base = datetime(2026, 8, 10, tzinfo=UTC)
    leg = Leg(
        leg_code=code,
        vessel_id=v.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd_ref=base,
        eta_ref=base + timedelta(days=20),
        etd=base,
        eta=base + timedelta(days=20),
    )
    db.add(leg)
    await db.flush()
    return leg


async def _pl(db, leg, *, ref="CMD-SEQ"):
    from app.models.commercial import Client, Order
    from app.models.packing_list import PackingList

    cl = Client(name=f"ACME {ref}", client_type="shipper")
    db.add(cl)
    await db.flush()
    order = Order(reference=ref, client_id=cl.id, leg_id=leg.id)
    db.add(order)
    await db.flush()
    pl = PackingList(order_id=order.id, leg_id=leg.id)
    db.add(pl)
    await db.flush()
    return pl


async def _batch(db, pl, n):
    from app.models.packing_list import PackingListBatch

    b = PackingListBatch(packing_list_id=pl.id, batch_number=n, pallet_count=1)
    db.add(b)
    await db.flush()
    return b


# ───────────────────────── numérotation nominale ─────────────────────────


@pytest.mark.asyncio
async def test_numbers_increment_within_a_voyage(db):
    leg = await _leg(db)
    pl = await _pl(db, leg)
    numbers = [await assign_bl_number(db, pl, await _batch(db, pl, i), leg) for i in range(1, 4)]
    assert numbers == ["TUAW_1CFRBR6_001", "TUAW_1CFRBR6_002", "TUAW_1CFRBR6_003"]


@pytest.mark.asyncio
async def test_assignment_is_idempotent(db):
    leg = await _leg(db)
    pl = await _pl(db, leg)
    b = await _batch(db, pl, 1)
    first = await assign_bl_number(db, pl, b, leg)
    assert await assign_bl_number(db, pl, b, leg) == first


@pytest.mark.asyncio
async def test_two_voyages_have_independent_sequences(db):
    leg_a = await _leg(db, code="1CFRBR6")
    leg_b = await _leg(db, code="2AFRBR6")
    pl_a = await _pl(db, leg_a, ref="CMD-A")
    pl_b = await _pl(db, leg_b, ref="CMD-B")

    assert await assign_bl_number(db, pl_a, await _batch(db, pl_a, 1), leg_a) == "TUAW_1CFRBR6_001"
    assert await assign_bl_number(db, pl_b, await _batch(db, pl_b, 1), leg_b) == "TUAW_2AFRBR6_001"


# ───────────── 🔴 le cœur : un numéro consommé ne revient jamais ─────────────


@pytest.mark.asyncio
async def test_deleting_the_last_batch_does_not_free_its_number(db):
    """🔴 Le recyclage. Avant, le compteur baissait et 002 était réattribué.

    Deux documents différents auraient alors porté le même numéro à deux moments de
    l'histoire du registre.
    """
    leg = await _leg(db)
    pl = await _pl(db, leg)
    b1 = await _batch(db, pl, 1)
    b2 = await _batch(db, pl, 2)
    await assign_bl_number(db, pl, b1, leg)
    assert await assign_bl_number(db, pl, b2, leg) == "TUAW_1CFRBR6_002"

    await db.delete(b2)
    await db.flush()

    b3 = await _batch(db, pl, 3)
    assert await assign_bl_number(db, pl, b3, leg) == "TUAW_1CFRBR6_003", (
        "le numéro 002, déjà consommé, a été réattribué"
    )


@pytest.mark.asyncio
async def test_deleting_a_middle_batch_does_not_block_issuance(db):
    """🔴 Le blocage. Avant : compteur à 2, retente 003, collision, échec après
    5 tentatives — l'émission devenait impossible sur ce voyage."""
    leg = await _leg(db)
    pl = await _pl(db, leg)
    b1, b2, b3 = [await _batch(db, pl, i) for i in (1, 2, 3)]
    for b in (b1, b2, b3):
        await assign_bl_number(db, pl, b, leg)

    await db.delete(b2)  # on supprime le lot du MILIEU
    await db.flush()

    b4 = await _batch(db, pl, 4)
    assert await assign_bl_number(db, pl, b4, leg) == "TUAW_1CFRBR6_004"


@pytest.mark.asyncio
async def test_gaps_are_normal_and_preserved(db):
    """Un trou est la trace d'un numéro consommé puis abandonné : on le garde."""
    from app.models.packing_list import PackingListBatch

    leg = await _leg(db)
    pl = await _pl(db, leg)
    kept = await _batch(db, pl, 1)
    doomed = await _batch(db, pl, 2)
    await assign_bl_number(db, pl, kept, leg)
    await assign_bl_number(db, pl, doomed, leg)
    await db.delete(doomed)
    await db.flush()
    await assign_bl_number(db, pl, await _batch(db, pl, 3), leg)

    from sqlalchemy import select

    numbers = sorted(
        n
        for n in (
            await db.execute(
                select(PackingListBatch.bl_number).where(PackingListBatch.bl_number.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    assert numbers == ["TUAW_1CFRBR6_001", "TUAW_1CFRBR6_003"]


# ───────────────────── amorçage sur un voyage historique ─────────────────────


@pytest.mark.asyncio
async def test_the_counter_is_seeded_from_the_highest_suffix_not_the_count(db):
    """🔴 Le point délicat de l'amorçage.

    Un voyage antérieur à cette table porte déjà des numéros. Amorcer le compteur sur
    leur *nombre* recyclerait dès la première émission ; il faut le plus grand
    suffixe. Ici trois numéros existent mais le plus grand est 007.
    """
    leg = await _leg(db)
    pl = await _pl(db, leg)
    for i, seq in enumerate((1, 5, 7), start=1):
        b = await _batch(db, pl, i)
        b.bl_number = f"TUAW_1CFRBR6_{seq:03d}"
        b.bl_issued_at = datetime.now(UTC)
    await db.flush()

    fresh = await _batch(db, pl, 99)
    assert await assign_bl_number(db, pl, fresh, leg) == "TUAW_1CFRBR6_008"


@pytest.mark.asyncio
async def test_a_non_conforming_historical_number_does_not_break_seeding(db):
    """Un numéro hors format ne doit pas faire échouer l'amorçage, seulement ne pas
    y contribuer."""
    leg = await _leg(db)
    pl = await _pl(db, leg)
    weird = await _batch(db, pl, 1)
    weird.bl_number = "TUAW_1CFRBR6_ANCIEN"
    await db.flush()

    fresh = await _batch(db, pl, 2)
    assert await assign_bl_number(db, pl, fresh, leg) == "TUAW_1CFRBR6_001"


@pytest.mark.asyncio
async def test_another_voyages_numbers_do_not_seed_this_one(db):
    """Le préfixe isole les voyages : sinon un voyage chargé décalerait les autres."""
    leg_a = await _leg(db, code="1CFRBR6")
    leg_b = await _leg(db, code="2AFRBR6")
    pl_a = await _pl(db, leg_a, ref="CMD-A2")
    pl_b = await _pl(db, leg_b, ref="CMD-B2")
    for i in range(1, 6):
        await assign_bl_number(db, pl_a, await _batch(db, pl_a, i), leg_a)

    assert await assign_bl_number(db, pl_b, await _batch(db, pl_b, 1), leg_b) == "TUAW_2AFRBR6_001"


# ───────────────────── le compteur ne décroît jamais ─────────────────────


@pytest.mark.asyncio
async def test_the_counter_row_only_grows(db):
    from app.models.packing_list import BlNumberSequence

    leg = await _leg(db)
    pl = await _pl(db, leg)
    b1 = await _batch(db, pl, 1)
    b2 = await _batch(db, pl, 2)
    await assign_bl_number(db, pl, b1, leg)
    await assign_bl_number(db, pl, b2, leg)
    row = await db.get(BlNumberSequence, leg.id)
    assert row.last_seq == 2

    await db.delete(b2)
    await db.flush()
    await assign_bl_number(db, pl, await _batch(db, pl, 3), leg)

    row = await db.get(BlNumberSequence, leg.id)
    assert row.last_seq == 3, "le compteur a régressé après une suppression"


@pytest.mark.asyncio
async def test_the_database_refuses_a_negative_counter(db):
    """⚠️ Un compteur négatif signalerait une décrémentation — interdite en base."""
    from sqlalchemy.exc import IntegrityError

    from app.models.packing_list import BlNumberSequence

    leg = await _leg(db)
    db.add(BlNumberSequence(leg_id=leg.id, last_seq=-1))
    with pytest.raises(IntegrityError):
        await db.flush()


# ───────────────────── cas dégradé : aucun voyage ─────────────────────


@pytest.mark.asyncio
async def test_a_packing_list_without_a_leg_still_gets_distinct_numbers(db):
    """Sans voyage il n'y a pas de clé de séquence — mais plus de collision en boucle.

    Le repli lit le plus grand suffixe connu, pas leur nombre : c'est moins fort
    (supprimer le dernier libère son numéro) mais l'émission n'est plus bloquée par un
    trou au milieu de la série.
    """
    leg = await _leg(db)
    pl = await _pl(db, leg)
    a = await assign_bl_number(db, pl, await _batch(db, pl, 1), None)
    b = await assign_bl_number(db, pl, await _batch(db, pl, 2), None)
    assert (a, b) == ("TUAW_NA_001", "TUAW_NA_002")
