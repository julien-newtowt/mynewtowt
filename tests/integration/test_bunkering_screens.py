"""Écrans soutage (Bunker Report / BDN) — MRV lot 6.

Patron ``tests/integration/test_mrv_parametres.py`` (coroutines de route
appelées directement, hors ASGI, avec le ``db``/``staff_user``/``FakeRequest``
de ``tests/integration/conftest.py``). Couvre : gate de permission bord
(``captain:M``) et siège (``mrv:C``/``mrv:M``), écran liste + détail + édition
+ validation Master (bord), écran liste + détail + correction (siège),
traçabilité (``activity_log``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.activity_log import ActivityLog
from app.models.bunker import BunkerOperation
from app.models.leg import Leg
from app.models.port import Port
from app.models.user import User
from app.models.vessel import Vessel
from app.models.vessel_env import VesselTank
from app.permissions import require_permission
from tests.integration.conftest import FakeRequest


async def _captain_user(db, assigned_vessel_id: int | None = None, user_id: int = 10) -> User:
    """Utilisateur bord **persisté** — ``author_user_id``/``validated_master_by``
    sont de vraies FK vers ``users.id`` (contrainte appliquée sous SQLite,
    ``PRAGMA foreign_keys=ON`` du fixture ``db``) : un ``SimpleNamespace`` non
    persisté y échouerait silencieusement en ``IntegrityError``.

    Rôle ``manager_maritime`` (captain:CMS dans la matrice par défaut) — le
    rôle ``marins`` n'a que ``captain:C`` (lecture) : la saisie/validation
    des soutages (niveau M) revient au commandant (``manager_maritime``) ou
    aux rôles opérationnels sédentaires (``operation``/``technique``), pas
    à l'équipage seul.
    """
    u = User(
        id=user_id,
        username=f"cdt{user_id}",
        email=f"cdt{user_id}@example.test",
        hashed_password="x",
        full_name="Cdt Test",
        role="manager_maritime",
        assigned_vessel_id=assigned_vessel_id,
    )
    db.add(u)
    await db.flush()
    return u


def _mrv_editor_user():
    """Utilisateur siège — jamais écrit en FK par ``apply_review_correction``
    (seul l'auteur bord et le validateur Master le sont) : pas besoin de
    persister de ligne ``users`` réelle ici."""
    return SimpleNamespace(id=20, full_name="Ops MRV", username="opsmrv", role="operation")


async def _make_vessel_with_tanks(db, code: str = "ANE") -> Vessel:
    v = Vessel(code=code, name="Anemos")
    db.add(v)
    await db.flush()
    for tc in ("14", "15", "16", "17", "other"):
        db.add(VesselTank(vessel_id=v.id, tank_code=tc))
    await db.flush()
    return v


async def _make_leg(db, vessel: Vessel, etd: datetime, leg_code: str = "1AFRBR6") -> Leg:
    pol = Port(locode="FRFEC", name="Fécamp", country="FR")
    pod = Port(locode="BRSSZ", name="Santos", country="BR")
    db.add_all([pol, pod])
    await db.flush()
    leg = Leg(
        leg_code=leg_code,
        vessel_id=vessel.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd=etd,
        eta=etd + timedelta(days=20),
        etd_ref=etd,
        eta_ref=etd + timedelta(days=20),
    )
    db.add(leg)
    await db.flush()
    return leg


# ─────────────────────────── routes enregistrées ───────────────────────────


def test_bunkering_routes_registered():
    from app.routers import mrv_router, onboard_router

    onboard_paths = {r.path for r in onboard_router.router.routes}
    assert "/onboard/bunkering" in onboard_paths
    assert "/onboard/bunkering/new" in onboard_paths
    assert "/onboard/bunkering/{bunker_id}" in onboard_paths
    assert "/onboard/bunkering/{bunker_id}/edit" in onboard_paths
    assert "/onboard/bunkering/{bunker_id}/validate" in onboard_paths

    mrv_paths = {r.path for r in mrv_router.router.routes}
    assert "/mrv/bunkering" in mrv_paths
    assert "/mrv/bunkering/{bunker_id}" in mrv_paths
    assert "/mrv/bunkering/{bunker_id}/edit" in mrv_paths


# ══════════════════════════════════════ Gate de permission — bord (captain:M)


@pytest.mark.asyncio
async def test_onboard_bunkering_requires_captain_m(db):
    """Un rôle sans ``captain:M`` (ex. commercial, C seulement) reçoit 403."""
    checker = require_permission("captain", "M")
    commercial = SimpleNamespace(id=99, full_name="Commercial", username="com", role="commercial")
    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=commercial, db=db)
    assert exc.value.status_code == 403

    captain = SimpleNamespace(id=11, full_name="Cdt", username="cdt2", role="manager_maritime")
    assert await checker(FakeRequest(), user=captain, db=db) is captain


# ══════════════════════════════════════ Gate de permission — siège (mrv:C/M)


@pytest.mark.asyncio
async def test_mrv_bunkering_requires_mrv_c(db):
    """Un rôle sans accès ``mrv`` du tout (ex. rh) reçoit 403 en consultation."""
    checker = require_permission("mrv", "C")
    rh_user = SimpleNamespace(id=98, full_name="RH", username="rh1", role="rh")
    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=rh_user, db=db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_mrv_bunkering_edit_requires_mrv_m(db):
    """``armement`` n'a que ``mrv:C`` — la correction (M) doit refuser 403."""
    checker = require_permission("mrv", "M")
    armement_user = SimpleNamespace(id=97, full_name="Armement", username="arm", role="armement")
    with pytest.raises(HTTPException) as exc:
        await checker(FakeRequest(), user=armement_user, db=db)
    assert exc.value.status_code == 403


# ═══════════════════════════════════════════════════ Écran bord — liste/new


@pytest.mark.asyncio
async def test_onboard_bunkering_index_renders_empty(db, staff_user):
    from app.routers.onboard_router import onboard_bunkering_index

    v = await _make_vessel_with_tanks(db)
    resp = await onboard_bunkering_index(
        FakeRequest(), vessel_id=v.id, status=None, db=db, user=staff_user
    )
    assert resp.status_code == 200
    assert resp.template.name == "staff/onboard/bunkering_index.html"
    assert resp.context["bunkers"] == []


@pytest.mark.asyncio
async def test_onboard_bunkering_new_form_renders_with_tanks(db, staff_user):
    from app.routers.onboard_router import onboard_bunkering_new_form

    v = await _make_vessel_with_tanks(db)
    resp = await onboard_bunkering_new_form(FakeRequest(), vessel_id=v.id, db=db, user=staff_user)
    assert resp.status_code == 200
    assert resp.template.name == "staff/onboard/bunkering_form.html"
    assert len(resp.context["tanks"]) == 5


@pytest.mark.asyncio
async def test_onboard_bunkering_new_form_invites_referential_init_when_no_tanks(db, staff_user):
    from app.routers.onboard_router import onboard_bunkering_new_form

    v = Vessel(code="ART", name="Artemis")
    db.add(v)
    await db.flush()
    resp = await onboard_bunkering_new_form(FakeRequest(), vessel_id=v.id, db=db, user=staff_user)
    assert resp.status_code == 200
    assert "Ce navire n" in resp.body.decode()  # message d'invitation référentiel


# ═══════════════════════════════════════════════ Écran bord — création + Master


@pytest.mark.asyncio
async def test_onboard_bunkering_create_then_validate_master_flow(db):
    from app.routers.onboard_router import (
        onboard_bunkering_create,
        onboard_bunkering_detail,
        onboard_bunkering_validate,
    )

    v = await _make_vessel_with_tanks(db)
    delivery = datetime(2026, 3, 10, 8, 0, tzinfo=UTC)
    leg = await _make_leg(db, v, etd=delivery + timedelta(days=3))
    tanks = list(
        (await db.execute(select(VesselTank).where(VesselTank.vessel_id == v.id))).scalars().all()
    )
    tank14 = next(t for t in tanks if t.tank_code == "14")

    user = await _captain_user(db, assigned_vessel_id=v.id)
    form = {
        "vessel_id": str(v.id),
        "bdn_number": "BDN-2026-001",
        "port_locode": "frfec",
        "delivery_datetime_utc": "2026-03-10T08:00",
        "fuel_type": "MDO",
        "mass_t": "16.9",
        "density_15c_t_m3": "0.845",
        "supplier_name": "Total Energies",
        f"volume_m3__{tank14.id}": "20",
        f"density_t_m3__{tank14.id}": "0.845",
    }
    resp = await onboard_bunkering_create(FakeRequest(form), db=db, user=user)
    assert resp.status_code == 303
    bunker_id = int(resp.headers["location"].rsplit("/", 1)[-1])

    bunker = await db.get(BunkerOperation, bunker_id)
    assert bunker is not None
    assert bunker.port_locode == "FRFEC"  # normalisé en majuscules
    assert bunker.status == "brouillon"
    assert bunker.leg_id == leg.id  # rattachement voyage auto (dans la fenêtre)
    assert bunker.author_user_id == user.id

    # Trace de la création.
    logs = list(
        (await db.execute(select(ActivityLog).where(ActivityLog.action == "bunker_create")))
        .scalars()
        .all()
    )
    assert logs and logs[0].entity_id == bunker.id

    # Détail — contrôles structurels calculés à l'affichage.
    detail = await onboard_bunkering_detail(bunker_id, FakeRequest(), db=db, user=user)
    assert detail.status_code == 200
    assert detail.context["checks"].mass.status == "ok"
    assert detail.context["can_edit"] is True

    # Validation Master.
    validate_resp = await onboard_bunkering_validate(bunker_id, FakeRequest(), db=db, user=user)
    assert validate_resp.status_code == 303
    await db.refresh(bunker)
    assert bunker.status == "valide_master"
    assert bunker.validated_master_by == user.id
    assert bunker.validated_master_at is not None

    validate_logs = list(
        (
            await db.execute(
                select(ActivityLog).where(ActivityLog.action == "bunker_validate_master")
            )
        )
        .scalars()
        .all()
    )
    assert validate_logs


@pytest.mark.asyncio
async def test_onboard_bunkering_create_duplicate_bdn_rejected(db):
    from app.routers.onboard_router import onboard_bunkering_create

    v = await _make_vessel_with_tanks(db)
    user = await _captain_user(db, assigned_vessel_id=v.id)
    base_form = {
        "vessel_id": str(v.id),
        "bdn_number": "BDN-DUP",
        "port_locode": "FRFEC",
        "delivery_datetime_utc": "2026-03-10T08:00",
        "mass_t": "10",
        "density_15c_t_m3": "0.845",
    }
    resp = await onboard_bunkering_create(FakeRequest(dict(base_form)), db=db, user=user)
    assert resp.status_code == 303

    with pytest.raises(HTTPException) as exc:
        await onboard_bunkering_create(FakeRequest(dict(base_form)), db=db, user=user)
    assert exc.value.status_code == 400


# ═══════════════════════════════════════════════════ Écran bord — édition


@pytest.mark.asyncio
async def test_onboard_bunkering_edit_rejects_non_author(db):
    from app.routers.onboard_router import onboard_bunkering_create, onboard_bunkering_edit_post

    v = await _make_vessel_with_tanks(db)
    author = await _captain_user(db, assigned_vessel_id=v.id)
    create_resp = await onboard_bunkering_create(
        FakeRequest(
            {
                "vessel_id": str(v.id),
                "bdn_number": "BDN-EDIT-1",
                "port_locode": "FRFEC",
                "delivery_datetime_utc": "2026-03-10T08:00",
                "mass_t": "10",
                "density_15c_t_m3": "0.845",
            }
        ),
        db=db,
        user=author,
    )
    bunker_id = int(create_resp.headers["location"].rsplit("/", 1)[-1])

    other = await _captain_user(db, assigned_vessel_id=v.id, user_id=999)
    with pytest.raises(HTTPException) as exc:
        await onboard_bunkering_edit_post(
            bunker_id, FakeRequest({"supplier_name": "Someone else"}), db=db, user=other
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_onboard_bunkering_edit_allows_author(db):
    from app.routers.onboard_router import onboard_bunkering_create, onboard_bunkering_edit_post

    v = await _make_vessel_with_tanks(db)
    author = await _captain_user(db, assigned_vessel_id=v.id)
    create_resp = await onboard_bunkering_create(
        FakeRequest(
            {
                "vessel_id": str(v.id),
                "bdn_number": "BDN-EDIT-2",
                "port_locode": "FRFEC",
                "delivery_datetime_utc": "2026-03-10T08:00",
                "mass_t": "10",
                "density_15c_t_m3": "0.845",
            }
        ),
        db=db,
        user=author,
    )
    bunker_id = int(create_resp.headers["location"].rsplit("/", 1)[-1])

    resp = await onboard_bunkering_edit_post(
        bunker_id, FakeRequest({"supplier_name": "Total Energies"}), db=db, user=author
    )
    assert resp.status_code == 303
    bunker = await db.get(BunkerOperation, bunker_id)
    assert bunker.supplier_name == "Total Energies"


# ══════════════════════════════════════════════════════ Écran siège — liste


@pytest.mark.asyncio
async def test_mrv_bunkering_index_filters_by_ecart(db):
    from app.routers.mrv_router import mrv_bunkering_index
    from app.routers.onboard_router import onboard_bunkering_create

    v = await _make_vessel_with_tanks(db)
    tanks = list(
        (await db.execute(select(VesselTank).where(VesselTank.vessel_id == v.id))).scalars().all()
    )
    tank14 = next(t for t in tanks if t.tank_code == "14")
    author = await _captain_user(db, assigned_vessel_id=v.id)

    # BDN conforme (allocation = masse déclarée).
    await onboard_bunkering_create(
        FakeRequest(
            {
                "vessel_id": str(v.id),
                "bdn_number": "BDN-OK",
                "port_locode": "FRFEC",
                "delivery_datetime_utc": "2026-03-10T08:00",
                "mass_t": "20",
                "density_15c_t_m3": "0.845",
                f"volume_m3__{tank14.id}": "20",
                f"density_t_m3__{tank14.id}": "1.0",
            }
        ),
        db=db,
        user=author,
    )
    # BDN avec écart majeur (aucune allocation -> masse allouée = 0).
    await onboard_bunkering_create(
        FakeRequest(
            {
                "vessel_id": str(v.id),
                "bdn_number": "BDN-ECART",
                "port_locode": "FRFEC",
                "delivery_datetime_utc": "2026-03-11T08:00",
                "mass_t": "30",
                "density_15c_t_m3": "0.845",
            }
        ),
        db=db,
        user=author,
    )

    resp = await mrv_bunkering_index(
        FakeRequest(),
        vessel_id=None,
        status=None,
        date_from=None,
        date_to=None,
        ecart=None,
        db=db,
        user=_mrv_editor_user(),
    )
    assert resp.status_code == 200
    assert len(resp.context["rows"]) == 2

    resp_majeur = await mrv_bunkering_index(
        FakeRequest(),
        vessel_id=None,
        status=None,
        date_from=None,
        date_to=None,
        ecart="ecart_majeur",
        db=db,
        user=_mrv_editor_user(),
    )
    bdns = {row["bunker"].bdn_number for row in resp_majeur.context["rows"]}
    assert bdns == {"BDN-ECART"}


@pytest.mark.asyncio
async def test_mrv_bunkering_detail_and_correction_traced(db):
    from app.routers.mrv_router import mrv_bunkering_detail, mrv_bunkering_edit
    from app.routers.onboard_router import onboard_bunkering_create, onboard_bunkering_validate

    v = await _make_vessel_with_tanks(db)
    author = await _captain_user(db, assigned_vessel_id=v.id)
    create_resp = await onboard_bunkering_create(
        FakeRequest(
            {
                "vessel_id": str(v.id),
                "bdn_number": "BDN-SIEGE-1",
                "port_locode": "FRFEC",
                "delivery_datetime_utc": "2026-03-10T08:00",
                "mass_t": "10",
                "density_15c_t_m3": "0.845",
            }
        ),
        db=db,
        user=author,
    )
    bunker_id = int(create_resp.headers["location"].rsplit("/", 1)[-1])
    await onboard_bunkering_validate(bunker_id, FakeRequest(), db=db, user=author)

    editor = _mrv_editor_user()
    detail = await mrv_bunkering_detail(bunker_id, FakeRequest(), db=db, user=editor)
    assert detail.status_code == 200
    assert detail.context["bunker"].status == "valide_master"

    # Correction siège possible même après validation Master.
    correction = await mrv_bunkering_edit(
        bunker_id, FakeRequest({"supplier_name": "Corrigé par le siège"}), db=db, user=editor
    )
    assert correction.status_code == 303
    bunker = await db.get(BunkerOperation, bunker_id)
    assert bunker.supplier_name == "Corrigé par le siège"
    assert bunker.status == "valide_master"  # inchangé

    logs = list(
        (
            await db.execute(
                select(ActivityLog).where(ActivityLog.action == "bunker_review_correction")
            )
        )
        .scalars()
        .all()
    )
    assert logs and logs[0].module == "mrv" and logs[0].entity_id == bunker_id
