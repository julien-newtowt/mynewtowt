"""Admin → Flotte : la vitesse d'essai devient saisissable, et jamais sans source.

Contexte (17/09/2026). ``baseline_speed_kn`` décide du taux de décarbonation et
n'avait **qu'un seul écrivain** : la migration ``20260911_0148``. Or une
migration appliquée ne se rejoue pas. Quand son seed n'a rattaché aucun navire
en production — IMO de remplissage — la valeur est restée ``NULL`` **sans aucune
voie de correction depuis l'application**. Le modèle anticipait pourtant la
correction manuelle (``baseline_speed_source`` existe pour la tracer) ; l'écran
ne la permettait pas.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException

from tests.integration.conftest import FakeRequest


def _base_form(**overrides):
    form = {
        "name": "Anemos",
        "vessel_class": "phoenix",
        "imo_number": "9982938",
        "flag": "FR",
        "dwt": None,
        "capacity_palettes": "978",
        "default_speed_kn": "8.0",
        "default_elongation": "1.15",
        "opex_daily_sea_eur": "4500",
    }
    form.update(overrides)
    return form


@pytest.mark.asyncio
async def test_operator_can_set_baseline_speed_with_its_source(db, staff_user):
    """Le geste de réparation attendu en production : vitesse + document visé."""
    from app.models.vessel import Vessel
    from app.routers.admin_router import vessel_edit

    db.add(Vessel(id=1, code="ANE", name="Anemos", imo_number="9982938"))
    await db.flush()

    await vessel_edit(
        1,
        FakeRequest(),
        **_base_form(
            baseline_speed_kn="11.360",
            baseline_speed_source="REF-07 §3.1.3.3 — C413-1000-16 rev B, visé BV 23/07/2024",
        ),
        db=db,
        user=staff_user,
    )

    vessel = await db.get(Vessel, 1)
    # Decimal, pas float : la colonne est Numeric(6, 3) et la valeur part dans
    # un chiffre publiable — l'arrondi se fait à la saisie, pas dans la base.
    assert vessel.baseline_speed_kn == Decimal("11.360")
    assert vessel.baseline_speed_source.startswith("REF-07")


@pytest.mark.asyncio
async def test_speed_without_source_is_refused(db, staff_user):
    """🔴 La règle de fond : aucun paramètre de la base n'est une hypothèse interne.

    Ouvrir la saisie manuelle sans cette garde ferait de l'écran le trou par
    lequel une valeur sans document visé entre dans le taux de décarbonation.
    """
    from app.models.vessel import Vessel
    from app.routers.admin_router import vessel_edit

    db.add(Vessel(id=1, code="ANE", name="Anemos", imo_number="9982938"))
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await vessel_edit(
            1,
            FakeRequest(),
            **_base_form(baseline_speed_kn="11.360", baseline_speed_source=""),
            db=db,
            user=staff_user,
        )

    assert exc.value.status_code == 400
    # `toast.js` ne sait lire qu'un `detail` textuel : un message exploitable,
    # pas une liste de validation.
    assert isinstance(exc.value.detail, str)
    assert "source" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_clearing_the_speed_also_clears_the_source(db, staff_user):
    """Une source orpheline décrirait une valeur qui n'existe plus."""
    from app.models.vessel import Vessel
    from app.routers.admin_router import vessel_edit

    db.add(
        Vessel(
            id=1,
            code="ANE",
            name="Anemos",
            imo_number="9982938",
            baseline_speed_kn=Decimal("11.360"),
            baseline_speed_source="REF-07",
        )
    )
    await db.flush()

    await vessel_edit(
        1,
        FakeRequest(),
        **_base_form(baseline_speed_kn="", baseline_speed_source="REF-07"),
        db=db,
        user=staff_user,
    )

    vessel = await db.get(Vessel, 1)
    assert vessel.baseline_speed_kn is None
    assert vessel.baseline_speed_source is None


@pytest.mark.parametrize("raw", ["nan", "Infinity", "-3", "0", "150", "abc"])
def test_absurd_speeds_are_refused(raw):
    """``Decimal("nan")`` est un littéral VALIDE — la garde naïve le laisse passer.

    Même motif que ``utils.decimals`` : PostgreSQL accepte ``NaN`` en ``numeric``
    et le propage. Ici il rendrait le taux de décarbonation illisible sans que
    rien ne signale d'où vient la valeur.
    """
    from app.routers.admin_router import parse_baseline_speed

    with pytest.raises(HTTPException):
        parse_baseline_speed(raw, "REF-07 §3.1.3.3")


def test_empty_speed_is_not_an_error():
    """Un navire sans vitesse d'essai est un état légitime (ATLAS, hors service)."""
    from app.routers.admin_router import parse_baseline_speed

    assert parse_baseline_speed("", "REF-07") == (None, None)
    assert parse_baseline_speed(None, None) == (None, None)
