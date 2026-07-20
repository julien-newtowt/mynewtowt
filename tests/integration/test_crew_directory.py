"""Intégration — trombinoscope Armement, service ``crew_directory``.

Couvre le regroupement par fonction / par agence, la normalisation de
taxonomie (intitulés Marad bruts, valeurs françaises canoniques, valeurs
inconnues), l'exclusion des marins inactifs, et le repli sans photo.
Cf. docs/strategy/CAHIER_DES_CHARGES_TROMBINOSCOPE.md pour le mapping figé.
"""

from __future__ import annotations

import pytest

from app.models.crew import CrewMember
from app.services import crew_directory as directory_svc


# ───────────────────── normalize_role_for_directory (pure) ─────────────────────


def test_normalize_role_accepts_canonical_french_values():
    assert directory_svc.normalize_role_for_directory("capitaine") == "capitaine"
    assert directory_svc.normalize_role_for_directory("chef_mecanicien") == "chef_mecanicien"
    assert directory_svc.normalize_role_for_directory("matelot_cuisinier") == "matelot_cuisinier"


def test_normalize_role_accepts_raw_marad_titles_case_and_space_insensitive():
    # La sync Marad écrit `ranks[0]` tel quel (cf. marad_sync.py::_apply) —
    # espaces, casse mixte, jamais de souligné.
    assert directory_svc.normalize_role_for_directory("Master") == "capitaine"
    assert directory_svc.normalize_role_for_directory("CHIEF OFFICER") == "second"
    assert directory_svc.normalize_role_for_directory("Chief Engineer") == "chef_mecanicien"
    assert directory_svc.normalize_role_for_directory("Mate") == "lieutenant"
    assert (
        directory_svc.normalize_role_for_directory("Assisting Electrical Engineering Officer")
        == "electricien"
    )
    assert directory_svc.normalize_role_for_directory("Cadet") == "eleve_officier"
    assert directory_svc.normalize_role_for_directory("Bosun") == "bosco"
    assert directory_svc.normalize_role_for_directory("Able Seaman") == "marin"
    assert directory_svc.normalize_role_for_directory("Fitter") == "ajusteur"
    assert directory_svc.normalize_role_for_directory("Able Seaman Cook") == "matelot_cuisinier"


def test_normalize_role_unknown_value_forms_its_own_bucket_not_dropped():
    assert directory_svc.normalize_role_for_directory("Radio Officer") == "radio officer"


def test_normalize_role_empty_defaults_to_marin():
    assert directory_svc.normalize_role_for_directory(None) == "marin"
    assert directory_svc.normalize_role_for_directory("   ") == "marin"


def test_display_label_known_and_unknown():
    assert directory_svc._display_label("capitaine") == "MASTER"
    assert directory_svc._display_label("radio officer") == "RADIO OFFICER"


def test_display_name_prefers_first_last_falls_back_to_full_name():
    m1 = CrewMember(full_name="Jean Dupont", first_name="Jean", last_name="Dupont", role="marin")
    assert directory_svc._display_name(m1) == "JEAN DUPONT"
    m2 = CrewMember(full_name="Jean Dupont", role="marin")
    assert directory_svc._display_name(m2) == "JEAN DUPONT"


# ───────────────────── build_directory (DB) ─────────────────────


async def _seed(db) -> None:
    db.add_all(
        [
            CrewMember(
                full_name="Hadrien Busson",
                first_name="Hadrien",
                last_name="Busson",
                role="Master",
                is_active=True,
            ),
            CrewMember(
                full_name="Gwenola Le Guil",
                first_name="Gwenola",
                last_name="Le Guil",
                role="Master",
                is_active=True,
            ),
            CrewMember(
                full_name="Agathe Lecomte",
                first_name="Agathe",
                last_name="Lecomte",
                role="Mate",
                is_active=True,
            ),
            CrewMember(
                full_name="Ancien Marin",
                first_name="Ancien",
                last_name="Marin",
                role="marin",
                is_active=False,  # inactif — ne doit jamais apparaître
            ),
            CrewMember(
                full_name="Mody Ba",
                first_name="Mody",
                last_name="Ba",
                role="Fitter",
                agency="Pelican Marine Services",
                is_active=True,
            ),
            CrewMember(
                full_name="Charles Ndia",
                first_name="Charles",
                last_name="Ndia",
                role="Able Seaman Cook",
                agency="Pelican Marine Services",
                is_active=True,
            ),
        ]
    )
    await db.flush()


@pytest.mark.asyncio
async def test_build_directory_empty_db_has_no_content(db):
    directory_svc.invalidate_cache()
    directory = await directory_svc.build_directory(db)
    assert directory.has_content is False
    assert directory.member_count == 0


@pytest.mark.asyncio
async def test_build_directory_groups_by_function_excludes_inactive(db):
    directory_svc.invalidate_cache()
    await _seed(db)
    directory = await directory_svc.build_directory(db)

    titles = [g.title for g in directory.groups]
    assert "MASTER" in titles
    assert "MATE" in titles
    # Ordre hiérarchique : Master avant Mate.
    assert titles.index("MASTER") < titles.index("MATE")

    master_group = next(g for g in directory.groups if g.title == "MASTER")
    assert {e.display_name for e in master_group.entries} == {
        "HADRIEN BUSSON",
        "GWENOLA LE GUIL",
    }
    # Le marin inactif n'apparaît nulle part.
    all_names = {e.display_name for g in directory.groups for e in g.entries}
    assert "ANCIEN MARIN" not in all_names
    directory_svc.invalidate_cache()


@pytest.mark.asyncio
async def test_build_directory_groups_agency_separately_with_function_subtitle(db):
    directory_svc.invalidate_cache()
    await _seed(db)
    directory = await directory_svc.build_directory(db)

    agency_group = next(g for g in directory.groups if g.title == "PELICAN MARINE SERVICES")
    assert agency_group.is_agency is True
    labels = {e.display_name: e.function_label for e in agency_group.entries}
    assert labels["MODY BA"] == "FITTER"
    assert labels["CHARLES NDIA"] == "ABLE SEAMAN COOK"

    # Les fonctions "capitaine"/"lieutenant" ne contiennent pas les marins d'agence.
    master_group = next(g for g in directory.groups if g.title == "MASTER")
    assert "MODY BA" not in {e.display_name for e in master_group.entries}
    directory_svc.invalidate_cache()


@pytest.mark.asyncio
async def test_build_directory_no_photo_falls_back_to_none(db):
    directory_svc.invalidate_cache()
    await _seed(db)
    directory = await directory_svc.build_directory(db)
    for group in directory.groups:
        for entry in group.entries:
            assert entry.photo_data_uri is None  # aucune photo uploadée dans ce jeu de test
    directory_svc.invalidate_cache()
