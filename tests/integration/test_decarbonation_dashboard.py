"""Le taux de décarbonation sur le dashboard interne (méthodologie v3.0 §11.2).

Ces tests couvrent le **branchement** — la formule elle-même est verrouillée par
``tests/unit/test_decarbonation.py``, qui épingle les chiffres publiés.

⚠️ Suivi **interne**. La méthodologie §1.2 bis ne publie pas ce taux de notre
propre initiative : il se communique sur demande, en énonçant sa base. D'où le
test qui vérifie que l'écran **nomme** le scénario de référence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.models.voyage_emission_summary import VoyageEmissionSummary
from app.services.kpi_env import fleet_summary
from app.templating import templates

_ETD = datetime(2026, 3, 1, tzinfo=UTC)
_ATA = datetime(2026, 3, 20, tzinfo=UTC)


async def _seed(db, *, baseline_speed_kn, co2eq_t, co2eq_mouillage_t):
    db.add(
        Vessel(
            id=1,
            code="ANE",
            name="Anemos",
            is_active=True,
            baseline_speed_kn=baseline_speed_kn,
        )
    )
    db.add(Port(id=1, locode="FRLEH", name="Le Havre", country="FR"))
    db.add(Port(id=2, locode="BRSSZ", name="Santos", country="BR"))
    await db.flush()
    db.add(
        Leg(
            id=1,
            leg_code="1AFRBR6",
            vessel_id=1,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=_ETD,
            eta_ref=_ATA,
            etd=_ETD,
            eta=_ATA,
            ata=_ATA,
            distance_nm=Decimal("5782"),
        )
    )
    await db.flush()
    db.add(
        VoyageEmissionSummary(
            leg_id=1,
            source="events",
            co2_t=Decimal("128.4"),
            co2eq_t=co2eq_t,
            co2_mouillage_t=Decimal("1.9"),
            co2eq_mouillage_t=co2eq_mouillage_t,
            distance_nm=Decimal("5782"),
            cargo_bl_t=Decimal("500"),
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_the_rate_is_computed_when_the_ship_has_its_trial_speed(db):
    """Vitesse d'essai + CO₂eq ⇒ un taux, et un taux plausible.

    ANEMOS, 5 782 milles : la référence « moteur seul, route directe » consomme
    16,0 kg de MDO par mille de route directe. Notre voyage réel émet
    130,4 t CO₂e. Le taux doit être largement positif — la voile fait l'essentiel
    du travail — sans jamais atteindre 100 %, puisque nos moteurs tournent aussi.
    """
    await _seed(
        db,
        baseline_speed_kn=Decimal("11.360"),
        co2eq_t=Decimal("128.5"),
        co2eq_mouillage_t=Decimal("1.9"),
    )

    summary = await fleet_summary(db, period=2026, method="B", now=_ATA)

    dec = summary.fleet.decarbonation
    assert dec is not None
    assert dec.rate_pct is not None
    assert dec.na_reason is None
    assert Decimal("0") < dec.rate_pct < Decimal("100")
    # Notre côté du rapport est bien l'assiette MÉTIER : trajet + mouillage.
    assert dec.actual_ghg_t == Decimal("130.400")


@pytest.mark.asyncio
async def test_a_ship_without_trial_speed_yields_a_motivated_absence(db):
    """🔴 Pas de vitesse d'essai ⇒ absence motivée, jamais un taux fabriqué.

    On ne substitue pas la vitesse d'un sistership : ANEMOS et ARTEMIS diffèrent
    de 12 % en puissance à vitesse égale, et un repli préjugerait auquel des deux
    le navire ressemble.
    """
    await _seed(
        db,
        baseline_speed_kn=None,
        co2eq_t=Decimal("128.5"),
        co2eq_mouillage_t=Decimal("1.9"),
    )

    dec = (await fleet_summary(db, period=2026, method="B", now=_ATA)).fleet.decarbonation

    assert dec is not None
    assert dec.rate_pct is None
    assert dec.na_reason  # motivé, pas un simple None silencieux


@pytest.mark.asyncio
async def test_an_unknown_anchorage_keeps_the_voyage_out_of_both_terms(db):
    """Mouillage inconnu ⇒ pas de CO₂eq Métier ⇒ le voyage sort des deux termes.

    Le laisser entrer avec son seul CO₂ de trajet sous-estimerait notre côté du
    rapport, donc **gonflerait** le taux. C'est la direction la plus exposée.
    """
    await _seed(
        db,
        baseline_speed_kn=Decimal("11.360"),
        co2eq_t=Decimal("128.5"),
        co2eq_mouillage_t=None,  # inconnu, pas nul
    )

    dec = (await fleet_summary(db, period=2026, method="B", now=_ATA)).fleet.decarbonation

    assert dec is not None
    assert dec.rate_pct is None


def test_the_screen_names_the_baseline_next_to_the_rate():
    """🔴 Le chiffre ne sort jamais sans sa base.

    La méthodologie §1.2 bis impose d'énoncer explicitement le scénario de
    référence chaque fois que ce taux est communiqué, « sans quoi le chiffre
    serait inintelligible et ouvert à contestation ». Le gabarit doit donc
    afficher la base **à côté** de la valeur, pas dans une note de bas de page
    optionnelle.
    """
    src = templates.env.loader.get_source(
        templates.env, "staff/dashboard_perf/_fleet_fragment.html"
    )[0]
    assert "dashperf_kpi_decarbonation" in src
    assert "dashperf_decarbonation_baseline" in src
    # La base nommée dit bien à quoi on se compare, et que la route est directe.
    from app.i18n import fr

    baseline = fr.CATALOG["dashperf_decarbonation_baseline"]
    assert "sans voiles" in baseline
    assert "directe" in baseline
