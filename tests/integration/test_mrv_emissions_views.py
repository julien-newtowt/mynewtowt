"""Tests d'intégration — vues d'émissions par voyage et par escale.

Deux écrans de **lecture seule** sur ``voyage_emission_summaries``. Ce que ces
tests verrouillent en priorité n'est pas le rendu, mais **ce que les vues
n'affirment pas** : l'assiette des émissions du grand livre étant la
consommation hors mouillage, la vue escale ne doit jamais laisser croire à une
émission nulle là où aucune émission n'est calculée.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.models.voyage_emission_summary import VoyageEmissionSummary
from app.routers import mrv_router as mr
from app.services import mrv_emission_views as emv
from tests.integration.conftest import FakeRequest

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


async def _leg(db, vessel, ports, *, code, etd, summary: dict | None = None) -> Leg:
    p1, p2 = ports
    leg = Leg(
        leg_code=code,
        vessel_id=vessel.id,
        departure_port_id=p1.id,
        arrival_port_id=p2.id,
        etd_ref=etd,
        eta_ref=etd + timedelta(days=3),
        etd=etd,
        eta=etd + timedelta(days=3),
    )
    db.add(leg)
    await db.flush()
    if summary is not None:
        db.add(VoyageEmissionSummary(leg_id=leg.id, source="events", **summary))
        await db.flush()
    return leg


async def _fleet(db):
    vessel = Vessel(code="ANE", name="Anemos")
    other = Vessel(code="ART", name="Artemis")
    db.add_all([vessel, other])
    await db.flush()
    p1 = Port(name="Fecamp", country="FR", locode="FRFEC", latitude=49.7, longitude=0.37)
    p2 = Port(name="Belem", country="BR", locode="BRBEL", latitude=-1.45, longitude=-48.5)
    db.add_all([p1, p2])
    await db.flush()
    return vessel, other, (p1, p2)


# ═════════════════════════════════════════════ Routes


def test_emissions_routes_registered():
    paths = {r.path for r in mr.router.routes}
    assert "/mrv/emissions/voyages" in paths
    assert "/mrv/emissions/port" in paths


# ═════════════════════════════════════════════ Service


async def test_voyage_view_reads_the_ledger_materialisation(db):
    vessel, _other, ports = await _fleet(db)
    await _leg(
        db,
        vessel,
        ports,
        code="1AFRBR6",
        etd=T0,
        summary={
            "conso_hors_mouillage_t": Decimal("4.500"),
            "conso_mouillage_t": Decimal("0.300"),
            "conso_escale_t": Decimal("1.200"),
            "co2_t": Decimal("14.400"),
            "co2eq_t": Decimal("14.600"),
            "distance_nm": Decimal("3200.00"),
        },
    )

    rows = await emv.voyage_emissions(db)
    assert len(rows) == 1
    row = rows[0]
    assert row.has_summary is True
    # La conso du trajet est l'assiette des émissions — hors mouillage.
    assert row.conso_voyage_t == Decimal("4.500")
    assert row.co2_t == Decimal("14.400")
    assert row.distance_nm == Decimal("3200.00")
    assert row.vessel is not None and row.vessel.code == "ANE"


async def test_port_view_carries_escale_emissions_on_a_disjoint_scope(db):
    """« Port emissions = émissions d'escale » (décision du 2026-09-04).

    L'assiette est **disjointe** de celle du trajet : la vue expose les deux
    grandeurs mais ne les additionne jamais — l'escale d'un voyage peut
    s'étendre sur la fenêtre du voyage suivant.
    """
    vessel, _other, ports = await _fleet(db)
    await _leg(
        db,
        vessel,
        ports,
        code="1AFRBR6",
        etd=T0,
        summary={
            "conso_escale_t": Decimal("1.200"),
            "conso_mouillage_t": Decimal("0.300"),
            "co2_t": Decimal("14.400"),
            "co2_escale_t": Decimal("3.847"),
            "co2eq_escale_t": Decimal("3.900"),
        },
    )

    rows = await emv.port_emissions(db)
    assert len(rows) == 1
    row = rows[0]
    assert row.conso_escale_t == Decimal("1.200")
    assert row.co2_escale_t == Decimal("3.847")
    assert row.co2eq_escale_t == Decimal("3.900")
    # Le port de l'escale est le POD du voyage qui arrive.
    assert row.arrival_port is not None and row.arrival_port.locode == "BRBEL"
    # Les deux assiettes restent distinctes sur la ligne — aucun agrégat.
    assert row.co2_t == Decimal("14.400")
    assert row.co2_t != row.co2_escale_t


async def test_escale_emission_absent_is_shown_as_not_computed(db):
    """Le résumé est un cache : une ligne antérieure à la migration 0143 n'a
    pas encore été recalculée. ``None`` doit rester ``None`` jusque-là, jamais
    devenir un zéro."""
    vessel, _other, ports = await _fleet(db)
    await _leg(
        db,
        vessel,
        ports,
        code="1AFRBR6",
        etd=T0,
        summary={"conso_escale_t": Decimal("1.200"), "co2_t": Decimal("14.400")},
    )

    row = (await emv.port_emissions(db))[0]
    assert row.conso_escale_t == Decimal("1.200")
    assert row.co2_escale_t is None
    assert row.co2eq_escale_t is None


async def test_port_view_excludes_a_voyage_not_yet_arrived(db):
    """``conso_escale_t`` est ``None`` tant que le voyage n'est pas arrivé
    (G12) : une ligne d'escale sans séjour n'aurait rien à dire."""
    vessel, _other, ports = await _fleet(db)
    await _leg(
        db,
        vessel,
        ports,
        code="1AFRBR6",
        etd=T0,
        summary={"conso_hors_mouillage_t": Decimal("2.000"), "co2_t": Decimal("6.400")},
    )

    assert await emv.port_emissions(db) == []
    # Le même voyage reste visible côté trajet — il a navigué.
    assert len(await emv.voyage_emissions(db)) == 1


async def test_a_leg_without_summary_is_shown_as_not_computed(db):
    """Ne jamais confondre « pas encore calculé » et « calculé à zéro »."""
    vessel, _other, ports = await _fleet(db)
    await _leg(db, vessel, ports, code="1AFRBR6", etd=T0, summary=None)

    rows = await emv.voyage_emissions(db)
    assert len(rows) == 1
    assert rows[0].has_summary is False
    assert rows[0].co2_t is None
    assert rows[0].conso_voyage_t is None


async def test_vessel_filter_and_vessels_list(db):
    vessel, other, ports = await _fleet(db)
    await _leg(db, vessel, ports, code="1AFRBR6", etd=T0, summary={"co2_t": Decimal("1.000")})
    await _leg(
        db,
        other,
        ports,
        code="2AFRBR6",
        etd=T0 + timedelta(days=10),
        summary={"co2_t": Decimal("2.000")},
    )

    assert len(await emv.voyage_emissions(db)) == 2
    only = await emv.voyage_emissions(db, vessel_id=other.id)
    assert [r.vessel.code for r in only] == ["ART"]
    # Le filtre ne propose que les navires porteurs d'un résumé.
    assert {v.code for v in await emv.vessels_with_summaries(db)} == {"ANE", "ART"}


async def test_rows_are_ordered_most_recent_first(db):
    vessel, _other, ports = await _fleet(db)
    await _leg(db, vessel, ports, code="1AFRBR6", etd=T0, summary={"co2_t": Decimal("1")})
    await _leg(
        db,
        vessel,
        ports,
        code="1BFRBR6",
        etd=T0 + timedelta(days=30),
        summary={"co2_t": Decimal("2")},
    )

    rows = await emv.voyage_emissions(db)
    assert [r.leg.leg_code for r in rows] == ["1BFRBR6", "1AFRBR6"]


# ═════════════════════════════════════════════ Écrans


async def test_screens_render_with_the_right_scope(db, staff_user):
    vessel, _other, ports = await _fleet(db)
    await _leg(
        db,
        vessel,
        ports,
        code="1AFRBR6",
        etd=T0,
        summary={"conso_escale_t": Decimal("1.2"), "co2_t": Decimal("9")},
    )

    voyage = await mr.mrv_emissions_voyages(FakeRequest(), db=db, user=staff_user)
    port = await mr.mrv_emissions_port(FakeRequest(), db=db, user=staff_user)

    assert voyage.status_code == 200 and port.status_code == 200
    assert voyage.template.name == "staff/mrv/emissions.html"
    assert port.template.name == "staff/mrv/emissions.html"
    assert voyage.context["scope"] == "voyage"
    assert port.context["scope"] == "port"


async def test_screens_render_empty_without_crashing(db, staff_user):
    for coro in (mr.mrv_emissions_voyages, mr.mrv_emissions_port):
        resp = await coro(FakeRequest(), db=db, user=staff_user)
        assert resp.status_code == 200
        assert resp.context["rows"] == []


async def test_unknown_vessel_falls_back_to_fleet(db, staff_user):
    """Même repli silencieux que les autres écrans filtrables du dépôt."""
    vessel, _other, ports = await _fleet(db)
    await _leg(db, vessel, ports, code="1AFRBR6", etd=T0, summary={"co2_t": Decimal("1")})

    resp = await mr.mrv_emissions_voyages(FakeRequest(), vessel_id=999, db=db, user=staff_user)
    assert resp.context["selected_vessel_id"] is None
    assert len(resp.context["rows"]) == 1


async def test_screens_require_mrv_c(db):
    checker = mr.require_permission("mrv", "C")
    from types import SimpleNamespace

    armement = SimpleNamespace(id=3, role="armement", username="arm", full_name="Arm")
    assert await checker(FakeRequest(), user=armement, db=db) is armement


async def test_no_summary_row_is_created_by_reading(db, staff_user):
    """Les vues sont en lecture seule : consulter ne matérialise rien."""
    vessel, _other, ports = await _fleet(db)
    await _leg(db, vessel, ports, code="1AFRBR6", etd=T0, summary=None)

    await mr.mrv_emissions_voyages(FakeRequest(), db=db, user=staff_user)
    await mr.mrv_emissions_port(FakeRequest(), db=db, user=staff_user)

    assert (await db.execute(select(VoyageEmissionSummary))).scalars().all() == []
