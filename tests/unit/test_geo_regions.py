"""Régions géographiques des pays — filtre « Zone » du formulaire de leg.

Bug du 2026-09-02 : la carte pays → continent vivait dans `leg-cascade.js`,
codée en dur sur ~90 pays (« minimal viable list »). Avec 200+ pays en base,
tout le reste tombait dans la zone « Autre ». La table est désormais servie
par l'API depuis `services.geo` — ces tests la tiennent complète.
"""

from __future__ import annotations

import pytest

from app.services.geo import (
    EUROPE_ISO2,
    PORT_REGIONS,
    REGION_ORDER,
    UNKNOWN_REGION,
    region_of,
)


def test_every_region_is_declared_in_the_display_order() -> None:
    """Une région absente de REGION_ORDER passerait en fin de liste sans le dire."""
    assert set(PORT_REGIONS.values()) <= set(REGION_ORDER)


def test_region_order_has_no_orphan() -> None:
    """Et inversement : pas de zone annoncée sans aucun pays."""
    assert set(REGION_ORDER) == set(PORT_REGIONS.values())


def test_country_codes_are_well_formed() -> None:
    for code in PORT_REGIONS:
        assert len(code) == 2 and code.isupper() and code.isalpha(), code


def test_coverage_is_iso_scale() -> None:
    """~250 codes ISO-3166-1 alpha-2 : une table à 90 entrées est un piège."""
    assert len(PORT_REGIONS) > 240, len(PORT_REGIONS)


def test_commercial_europe_is_a_subset_of_the_europe_region() -> None:
    """Deux notions distinctes, qui ne doivent pas se contredire.

    `EUROPE_ISO2` est le **périmètre commercial** (catégories import/export,
    Russie et Turquie volontairement exclues) ; la région « Europe » est la
    **géographie** du sélecteur de ports. Le périmètre doit rester inclus dans
    la région, sinon un pays serait « européen » pour la facturation et
    ailleurs pour l'opérateur.
    """
    outside = sorted(c for c in EUROPE_ISO2 if PORT_REGIONS.get(c) != "Europe")
    assert outside == [], outside


@pytest.mark.parametrize(
    ("country", "expected"),
    [
        ("VN", "Asie"),  # Da Nang — le port absent qui a révélé le bug
        ("NL", "Europe"),
        ("US", "Amériques"),
        ("MQ", "Amériques"),  # Martinique
        ("GP", "Amériques"),  # Guadeloupe
        ("GF", "Amériques"),  # Guyane
        ("RE", "Afrique"),  # La Réunion — océan Indien
        ("YT", "Afrique"),  # Mayotte
        ("ZA", "Afrique"),
        ("SG", "Asie"),
        ("NZ", "Océanie"),
        ("AQ", "Antarctique"),
        ("XZ", "Haute mer"),  # UN/LOCODE : installations en eaux internationales
        ("TR", "Europe"),  # transcontinental, rattaché à l'Europe (cf. geo.py)
        ("RU", "Europe"),
    ],
)
def test_region_of_spot_checks(country: str, expected: str) -> None:
    assert region_of(country) == expected


def test_region_of_is_case_and_space_tolerant() -> None:
    assert region_of(" vn ") == "Asie"


def test_region_of_falls_back_without_raising() -> None:
    """Un code inconnu ne casse pas le formulaire : il atterrit dans « Autre »."""
    assert region_of("QQ") == UNKNOWN_REGION
    assert region_of(None) == UNKNOWN_REGION
    assert region_of("") == UNKNOWN_REGION


def test_embedded_catalogue_countries_are_all_mapped() -> None:
    """Aucun pays du catalogue embarqué ne doit tomber dans « Autre »."""
    from scripts.data.world_ports import WORLD_PORTS

    unmapped = sorted(
        {c for (_lo, _n, c, _la, _ln, _f) in WORLD_PORTS if region_of(c) == UNKNOWN_REGION}
    )
    assert unmapped == [], unmapped
