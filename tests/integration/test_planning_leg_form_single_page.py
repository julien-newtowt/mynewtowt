"""PLN-08 — page unique « Créer un leg » (maquette validée le 2026-09-02).

Navire par boutons (radios), Départ / Arrivée côte à côte (ports habituels
BRSSO / FRFEC en repli, filtres Zone / Pays / Port, recherche libre), dates à
la journée pré-remplies depuis la séquence, escale saisie en JOURS (stockée en
heures), réservation = une case. Plus de wizard.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from tests.integration.conftest import FakeRequest


def _src():
    from app.templating import templates

    return templates.env.loader.get_source(templates.env, "staff/planning/leg_form.html")[0]


def test_leg_form_is_single_page_with_vessel_buttons_and_two_columns():
    src = _src()
    assert "wizard" not in src and "leg-wizard.js" not in src
    assert 'type="radio" name="vessel_id"' in src  # un bouton par navire
    assert "port_column('pol'" in src and "port_column('pod'" in src  # Départ / Arrivée
    assert 'data-port-search="{{ prefix }}"' in src  # recherche libre par colonne
    assert 'name="port_stay_planned_days"' in src  # escale en jours
    assert 'name="is_bookable"' in src and "public_capacity_palettes" in src
    # Ports habituels en repli : São Sebastião et Fécamp, rien d'autre.
    assert 'data-locode="BRSSO"' in src and 'data-locode="FRFEC"' in src
    assert 'data-locode="USNYC"' not in src and 'data-locode="FRLEH"' not in src


def test_leg_form_lets_the_reference_leg_be_chosen():
    """Le sélecteur « Chaîner après » doit exister et être câblé au JS.

    Sans lui, l'opérateur subit le défaut (dernier leg par ETD) sans pouvoir
    le corriger — bug remonté le 2026-09-02.
    """
    src = _src()
    assert 'id="leg-chain-after"' in src
    assert 'id="leg-chain-picker"' in src
    js = (pathlib.Path("app/static/js/leg-form-suggest.js")).read_text()
    for ident in ("leg-chain-after", "leg-chain-picker", "chain_options"):
        assert ident in js, f"{ident} absent de leg-form-suggest.js"
    # Le récapitulatif doit chiffrer le code prévisionnel sur la référence
    # RETENUE, pas sur le défaut du navire : sinon le rang affiche « ? » dès
    # qu'on change de leg de référence.
    assert "activeSuggestion" in js
    cascade = (pathlib.Path("app/static/js/leg-cascade.js")).read_text()
    assert "activeSuggestion" in cascade


def test_port_picker_queries_the_server_not_the_whole_table():
    """Le formulaire ne doit plus rapatrier le référentiel de ports.

    Bug du 2026-09-02 : `limit=10000` tronquait silencieusement au-delà de
    10 000 ports (123 pays perdus, dont le Viêt Nam), et la carte de continents
    codée en dur ne couvrait que ~90 pays. Zones et pays viennent maintenant de
    l'API, la recherche interroge le serveur.
    """
    js = pathlib.Path("app/static/js/leg-cascade.js").read_text()
    body = js.split("*/", 1)[1]  # hors en-tête, qui cite l'ancien appel
    assert "limit=10000" not in body
    assert "allPorts" not in body
    assert "CONTINENT" not in body  # plus de carte de continents dans le navigateur
    assert "/api/v1/ports/countries" in body
    assert "/api/v1/ports/search?limit=" in body
    assert "/api/v1/ports/" in body


async def _setup(db):
    db.add(Vessel(id=1, code="1", name="Anemos"))
    db.add(Vessel(id=2, code="2", name="Artemis"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR", latitude=49.76, longitude=0.37))
    db.add(
        Port(
            id=2,
            locode="BRSSO",
            name="São Sebastião",
            country="BR",
            latitude=-23.8,
            longitude=-45.4,
        )
    )
    await db.flush()
    base = datetime(2026, 9, 1, tzinfo=UTC)
    db.add(
        Leg(
            id=1,
            leg_code="1CFRBR6",
            vessel_id=1,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=base,
            eta_ref=base + timedelta(days=31),
            etd=base,
            eta=base + timedelta(days=31),
            atd=base,
            port_stay_planned_hours=72,
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_suggestions_carry_sequence_state_per_vessel(db):
    from app.routers.planning_router import _new_leg_suggestions

    await _setup(db)
    s = await _new_leg_suggestions(db)
    anemos, artemis = s[1], s[2]
    assert artemis["no_legs"] is True and artemis["vessel_code"] == "2"
    assert anemos["no_legs"] is False
    assert anemos["from_leg_code"] == "1CFRBR6" and anemos["from_phase"] == "en_mer"
    assert anemos["from_pol_locode"] == "FRFEC" and anemos["from_pod_locode"] == "BRSSO"
    assert anemos["pol_id"] == 2  # POL du prochain leg = POD du dernier (continuité)
    assert anemos["port_stay_days"] == 3  # 72 h → 3 jours
    assert anemos["etd"] == "2026-10-05"  # ETA 02/10 + 3 j (pas de fermeture WE configurée)
    assert anemos["next_rank_letter"] == "B"  # 2ᵉ leg de 2026 pour ce navire
    assert anemos["year_digit"] == "6"


@pytest.mark.asyncio
async def test_suggestions_expose_every_chainable_leg(db):
    """Le défaut (dernier ETD) ne suffit pas : le leg de référence est choisi.

    Bug 2026-09-02 — un voyage saisi longtemps à l'avance capte le défaut et
    fait chaîner sur lui les legs de l'année en cours : « il a repris le leg A
    alors qu'on programme le D ». Le formulaire doit donc proposer les autres
    legs de la séquence, chacun avec SA suggestion (ETD, POL, escale, rang).
    """
    from app.routers.planning_router import _new_leg_suggestions

    await _setup(db)
    # Un voyage 2027 saisi à l'avance : c'est lui qui a l'ETD le plus tardif.
    far = datetime(2027, 1, 20, tzinfo=UTC)
    db.add(
        Leg(
            id=2,
            leg_code="1ABRFR7",
            vessel_id=1,
            departure_port_id=2,
            arrival_port_id=1,
            etd_ref=far,
            eta_ref=far + timedelta(days=21),
            etd=far,
            eta=far + timedelta(days=21),
            port_stay_planned_hours=48,
        )
    )
    await db.flush()

    anemos = (await _new_leg_suggestions(db))[1]
    options = anemos["chain_options"]
    # Défaut inchangé : on prolonge la ligne (ETD le plus tardif).
    assert anemos["from_leg_code"] == "1ABRFR7"
    assert [o["from_leg_code"] for o in options] == ["1ABRFR7", "1CFRBR6"]
    # Chaque option porte SA propre dérivation — c'est ce qui rend le choix utile.
    by_code = {o["from_leg_code"]: o for o in options}
    assert by_code["1ABRFR7"]["pol_id"] == 1 and by_code["1ABRFR7"]["etd"].startswith("2027-")
    assert by_code["1CFRBR6"]["pol_id"] == 2 and by_code["1CFRBR6"]["etd"] == "2026-10-05"
    assert by_code["1CFRBR6"]["port_stay_days"] == 3
    assert by_code["1CFRBR6"]["ref_leg_id"] == 1


@pytest.mark.asyncio
async def test_create_leg_accepts_stay_in_days(db, staff_user):
    from app.routers.planning_router import create_leg_action

    await _setup(db)
    resp = await create_leg_action(
        FakeRequest(
            {
                "vessel_id": "1",
                "departure_port_id": "2",
                "arrival_port_id": "1",
                "etd": "2026-10-05",
                "eta": "2026-11-04",
                "port_stay_planned_days": "3",
                "is_bookable": "on",
            }
        ),
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    from sqlalchemy import select

    leg = (
        await db.execute(select(Leg).where(Leg.departure_port_id == 2, Leg.arrival_port_id == 1))
    ).scalar_one()
    assert leg.port_stay_planned_hours == 72  # jours × 24
    assert leg.etd.replace(tzinfo=None) == datetime(2026, 10, 5)
    assert leg.is_bookable is True
    assert leg.leg_code == "1BBRFR6"  # rang B, BR → FR, 2026


def test_stay_hours_from_form_prefers_days_then_hours():
    from app.routers.planning_router import _stay_hours_from_form

    assert _stay_hours_from_form({"port_stay_planned_days": "2"}) == 48
    assert _stay_hours_from_form({"port_stay_planned_hours": "36"}) == 36
    assert (
        _stay_hours_from_form({"port_stay_planned_days": "1", "port_stay_planned_hours": "5"}) == 24
    )
    assert _stay_hours_from_form({}) is None


@pytest.mark.asyncio
async def test_new_and_edit_forms_render(db, staff_user):
    """Le template compile et rend en création (navire présélectionné) et en édition."""
    from app.routers.planning_router import edit_leg_form, new_leg_form

    await _setup(db)
    resp = await new_leg_form(FakeRequest(), vessel_id=1, db=db, user=staff_user)
    assert resp.status_code == 200
    body = resp.body.decode()
    assert "Nouveau leg" in body and "Anemos" in body and "Artemis" in body
    assert "Aucun leg planifié" in body  # Artemis, sans leg
    assert "1CFRBR6 FRFEC → BRSSO" in body  # état de séquence d'Anemos sur son bouton
    assert 'value="1" required' in body and "checked" in body  # navire présélectionné
    assert "Créer le leg" in body

    resp = await edit_leg_form(FakeRequest(), leg_id=1, db=db, user=staff_user)
    assert resp.status_code == 200
    body = resp.body.decode()
    assert "Éditer leg" in body and "1CFRBR6" in body
    assert 'name="port_stay_planned_days"' in body and 'value="3"' in body  # 72 h → 3 j
    assert "expected_updated_at" in body and 'name="cascade"' in body
    assert "FRFEC — Fécamp (FR)" in body and "BRSSO — São Sebastião (BR)" in body  # ports courants
