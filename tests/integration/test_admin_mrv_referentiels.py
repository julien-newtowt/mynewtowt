"""Écrans admin — référentiel navire & facteurs d'émission (MRV lot 1).

Patron : ``tests/integration/test_admin_emission_factors.py`` (ADM-06, écran
CO₂ existant — feature différente mais même style de test : coroutines de
route appelées directement, hors ASGI, avec le ``db``/``staff_user``/
``FakeRequest`` de ``tests/integration/conftest.py``). On ne réutilise PAS ce
nom de fichier : il est déjà pris par une suite existante sans rapport avec
les référentiels multi-GES de ce lot (écart signalé au rapport).

``FakeRequest`` (conftest) suffit pour les routes POST (pas de rendu de
template, juste une ``RedirectResponse``) mais PAS pour les routes GET : le
context processor i18n (``app.templating._i18n_context_processor``) lit
``request.state`` / ``request.cookies``, et ``staff/_topbar.html`` lit
``request.url.path`` — des attributs absents de ``FakeRequest``. On étend
donc localement (sans toucher au conftest partagé) avec le strict minimum
pour permettre un rendu réel des 2 nouveaux écrans.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.integration.conftest import FakeRequest


class _PageRequest(FakeRequest):
    """``FakeRequest`` + attributs nécessaires au rendu d'une page staff complète."""

    def __init__(self, path: str = "/admin/flotte-env", form: dict | None = None):
        super().__init__(form)
        self.state = SimpleNamespace()
        self.cookies: dict[str, str] = {}
        self.url = SimpleNamespace(path=path)
        self.query_params: dict[str, str] = {}


# ════════════════════════════════════════════ Permissions — même garde que /admin/co2


@pytest.mark.asyncio
async def test_admin_module_permission_denies_non_admin_role(db):
    from app.permissions import require_permission

    checker = require_permission("admin", "C")
    marin = SimpleNamespace(id=42, username="marin1", full_name="Marin Un", role="marins")
    with pytest.raises(HTTPException) as exc_info:
        await checker(request=_PageRequest(), user=marin, db=db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_module_permission_allows_administrateur(db, staff_user):
    from app.permissions import require_permission

    checker = require_permission("admin", "C")
    # Ne doit pas lever — le rôle administrateur a CMS sur tous les modules.
    result = await checker(request=_PageRequest(), user=staff_user, db=db)
    assert result is staff_user


# ════════════════════════════════════════════ /admin/flotte-env — GET


@pytest.mark.asyncio
async def test_flotte_env_page_renders_with_no_vessels(db, staff_user):
    from app.routers.admin_router import flotte_env_page

    resp = await flotte_env_page(_PageRequest(), db=db, user=staff_user)
    assert resp.status_code == 200
    assert "Aucun navire" in resp.body.decode()


@pytest.mark.asyncio
async def test_flotte_env_page_renders_vessel_with_referentials(db, staff_user):
    from app.models.vessel import Vessel
    from app.routers.admin_router import flotte_env_page
    from app.services import referential_env

    v = Vessel(code="ANE", name="Anemos")
    db.add(v)
    await db.flush()
    await referential_env.ensure_vessel_env_defaults(db, v)

    resp = await flotte_env_page(_PageRequest(), db=db, user=staff_user)
    body = resp.status_code, resp.body.decode()
    assert body[0] == 200
    html = body[1]
    assert "ANE" in html
    assert "Anemos" in html
    # 5 cuves + 6 moteurs listés (codes/rôles visibles dans les tableaux).
    assert "PME" in html
    assert "FWD_GEN" in html


# ════════════════════════════════════════════ /admin/flotte-env — POST init/update


@pytest.mark.asyncio
async def test_flotte_env_init_is_idempotent_via_route(db, staff_user):
    from app.models.vessel import Vessel
    from app.routers.admin_router import flotte_env_init
    from app.services import referential_env

    v = Vessel(code="ART", name="Artemis")
    db.add(v)
    await db.flush()

    resp1 = await flotte_env_init(v.id, FakeRequest(), db=db, user=staff_user)
    assert resp1.status_code == 303
    tanks_after_first = await referential_env.get_vessel_tanks(db, v.id)
    engines_after_first = await referential_env.get_vessel_engines(db, v.id)
    assert len(tanks_after_first) == 5
    assert len(engines_after_first) == 6

    # Deuxième appel : rejouable sans risque, aucun doublon.
    resp2 = await flotte_env_init(v.id, FakeRequest(), db=db, user=staff_user)
    assert resp2.status_code == 303
    tanks_after_second = await referential_env.get_vessel_tanks(db, v.id)
    engines_after_second = await referential_env.get_vessel_engines(db, v.id)
    assert len(tanks_after_second) == 5
    assert len(engines_after_second) == 6


@pytest.mark.asyncio
async def test_flotte_env_init_unknown_vessel_404(db, staff_user):
    from fastapi import HTTPException

    from app.routers.admin_router import flotte_env_init

    with pytest.raises(HTTPException) as exc_info:
        await flotte_env_init(999, FakeRequest(), db=db, user=staff_user)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_flotte_env_update_persists_the_4_fields(db, staff_user):
    """G17 — deadweight_t (symétrique de lightweight_t) rejoint les champs
    référentiel environnemental édités par cet écran."""
    from app.models.vessel import Vessel
    from app.routers.admin_router import flotte_env_update

    v = Vessel(code="ATL", name="Atlantis")
    db.add(v)
    await db.flush()

    resp = await flotte_env_update(
        v.id,
        FakeRequest(),
        lightweight_t="612.500",
        deadweight_t="1200.000",
        default_fuel_type="mdo",
        water_density_default_t_m3="1.0250",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    await db.refresh(v)
    assert v.lightweight_t == Decimal("612.500")
    assert v.deadweight_t == Decimal("1200.000")
    assert v.default_fuel_type == "MDO"  # normalisé en majuscules
    assert v.water_density_default_t_m3 == Decimal("1.0250")


@pytest.mark.asyncio
async def test_flotte_env_update_rejects_invalid_number(db, staff_user):
    from app.models.vessel import Vessel
    from app.routers.admin_router import flotte_env_update

    v = Vessel(code="POS", name="Poseidon")
    db.add(v)
    await db.flush()

    with pytest.raises(HTTPException) as exc_info:
        await flotte_env_update(
            v.id,
            FakeRequest(),
            lightweight_t="pas un nombre",
            default_fuel_type="MDO",
            water_density_default_t_m3=None,
            db=db,
            user=staff_user,
        )
    assert exc_info.value.status_code == 400


# ════════════════════════════════════════════ /admin/emission-factors — GET


@pytest.mark.asyncio
async def test_emission_factors_page_renders_seeded_row(db, staff_user):
    from datetime import date

    from app.models.emission_factor import EmissionFactor
    from app.routers.admin_router import emission_factors_page

    db.add(
        EmissionFactor(
            fuel_type="MDO",
            ef_co2_kg_per_kg=Decimal("3.206"),
            ef_ch4_kg_per_kg=Decimal("0.00005"),
            ef_n2o_kg_per_kg=Decimal("0.00018"),
            wtt_gco2eq_per_mj=Decimal("17.7"),
            source_reference="MEPC.391(81) + CFOTE_09 Rev02",
            valid_from=date(2025, 1, 1),
            is_current=True,
        )
    )
    await db.flush()

    resp = await emission_factors_page(
        _PageRequest(path="/admin/emission-factors"), db=db, user=staff_user
    )
    assert resp.status_code == 200
    html = resp.body.decode()
    assert "MDO" in html
    assert "3.206" in html


@pytest.mark.asyncio
async def test_emission_factors_page_renders_fallback_notice_when_empty(db, staff_user):
    from app.routers.admin_router import emission_factors_page

    resp = await emission_factors_page(
        _PageRequest(path="/admin/emission-factors"), db=db, user=staff_user
    )
    assert resp.status_code == 200
    assert "constantes codées" in resp.body.decode()


# ════════════════════════════════════════════ /admin/emission-factors — POST create


@pytest.mark.asyncio
async def test_emission_factors_create_new_version_flips_old_current(db, staff_user):
    from datetime import date

    from sqlalchemy import select

    from app.models.emission_factor import EmissionFactor
    from app.routers.admin_router import emission_factors_create
    from app.services import referential_env

    old = EmissionFactor(
        fuel_type="MDO",
        ef_co2_kg_per_kg=Decimal("3.206"),
        ef_ch4_kg_per_kg=Decimal("0.00005"),
        ef_n2o_kg_per_kg=Decimal("0.00018"),
        wtt_gco2eq_per_mj=Decimal("17.7"),
        valid_from=date(2025, 1, 1),
        is_current=True,
    )
    db.add(old)
    await db.flush()

    resp = await emission_factors_create(
        FakeRequest(),
        fuel_type="mdo",
        ef_co2_kg_per_kg="3.250",
        ef_ch4_kg_per_kg="0.00006",
        ef_n2o_kg_per_kg="0.00019",
        wtt_gco2eq_per_mj="18.0",
        source_reference="Révision 2026",
        valid_from="2026-01-01",
        valid_to="",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303

    rows = (await db.execute(select(EmissionFactor).order_by(EmissionFactor.id))).scalars().all()
    assert len(rows) == 2
    await db.refresh(old)
    assert old.is_current is False
    new_row = rows[-1]
    assert new_row.is_current is True
    assert new_row.fuel_type == "MDO"
    assert new_row.ef_co2_kg_per_kg == Decimal("3.250")
    assert new_row.created_by_id == staff_user.id

    # La délégation co2 doit refléter la nouvelle version sans redéploiement
    # (cache invalidé par la route) — preuve que emission-factors est la
    # source de vérité effective de get_do_co2_factor.
    from app.services.co2 import get_do_co2_factor

    referential_env.invalidate_emission_factor_cache()
    factor = await get_do_co2_factor(db)
    assert factor == Decimal("3.250")
    referential_env.invalidate_emission_factor_cache()


@pytest.mark.asyncio
async def test_emission_factors_create_rejects_non_numeric_factor(db, staff_user):
    from app.routers.admin_router import emission_factors_create

    with pytest.raises(HTTPException) as exc_info:
        await emission_factors_create(
            FakeRequest(),
            fuel_type="MDO",
            ef_co2_kg_per_kg="abc",
            ef_ch4_kg_per_kg="0.00005",
            ef_n2o_kg_per_kg="0.00018",
            wtt_gco2eq_per_mj="17.7",
            source_reference="",
            valid_from="2026-01-01",
            valid_to="",
            db=db,
            user=staff_user,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_emission_factors_create_rejects_valid_to_before_valid_from(db, staff_user):
    from app.routers.admin_router import emission_factors_create

    with pytest.raises(HTTPException) as exc_info:
        await emission_factors_create(
            FakeRequest(),
            fuel_type="MDO",
            ef_co2_kg_per_kg="3.206",
            ef_ch4_kg_per_kg="0.00005",
            ef_n2o_kg_per_kg="0.00018",
            wtt_gco2eq_per_mj="17.7",
            source_reference="",
            valid_from="2026-06-01",
            valid_to="2026-01-01",
            db=db,
            user=staff_user,
        )
    assert exc_info.value.status_code == 400
