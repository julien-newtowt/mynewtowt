"""Sentinelle « tête Alembic unique » — filet anti-panne de déploiement.

La production applique les migrations par ``alembic upgrade head``
(``scripts/deploy.sh``). Dès que deux branches de fonctionnalité chaînent
leurs migrations sur le même parent et sont fusionnées séparément,
``main`` porte **deux têtes** et ``upgrade head`` échoue en refusant de
choisir (« Multiple head revisions are present ») : le déploiement s'arrête
et la base est restaurée depuis le snapshot. La panne s'est produite deux
fois — le 07/08/2026 (révision de fusion ``20260807_0113``) et le
26/08/2026 (``20260826_0119``) — à chaque fois découverte **en
déploiement**, jamais en CI.

Ce test la déplace en CI : il lit le graphe de migrations sans toucher à la
base (``ScriptDirectory`` n'ouvre aucune connexion). Une PR qui introduit
une migration frère d'une autre échoue ici, et le correctif est
mécanique : poser une révision de fusion (``alembic merge heads``) ou
rechaîner sur la tête courante **tant que la révision n'est pas publiée**.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

# Racine du dépôt (tests/regression/ → 2 niveaux au-dessus).
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _script_directory() -> ScriptDirectory:
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    # `script_location` est relatif à la racine du dépôt dans alembic.ini :
    # on l'absolutise pour que le test passe quel que soit le cwd de pytest.
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    return ScriptDirectory.from_config(config)


def test_migrations_have_exactly_one_head() -> None:
    """`alembic upgrade head` doit pouvoir désigner une tête sans ambiguïté."""
    heads = _script_directory().get_heads()

    assert len(heads) == 1, (
        "Le graphe de migrations porte plusieurs têtes "
        f"({', '.join(sorted(heads))}) : `alembic upgrade head` échouera au "
        "déploiement. Poser une révision de fusion (`alembic merge heads`), "
        "ou rechaîner la révision non encore publiée sur la tête courante."
    )


def test_migration_graph_is_walkable_from_base_to_head() -> None:
    """Aucun `down_revision` orphelin : la chaîne est parcourable de bout en bout.

    ``walk_revisions`` lève si une révision référence un parent inexistant
    (fichier supprimé, identifiant mal recopié) — cas qui, lui aussi, ne se
    manifesterait qu'au déploiement.
    """
    script_directory = _script_directory()

    revisions = list(script_directory.walk_revisions("base", "heads"))

    assert revisions, "Aucune révision trouvée dans migrations/versions."
    assert list(script_directory.get_bases()) == ["20260518_0001"], (
        "La base du graphe a changé : une migration a été rechaînée hors de "
        "la chaîne initiale, ou une seconde racine a été introduite."
    )
