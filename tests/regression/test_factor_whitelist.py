"""SUITE GELÉE LOT 9 — sentinelle « règle d'or » des facteurs d'émission.

⚠ **SUITE GELÉE (lot 9) : toute modification exige une justification
d'architecte.** Règle d'or du plan (§2.4) : après le lot 9, AUCUN service ne
multiplie une consommation par un facteur d'émission hors du grand livre
(``services/emission_ledger``) — ``co2.estimate`` et ``services/emissions``
restant les comparateurs officiels (forfaits d'évitement, pas des émissions
réelles de carburant).

Ce test greppe ``app/**/*.py`` pour les jetons qui trahissent la manipulation
d'un facteur d'émission carburant (constantes MEPC, colonnes multi-GES) et
échoue si un fichier HORS liste blanche apparaît. La liste blanche est FIGÉE :
l'étendre exige une justification d'architecte (et, pour les entrées marquées
« legacy », c'est une purge — lots 10/14 — qui est attendue, pas un ajout).
"""

from __future__ import annotations

import re
from pathlib import Path

# Racine du dépôt (tests/regression/ → 2 niveaux au-dessus).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_DIR = _REPO_ROOT / "app"

# Jetons révélant la référence à un facteur d'émission carburant.
FACTOR_TOKENS: tuple[str, ...] = (
    r"3\.206",  # constante MEPC.391(81) tCO₂/tDO
    r"CO2_EMISSION_FACTOR_MDO",  # constante legacy mrv_export
    r"DO_CO2_G_PER_G",  # constante co2.py
    r"DEFAULT_CO2_FACTOR",  # constante legacy mrv_compute
    r"ef_co2_kg_per_kg",  # colonnes / champs multi-GES
    r"ef_ch4_kg_per_kg",
    r"ef_n2o_kg_per_kg",
    r"wtt_gco2eq_per_mj",
)
_TOKEN_RE = re.compile("|".join(FACTOR_TOKENS))

# Liste blanche — chemins relatifs à la racine du dépôt (posix). Elle REFLÈTE
# LE RÉEL : chaque entrée doit encore référencer un jeton facteur (prouvé par
# ``test_no_obsolete_whitelist_entry``). Le lot 14 l'a RÉDUITE en purgeant le
# legacy : ``mrv_compute.py`` (constante CO₂ morte retirée), ``mrv_router.py``
# (CRUD/exports legacy retirés — plus de facteur affiché) et ``emissions.py``
# (NOx/SOx via ``co2_variables`` — ne porte AUCUN jeton facteur CO₂/multi-GES)
# en sont sortis. Étendre exige une justification d'architecte ; retirer, la
# preuve qu'une purge a bien eu lieu (lots 10/14).
FACTOR_WHITELIST: frozenset[str] = frozenset({
    # Le grand livre : L'UNIQUE implémentation des formules (lot 9).
    "app/services/emission_ledger.py",
    # Comparateur / référentiels officiels.
    "app/services/co2.py",  # forfait 1,5/13,7 + chaîne do_co2_ef (/admin/co2)
    "app/services/referential_env.py",  # référentiel emission_factors + replis codés
    "app/models/emission_factor.py",  # schéma du référentiel multi-GES
    # Écran d'administration du référentiel (affichage/saisie, pas de calcul).
    "app/routers/admin_router.py",
    # LEGACY résiduel (lot 14) : ``carbon_report_summary`` (agrégat historique)
    # porte encore ``CO2_EMISSION_FACTOR_MDO`` ; le CSV DNV 18/9 col. a été retiré.
    "app/services/mrv_export.py",
})

# Fichiers dont le lot 9 a PROUVÉ la consolidation : ils ne doivent JAMAIS
# référencer un facteur (ils consomment le grand livre).
CONSOLIDATED_CONSUMERS: frozenset[str] = frozenset({
    "app/services/carbon.py",
    "app/services/anemos.py",
    "app/services/report_generation.py",
    "app/services/kpi.py",
    "app/services/kpi_env.py",
    "app/services/kpi_consolidated.py",
})


def _files_referencing_factors() -> set[str]:
    found: set[str] = set()
    for path in _APP_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover — fichier illisible = suspect
            found.add(path.relative_to(_REPO_ROOT).as_posix())
            continue
        if _TOKEN_RE.search(text):
            found.add(path.relative_to(_REPO_ROOT).as_posix())
    return found


def test_no_new_file_references_an_emission_factor():
    """Règle d'or : tout NOUVEAU fichier référençant un facteur fait échouer la suite."""
    offenders = _files_referencing_factors() - FACTOR_WHITELIST
    assert not offenders, (
        "Règle d'or lot 9 violée : ces fichiers référencent un facteur "
        f"d'émission hors liste blanche : {sorted(offenders)}. Les formules "
        "d'émission vivent UNIQUEMENT dans app/services/emission_ledger.py "
        "(comparateurs officiels : co2.estimate, services/emissions). "
        "Étendre FACTOR_WHITELIST exige une justification d'architecte."
    )


def test_ledger_and_referentials_still_reference_factors():
    """Anti-pourrissement : si les jetons ne matchent plus rien, la sentinelle est morte."""
    found = _files_referencing_factors()
    for required in (
        "app/services/emission_ledger.py",
        "app/services/co2.py",
        "app/services/referential_env.py",
    ):
        assert required in found, (
            f"{required} ne référence plus aucun jeton facteur : les jetons de "
            "la sentinelle sont probablement obsolètes — la mettre à jour."
        )


def test_consolidated_consumers_are_factor_free():
    """Preuve du lot 9 : les consommateurs rebranchés ne portent plus de facteur."""
    found = _files_referencing_factors()
    regressed = found & CONSOLIDATED_CONSUMERS
    assert not regressed, (
        f"Régression règle d'or : {sorted(regressed)} référencent à nouveau un "
        "facteur d'émission — ils doivent consommer le grand livre "
        "(services/emission_ledger), jamais multiplier eux-mêmes."
    )


def test_no_obsolete_whitelist_entry():
    """Sens « entrée obsolète » (lot 14) : la liste blanche REFLÈTE le réel.

    Toute entrée listée qui ne référence PLUS aucun jeton facteur est une
    trace de code purgé (lots 10/14) laissée par erreur → elle doit être
    retirée de ``FACTOR_WHITELIST``. Ce test échoue tant que la liste n'a pas
    été mise à jour, garantissant qu'on ne blanchit pas des fichiers fantômes.
    """
    obsolete = FACTOR_WHITELIST - _files_referencing_factors()
    assert not obsolete, (
        "Entrées obsolètes dans FACTOR_WHITELIST (ne référencent plus aucun "
        f"facteur — purge effectuée, liste à jour requise) : {sorted(obsolete)}."
    )
