"""Garde anti-no-op du seed de vitesse d'essai (migration ``20260911_0148``).

Le 17/09/2026, cette migration a réussi en production **sans écrire une seule
ligne** : elle rattache les navires par ``imo_number``, et la production porte
les IMO de remplissage de ``scripts/seed_demo.py``. Personne ne l'a su — une
migration qui ne sème rien rend le même « OK » qu'une migration qui sème tout.

La garde distingue les deux cas à zéro ligne écrite, et c'est toute sa
difficulté : « rien à rattacher » (base neuve, chaîne CI) est légitime,
« des navires existent et aucun ne correspond » est un échec.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "20260911_0148_vessel_baseline_speed.py"
)


def _load_migration() -> ModuleType:
    """Charge le module de migration hors chaîne Alembic (aucune connexion)."""
    spec = importlib.util.spec_from_file_location("_mig_0148", _MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_empty_fleet_is_not_a_failure():
    """Base neuve / chaîne CI : rien à rattacher, la migration doit passer.

    C'est le cas qu'une garde naïve « 0 ligne écrite ⇒ erreur » casserait —
    elle ferait échouer la CI et tout bootstrap d'environnement.
    """
    mig = _load_migration()

    mig._assert_seed_reached_the_fleet(0, 0)


def test_fleet_present_but_nothing_matched_raises():
    """L'état réel de la production le 17/09/2026 : 4 navires, aucun rattaché."""
    mig = _load_migration()

    with pytest.raises(RuntimeError) as exc:
        mig._assert_seed_reached_the_fleet(4, 0, ("9123456", "9123457", "9123458", "9123459"))

    message = str(exc.value)
    # Le message doit porter le diagnostic, pas seulement le symptôme : les IMO
    # attendus ET ceux trouvés, sans quoi l'opérateur ne sait pas quoi corriger.
    assert "9982938" in message, "les IMO attendus doivent être nommés"
    assert "9123456" in message, "les IMO réellement en base doivent être nommés"
    assert "Admin → Flotte" in message, "le message doit dire où corriger"


def test_partial_match_is_accepted():
    """ATLANTIS peut n'être pas encore créé : un rattachement partiel passe."""
    mig = _load_migration()

    mig._assert_seed_reached_the_fleet(4, 2, ("9982938", "9983798"))


def test_seed_still_carries_the_three_in_service_vessels():
    """Les valeurs semées restent celles des fichiers EEDI visés par BV.

    Sentinelle de non-régression : ce sont des constantes **en dur** (une
    migration est un instantané), et elles décident d'un chiffre publiable.
    """
    mig = _load_migration()

    seeded = {imo: speed for imo, speed, _ in mig._BASELINE_SEED}

    assert {str(k) for k in seeded} == {"9982938", "9983798", "1094917"}
    assert [str(v) for v in seeded.values()] == ["11.360", "12.070", "11.860"]
    # Chaque vitesse porte sa source : une vitesse sans source ne vaut pas mieux
    # que le chiffre qu'elle remplace (cf. ``Vessel.baseline_speed_source``).
    assert all(source.strip() for _, _, source in mig._BASELINE_SEED)
