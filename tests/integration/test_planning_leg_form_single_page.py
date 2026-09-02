"""PLN-08 — page unique « Créer un leg » (maquette validée le 2026-09-02).

Navire par boutons (radios), Départ / Arrivée côte à côte (ports habituels
BRSSO / FRFEC en repli, filtres Zone / Pays / Port, recherche libre), dates à
la journée pré-remplies depuis la séquence, escale saisie en JOURS (stockée en
heures), réservation = une case. Plus de wizard.
"""

from __future__ import annotations

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
