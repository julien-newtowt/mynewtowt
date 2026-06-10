"""Tests pour delete_leg — vérifie que les listes de modèles BLOCKING /
_nullify_optional_fks référencent bien des classes qui ont ``leg_id``.

Bug-pattern récurrent : copier-coller une classe voisine qui n'a pas le
même schéma. Une AttributeError à l'exécution = 500 utilisateur visible.
Ces tests cassent au build si on régresse.
"""
from __future__ import annotations

import inspect

import pytest

from app.services import planning


def _collect_models_from_function(func) -> list[type]:
    """Exécute la closure d'imports + récupère les modèles dans le tuple final.

    Approche pragmatique : on parse le source de la fonction pour
    extraire les noms importés via ``from app.models.XX import YY``,
    puis on les résout via importlib. Évite d'exécuter la coroutine
    (qui exigerait un AsyncSession).
    """
    import importlib

    src = inspect.getsource(func)
    models: list[type] = []
    for line in src.splitlines():
        line = line.strip()
        if line.startswith("from app.models.") and " import " in line:
            mod_path, names = line.split(" import ", 1)
            mod = importlib.import_module(mod_path.replace("from ", ""))
            for n in [x.strip() for x in names.split(",")]:
                if n:
                    models.append(getattr(mod, n))
    return models


@pytest.mark.parametrize(
    "model",
    _collect_models_from_function(planning.delete_leg),
    ids=lambda m: m.__name__,
)
def test_delete_leg_blocking_models_have_leg_id(model):
    """Chaque modèle scanné par BLOCKING doit avoir un attribut leg_id."""
    assert hasattr(model, "leg_id"), f"{model.__name__} n'a pas leg_id"


@pytest.mark.parametrize(
    "model",
    _collect_models_from_function(planning._nullify_optional_fks),
    ids=lambda m: m.__name__,
)
def test_nullify_optional_fks_models_have_leg_id(model):
    """Chaque modèle "nullifié" doit avoir leg_id ET il doit être nullable."""
    assert hasattr(model, "leg_id"), f"{model.__name__} n'a pas leg_id"
    col = model.__table__.columns["leg_id"]
    assert col.nullable, (
        f"{model.__name__}.leg_id est NOT NULL — ne peut pas être set NULL ; "
        f"il appartient à BLOCKING, pas à _nullify_optional_fks"
    )
