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


async def test_mrv_scope_is_the_default_and_anchoring_is_opt_in(db, staff_user):
    """🔴 L'invariant central : le périmètre MRV ne grossit jamais tout seul.

    Le mouillage est hors périmètre MRV (constat du 2026-09-04) : il ne peut
    entrer dans un total que sur demande explicite, et l'écran l'étiquette.
    """
    vessel, _other, ports = await _fleet(db)
    await _leg(
        db,
        vessel,
        ports,
        code="1AFRBR6",
        etd=T0,
        summary={
            "conso_hors_mouillage_t": Decimal("4.000"),
            "conso_mouillage_t": Decimal("1.000"),
            "co2_t": Decimal("12.800"),
            "co2_mouillage_t": Decimal("3.200"),
        },
    )

    default = await mr.mrv_emissions_voyages(FakeRequest(), db=db, user=staff_user)
    assert default.context["include_anchoring"] is False

    opted_in = await mr.mrv_emissions_voyages(
        FakeRequest(), include_anchoring=True, db=db, user=staff_user
    )
    assert opted_in.context["include_anchoring"] is True

    # Le chiffre MRV est IDENTIQUE dans les deux cas — seul le total élargi
    # apparaît. C'est ce qui empêche un chiffre réglementaire de dériver.
    assert default.context["rows"][0].co2_t == Decimal("12.800")
    assert opted_in.context["rows"][0].co2_t == Decimal("12.800")
    assert opted_in.context["rows"][0].co2_with_anchoring_t == Decimal("16.000")


async def test_anchoring_never_offered_on_the_port_screen(db, staff_user):
    """L'escale est au port, le mouillage est en mer : le sélecteur n'a aucun
    sens sur l'écran d'escale, et une demande explicite y est ignorée."""
    vessel, _other, ports = await _fleet(db)
    await _leg(
        db,
        vessel,
        ports,
        code="1AFRBR6",
        etd=T0,
        summary={"conso_escale_t": Decimal("1.2"), "co2_escale_t": Decimal("3.8")},
    )

    resp = await mr.mrv_emissions_port(FakeRequest(), db=db, user=staff_user)
    assert resp.context["include_anchoring"] is False


async def test_extended_total_needs_both_terms_known(db):
    """🔴 Un mouillage à ``None`` n'est PAS un mouillage nul.

    La première version le sommait comme zéro et affichait un total en gras
    « trajet + mouillage » alors que la cellule mouillage de la même ligne
    montrait un tiret. La distinction est nette dans le grand livre : source
    ``events`` → somme d'intervalles, donc ``0`` si le navire n'a pas mouillé ;
    source ``legacy_noon`` (et archives TOWT) → ``None``, mouillage **inconnu**
    faute de granularité.

    Zéro reste additionné — c'est une mesure. ``None`` interdit le total.
    """
    vessel, _other, ports = await _fleet(db)
    await _leg(
        db,
        vessel,
        ports,
        code="SANS-TRAJET",
        etd=T0,
        summary={"co2_mouillage_t": Decimal("3.200")},  # pas de co2_t
    )
    await _leg(
        db,
        vessel,
        ports,
        code="MOUILLAGE-INCONNU",
        etd=T0 + timedelta(days=20),
        summary={"co2_t": Decimal("10.000")},  # mouillage None = inconnu
    )
    await _leg(
        db,
        vessel,
        ports,
        code="SANS-MOUILLAGE",
        etd=T0 + timedelta(days=40),
        summary={"co2_t": Decimal("10.000"), "co2_mouillage_t": Decimal("0")},
    )

    rows = {r.leg.leg_code: r for r in await emv.voyage_emissions(db, now=T0 + timedelta(days=60))}
    assert rows["SANS-TRAJET"].co2_with_anchoring_t is None
    # Inconnu ⇒ pas de total, plutôt qu'un total qui vaudrait le seul trajet.
    assert rows["MOUILLAGE-INCONNU"].co2_with_anchoring_t is None
    # Mesuré à zéro ⇒ total légitime.
    assert rows["SANS-MOUILLAGE"].co2_with_anchoring_t == Decimal("10.000")


async def test_the_voyage_view_ignores_legs_that_have_not_departed(db):
    """🔴 Même famine que la vue escale, corrigée d'un seul côté au départ.

    Sans borne temporelle, une séquence planifiée à l'avance remplissait les
    40 places de voyages FUTURS — « non calculé » partout, et tous les voyages
    porteurs de vraies émissions repoussés hors de la page. Une restitution ne
    regarde que le passé.
    """
    vessel, _other, ports = await _fleet(db)
    for i in range(45):
        await _leg(db, vessel, ports, code=f"FUT{i:03d}", etd=T0 + timedelta(days=100 + i))
    await _leg(db, vessel, ports, code="PARTI", etd=T0, summary={"co2_t": Decimal("9")})

    rows = await emv.voyage_emissions(db, now=T0 + timedelta(days=1))
    assert [r.leg.leg_code for r in rows] == ["PARTI"]


async def test_a_leg_that_sailed_early_is_not_hidden_until_its_planned_etd(db):
    """🔴 La borne se mesure sur le départ EFFECTIF, pas sur l'ETD planifiée.

    ``declare_departure`` ne réécrit pas l'ETD : un voyage parti le 02/09 avec
    une ETD au 01/10 aurait été exclu des deux écrans jusqu'en octobre, malgré
    des émissions réelles. C'est la convention ``planning.effective_etd`` du
    projet — tout calcul « où en est le voyage » lui passe par là.
    """
    vessel, _other, ports = await _fleet(db)
    leg = await _leg(
        db,
        vessel,
        ports,
        code="PARTI-TOT",
        etd=T0 + timedelta(days=30),  # ETD planifiée LOIN dans le futur
        summary={"co2_t": Decimal("7"), "conso_escale_t": Decimal("1.1")},
    )
    leg.atd = T0 - timedelta(days=2)  # mais il est parti il y a deux jours
    await db.flush()

    now = T0
    assert [r.leg.leg_code for r in await emv.voyage_emissions(db, now=now)] == ["PARTI-TOT"]
    assert [r.leg.leg_code for r in await emv.port_emissions(db, now=now)] == ["PARTI-TOT"]


async def test_the_cap_does_not_hide_arrived_legs_behind_future_ones(db):
    """🔴 Le plafond de 40 était appliqué AVANT le filtre d'escale.

    Les legs sont triés par ETD décroissant : une séquence planifiée à l'avance
    remplit les 40 places avec des voyages futurs, sans escale. L'écran
    `/mrv/emissions/port` se rendait alors vide alors que des escales
    existaient.
    """
    vessel, _other, ports = await _fleet(db)
    # 45 voyages FUTURS sans escale — de quoi saturer le plafond.
    for i in range(45):
        await _leg(
            db,
            vessel,
            ports,
            code=f"F{i:03d}",
            etd=T0 + timedelta(days=100 + i),
            summary={"co2_t": Decimal("1")},
        )
    # Un voyage ancien, arrivé, avec une escale close.
    await _leg(
        db,
        vessel,
        ports,
        code="ARRIVE",
        etd=T0,
        summary={"conso_escale_t": Decimal("1.2"), "co2_escale_t": Decimal("3.8")},
    )

    rows = await emv.port_emissions(db, now=T0 + timedelta(days=1))
    assert [r.leg.leg_code for r in rows] == ["ARRIVE"]


async def test_screens_render_empty_without_crashing(db, staff_user):
    for coro in (mr.mrv_emissions_voyages, mr.mrv_emissions_port):
        resp = await coro(FakeRequest(), db=db, user=staff_user)
        assert resp.status_code == 200
        assert resp.context["rows"] == []


async def test_the_vessel_filter_keeps_the_selected_perimeter(db, staff_user):
    """🔴 Sélectionner un navire remettait le sélecteur sur « périmètre MRV ».

    Changer de navire ne doit pas changer le SENS des chiffres affichés sans le
    dire : les liens du filtre reportent donc le périmètre choisi.
    """
    vessel, _other, ports = await _fleet(db)
    await _leg(
        db,
        vessel,
        ports,
        code="1AFRBR6",
        etd=T0,
        summary={"co2_t": Decimal("10"), "co2_mouillage_t": Decimal("2")},
    )

    resp = await mr.mrv_emissions_voyages(
        FakeRequest(), vessel_id=vessel.id, include_anchoring=True, db=db, user=staff_user
    )
    assert resp.context["include_anchoring"] is True
    assert resp.context["selected_vessel_id"] == vessel.id


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


async def test_the_cap_is_announced_never_silent(db, staff_user):
    """🔴 Le plafond est DIT, pas subi.

    ``_LIMIT`` tronquait en silence : montrer les 40 plus récents sans le dire
    laisse lire le tableau comme l'ensemble complet — d'autant plus trompeur
    avec les archives TOWT, où l'historique dépasse largement 40 voyages.
    Même règle que « 10 sur 23 » sur la carte de navigation.
    """
    vessel, _other, ports = await _fleet(db)
    for i in range(emv._LIMIT + 3):
        await _leg(
            db,
            vessel,
            ports,
            code=f"CAP{i:03d}",
            etd=T0 - timedelta(days=i),
            summary={"co2_t": Decimal("1")},
        )

    resp = await mr.mrv_emissions_voyages(FakeRequest(), db=db, user=staff_user)

    # Le listing reste plafonné…
    assert len(resp.context["rows"]) == emv._LIMIT
    # …mais l'écran sait combien de voyages existent réellement.
    assert resp.context["total"] == emv._LIMIT + 3
    assert resp.context["limit"] == emv._LIMIT


async def test_the_total_counts_exactly_what_the_listing_would_show(db, staff_user):
    """Le comptage et le listing partagent leurs critères (``_selection``).

    Un total calculé à part dériverait du listing : il annoncerait « 40 sur 12 »
    (total plus large que la sélection) ou masquerait une troncature réelle.
    Deux critères sont vérifiés ici : la borne du départ effectif exclut les
    voyages futurs, et le filtre d'escale ne retient que les escales closes.
    """
    vessel, _other, ports = await _fleet(db)
    # Arrivé, escale close → compté par les deux écrans.
    await _leg(
        db,
        vessel,
        ports,
        code="AVEC-ESC",
        etd=T0,
        summary={"co2_t": Decimal("1"), "conso_escale_t": Decimal("2")},
    )
    # Arrivé, pas d'escale close → trajet seulement.
    await _leg(db, vessel, ports, code="SANS-ESC", etd=T0, summary={"co2_t": Decimal("1")})
    # Voyage FUTUR → ni l'un ni l'autre.
    await _leg(
        db,
        vessel,
        ports,
        code="FUTUR",
        etd=T0 + timedelta(days=30),
        summary={"co2_t": Decimal("1"), "conso_escale_t": Decimal("2")},
    )

    now = T0 + timedelta(days=1)
    voyages = await emv.voyage_emissions(db, now=now)
    port = await emv.port_emissions(db, now=now)

    assert await emv.total_count(db, only_with_escale=False, now=now) == len(voyages) == 2
    assert await emv.total_count(db, only_with_escale=True, now=now) == len(port) == 1
