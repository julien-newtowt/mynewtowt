"""Le statut Schengen ne doit plus affirmer « conforme » sans données.

Régression du 2026-07-30. ``refresh_schengen_for_members`` retombait sur
``compliant`` dès que l'ensemble des jours de présence était vide — y compris
quand des embarquements existaient mais hors de portée du calcul :

- les **plannings Marad** (``MaradCrewSchedule``), là où l'Armement décide
  réellement les relèves, que ce calcul ne sait pas exploiter ;
- les affectations **sans voyage** (``leg_id`` nul, arbitrage A4), que la boucle
  saute explicitement.

Deux chemins menaient donc à un « conforme » sans fondement : le défaut de la
colonne et un décompte à zéro. La conformité Schengen fait foi dans **Marad**
(qui notifie l'Armement en amont) : mynewtowt doit dire qu'il ne sait pas,
plutôt que de rassurer à tort.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest


async def _member(db, mid: int, nationality: str | None) -> None:
    from app.models.crew import CrewMember

    db.add(CrewMember(id=mid, full_name=f"Marin {mid}", role="marin", nationality=nationality))
    await db.flush()


async def _leg_fr(db, *, etd: datetime) -> int:
    """Leg dont le port de départ est en zone Schengen (FR)."""
    from app.models.leg import Leg
    from app.models.port import Port
    from app.models.vessel import Vessel

    db.add(Vessel(id=1, code="ANE", name="Anemos", vessel_class="phoenix"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    db.add(
        Leg(
            id=1,
            leg_code="1CFRBR6",
            vessel_id=1,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=etd,
            eta_ref=etd + timedelta(days=20),
            etd=etd,
            eta=etd + timedelta(days=20),
        )
    )
    await db.flush()
    return 1


@pytest.mark.asyncio
async def test_marad_embarkation_makes_status_indetermine(db):
    """Embarquement connu de Marad seul : « non calculé », pas « conforme »."""
    from app.models.crew import CrewMember, MaradCrewSchedule
    from app.services.crew_compliance import refresh_schengen_for_members

    await _member(db, 1, "PH")  # hors Schengen → la règle 90/180 s'applique
    db.add(
        MaradCrewSchedule(
            marad_schedule_id="s1",
            crew_member_id=1,
            marad_vessel_name="Anemos",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 20),
        )
    )
    await db.flush()

    member = await db.get(CrewMember, 1)
    await refresh_schengen_for_members(db, [member])
    assert member.schengen_status == "indetermine"


@pytest.mark.asyncio
async def test_assignment_without_leg_makes_status_indetermine(db):
    """Affectation sans voyage (A4) : le calcul la saute, il doit le dire."""
    from app.models.crew import CrewAssignment, CrewMember
    from app.services.crew_compliance import refresh_schengen_for_members

    await _member(db, 2, "PH")
    db.add(
        CrewAssignment(
            crew_member_id=2,
            leg_id=None,  # arrêt technique : embarquement hors voyage
            embark_at=datetime(2026, 3, 1, tzinfo=UTC),
            disembark_at=datetime(2026, 3, 20, tzinfo=UTC),
        )
    )
    await db.flush()

    member = await db.get(CrewMember, 2)
    await refresh_schengen_for_members(db, [member])
    assert member.schengen_status == "indetermine"


@pytest.mark.asyncio
async def test_marad_leave_only_is_not_an_embarkation(db):
    """Un congé Marad (sans navire) n'est pas un embarquement : reste concluant."""
    from app.models.crew import CrewMember, MaradCrewSchedule
    from app.services.crew_compliance import refresh_schengen_for_members

    await _member(db, 3, "PH")
    db.add(
        MaradCrewSchedule(
            marad_schedule_id="s3",
            crew_member_id=3,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 30),
            status="Congés",  # pas de navire → période à terre
        )
    )
    await db.flush()

    member = await db.get(CrewMember, 3)
    await refresh_schengen_for_members(db, [member])
    assert member.schengen_status == "compliant"


@pytest.mark.asyncio
async def test_no_embarkation_anywhere_stays_compliant(db):
    """Aucun embarquement dans aucun registre : zéro jour est la vérité."""
    from app.models.crew import CrewMember
    from app.services.crew_compliance import refresh_schengen_for_members

    await _member(db, 4, "PH")
    member = await db.get(CrewMember, 4)
    await refresh_schengen_for_members(db, [member])
    assert member.schengen_status == "compliant"
    assert member.schengen_days_in_window == 0


@pytest.mark.asyncio
async def test_schengen_national_unaffected(db):
    """Ressortissant Schengen : la règle ne s'applique pas, même avec du Marad."""
    from app.models.crew import CrewMember, MaradCrewSchedule
    from app.services.crew_compliance import refresh_schengen_for_members

    await _member(db, 5, "FR")
    db.add(
        MaradCrewSchedule(
            marad_schedule_id="s5",
            crew_member_id=5,
            marad_vessel_name="Anemos",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 20),
        )
    )
    await db.flush()

    member = await db.get(CrewMember, 5)
    await refresh_schengen_for_members(db, [member])
    assert member.schengen_status == "compliant"
    assert member.schengen_days_in_window is None


@pytest.mark.asyncio
async def test_established_breach_outranks_incompleteness(db):
    """Un dépassement certain prime sur l'incertitude du reste du décompte.

    100 jours de présence Schengen établis sur les seules données exploitables
    dépassent déjà le plafond de 90 : la conclusion est certaine, la présence
    d'un planning Marad non exploité ne doit pas la diluer en « non calculé ».
    """
    from app.models.crew import CrewAssignment, CrewMember, MaradCrewSchedule
    from app.services.crew_compliance import refresh_schengen_for_members

    today = date.today()
    embark = datetime.combine(today - timedelta(days=100), datetime.min.time(), tzinfo=UTC)
    disembark = datetime.combine(today - timedelta(days=1), datetime.min.time(), tzinfo=UTC)

    await _member(db, 6, "PH")
    await _leg_fr(db, etd=disembark)  # départ du navire = fin de présence à quai
    db.add(
        CrewAssignment(
            crew_member_id=6,
            leg_id=1,
            embark_at=embark,
            disembark_at=disembark,
        )
    )
    db.add(
        MaradCrewSchedule(
            marad_schedule_id="s6",
            crew_member_id=6,
            marad_vessel_name="Anemos",
            start_date=today - timedelta(days=300),
            end_date=today - timedelta(days=280),
        )
    )
    await db.flush()

    member = await db.get(CrewMember, 6)
    await refresh_schengen_for_members(db, [member])
    assert member.schengen_days_in_window == 100
    assert member.schengen_status == "non_compliant"
