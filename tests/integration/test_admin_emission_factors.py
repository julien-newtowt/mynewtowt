"""ADM-06 — réexposition des facteurs NOx / SOx dans l'éditeur CO₂ admin.

Les facteurs d'émission NOx / SOx sont lus par ``services.emissions`` depuis
``co2_variables`` mais n'étaient pas éditables (absents de
``CO2_VARIABLE_DEFS``). On vérifie qu'ils sont désormais : (1) présents dans
l'éditeur, (2) seedables sans perte de précision via l'init admin, et (3) que
leur édition se propage au calcul des émissions.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.integration.conftest import FakeRequest


def test_nox_sox_factors_exposed_in_editor():
    from app.routers.admin_router import CO2_VARIABLE_DEFS
    from app.services import emissions as em

    for name in (em.NOX_CONV_VAR, em.NOX_SAIL_VAR, em.SOX_CONV_VAR, em.SOX_SAIL_VAR):
        assert name in CO2_VARIABLE_DEFS, f"{name} doit être éditable dans /admin/co2"
        # Repli = la constante canonique du service (source de vérité unique).
        assert CO2_VARIABLE_DEFS[name]["unit"] == "kg/t.nm"


def test_co2_editor_uses_free_step():
    """Le champ valeur doit rester en step="any" : les facteurs NOx/SOx ne sont
    pas des multiples d'un pas fixe (un step figé bloquerait la soumission)."""
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/admin/co2_variables.html")[0]
    assert 'step="any"' in src
    assert 'step="0.000001"' not in src


@pytest.mark.asyncio
async def test_init_seeds_nox_sox_without_precision_loss(db, staff_user):
    """L'init admin seed les 4 facteurs ; get_emission_factors les relit à l'identique."""
    from app.routers.admin_router import co2_variables_init
    from app.services.emissions import (
        CONV_NOX_PER_TNM,
        SAIL_SOX_PER_TNM,
        get_emission_factors,
    )

    await co2_variables_init(FakeRequest(), db=db, user=staff_user)

    factors = await get_emission_factors(db)
    # Valeurs seedées = constantes canoniques (8 décimales préservées).
    assert factors.conv_nox == CONV_NOX_PER_TNM
    assert factors.sail_sox == SAIL_SOX_PER_TNM


@pytest.mark.asyncio
async def test_update_nox_factor_flows_to_emissions(db, staff_user):
    from app.routers.admin_router import co2_variables_update
    from app.services.emissions import NOX_CONV_VAR, get_emission_factors

    await co2_variables_update(
        FakeRequest(),
        name=NOX_CONV_VAR,
        value="0.000777",
        source="test",
        effective_date="2026-06-01",
        db=db,
        user=staff_user,
    )
    factors = await get_emission_factors(db)
    assert factors.conv_nox == Decimal("0.000777")
