"""Sentinelle — un ``hx-include`` ne doit jamais produire un 422.

``hx-include`` rassemble **tous** les champs désignés, quel que soit leur
contenu : un `<select>` sur son option vide part comme ``leg_id=`` (chaîne
vide), pas absent de la query-string. Face à un paramètre déclaré
``int | None``, FastAPI répond alors **422 avant d'entrer dans la route**.

Côté client, le corps d'un 422 porte un ``detail`` qui est une **liste** ;
``toast.js`` ne sait lire qu'un ``detail`` textuel (celui d'un
``HTTPException``) et affiche donc son repli générique « Action refusée —
rechargez la page ». L'opérateur voit un refus d'autorisation là où il n'a
qu'un champ vide, et le fragment attendu n'arrive jamais.

C'est le défaut constaté le 2026-09-07 sur `/commercial/offers/new` : chaque
changement de client échouait, et la liste des grilles ne se filtrait jamais.

Ce test lit les gabarits, résout les routes réellement câblées à un
``hx-include``, et vérifie que **chacun de leurs paramètres de requête
optionnels accepte la chaîne vide**. Il grandit tout seul : un nouveau
``hx-include`` dans un gabarit est couvert sans rien écrire ici.

Portée volontairement bornée aux routes ainsi câblées : imposer la tolérance à
*toutes* les routes de l'application ferait remonter de la dette antérieure
sans rapport avec ce mode de panne.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import pytest
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError

from app.main import app

_TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "templates"

#: Un attribut ``hx-get``/``hx-post``/``hx-put`` et son URL, sur un élément qui
#: porte aussi ``hx-include``. On lit l'élément entier (jusqu'au ``>``) pour ne
#: retenir que les cibles réellement concernées.
_ELEMENT = re.compile(r"<[^>]*hx-include[^>]*>", re.DOTALL)
_HX_URL = re.compile(r"""hx-(?:get|post|put|patch)\s*=\s*["']([^"']+)["']""")


def _htmx_include_paths() -> set[str]:
    """Chemins d'URL invoqués par un élément portant ``hx-include``."""
    paths: set[str] = set()
    for template in _TEMPLATES.rglob("*.html"):
        for element in _ELEMENT.findall(template.read_text(encoding="utf-8")):
            for url in _HX_URL.findall(element):
                # On ignore les URL construites par Jinja : le test ne sait pas
                # les résoudre, et elles sont rares sur un hx-include.
                if "{{" in url or "{%" in url:
                    continue
                paths.add(url.split("?", 1)[0])
    return paths


def _route_for(path: str) -> APIRoute | None:
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path:
            return route
    return None


def test_at_least_one_htmx_include_target_is_checked() -> None:
    """Garde-fou du garde-fou : si l'extraction ne trouve plus rien, le test
    ci-dessous passerait à vide sans rien vérifier."""
    assert _htmx_include_paths(), "aucun hx-include trouvé — l'extraction est cassée"


def test_htmx_include_targets_tolerate_blank_query_params() -> None:
    unresolved: list[str] = []
    offenders: list[str] = []

    for path in sorted(_htmx_include_paths()):
        route = _route_for(path)
        if route is None:
            unresolved.append(path)
            continue
        for param in route.dependant.query_params:
            # Seuls les paramètres optionnels sont concernés : un paramètre
            # requis absent est un vrai 422, et c'est correct.
            if param.required:
                continue
            # ``field_info.annotation`` est le type **nu** : les validateurs
            # d'un ``Annotated[...]`` vivent dans ``metadata``. Les recoller,
            # sinon la sentinelle déclarerait fautif un paramètre correctement
            # annoté ``OptionalInt``.
            annotation = param.field_info.annotation
            if param.field_info.metadata:
                annotation = Annotated[tuple([annotation, *param.field_info.metadata])]
            try:
                TypeAdapter(annotation).validate_python("")
            except ValidationError:
                offenders.append(f"{path} → {param.name}: {annotation}")

    assert (
        not unresolved
    ), "Cibles hx-include non résolues en route FastAPI (URL renommée ?) : " + ", ".join(unresolved)
    assert not offenders, (
        "Ces paramètres de requête refusent la chaîne vide alors qu'un "
        "`hx-include` la leur enverra — la route répondra 422 et l'écran "
        "affichera « Action refusée — rechargez la page » :\n  "
        + "\n  ".join(offenders)
        + "\n\nUtiliser les alias de `app.utils.query` (OptionalInt / "
        "OptionalFloat / OptionalBool), qui traduisent « vide » par « non fourni »."
    )


@pytest.mark.parametrize(
    ("annotation_name", "raw"),
    [("OptionalInt", ""), ("OptionalInt", "   "), ("OptionalFloat", ""), ("OptionalBool", "")],
)
def test_query_aliases_read_blank_as_absent(annotation_name: str, raw: str) -> None:
    from app.utils import query

    adapter = TypeAdapter(getattr(query, annotation_name))
    assert adapter.validate_python(raw) is None


def test_query_aliases_still_reject_a_genuinely_invalid_value() -> None:
    """Une saisie fautive doit continuer à lever : la faire passer pour une
    absence de saisie donnerait un résultat sans dire qu'on a ignoré la demande."""
    from app.utils.query import OptionalInt

    adapter = TypeAdapter(OptionalInt)
    assert adapter.validate_python("12") == 12
    with pytest.raises(ValidationError):
        adapter.validate_python("douze")
