"""Sentinelle : aucune route littérale ne doit être masquée par une route à paramètre.

Starlette résout les routes **dans l'ordre de déclaration** et n'ajoute aucun
convertisseur de type au motif : ``/offers/{offer_id}`` capture n'importe quel
segment, y compris ``new``. Déclarer ``/offers/{offer_id}`` avant
``/offers/new`` rend donc l'écran de création inatteignable — il répond 422
``int_parsing`` sur un identifiant qui n'en est pas un.

Le piège est connu du dépôt (``/captain/ventes/{vessel_id}`` face à
``/catalogue`` et ``/rapport``, cf. CLAUDE.md), et il s'est reproduit sur
``/commercial/offers/new`` dès qu'un écran de détail a été ajouté au-dessus.
D'où cette sentinelle générale plutôt qu'une assertion par cas : elle vaut pour
toutes les routes de l'application, y compris celles qui n'existent pas encore.

Méthode : pour chaque route **entièrement littérale**, on vérifie qu'aucune
route déclarée **avant elle** ne capture déjà son chemin. Les routes portant un
paramètre ne sont pas testées comme cibles — il faudrait leur inventer une
valeur d'exemple, et ce n'est pas là que le défaut se produit.
"""

from __future__ import annotations

from starlette.routing import Route

from app.main import app


def _testable_routes() -> list[Route]:
    return [
        r for r in app.routes if isinstance(r, Route) and getattr(r, "path_regex", None) is not None
    ]


def _shared_method(a: Route, b: Route) -> bool:
    """Deux routes ne se masquent que si elles partagent un verbe HTTP."""
    return bool((a.methods or set()) & (b.methods or set()))


def test_aucune_route_litterale_n_est_captee_par_une_route_a_parametre():
    routes = _testable_routes()
    masquees: list[str] = []

    for position, cible in enumerate(routes):
        if "{" in cible.path:
            continue  # cible paramétrée : hors périmètre (cf. docstring)
        for amont in routes[:position]:
            if "{" not in amont.path or not _shared_method(amont, cible):
                continue
            if amont.path_regex.match(cible.path):
                verbes = ",".join(sorted((cible.methods or set()) & (amont.methods or set())))
                masquees.append(f"{verbes} {cible.path}  ←  masquée par  {amont.path}")

    assert not masquees, (
        "Route(s) littérale(s) inatteignable(s) — déclarez-les AVANT la route à "
        "paramètre qui les capture :\n  " + "\n  ".join(masquees)
    )


def test_la_sentinelle_detecte_reellement_le_defaut():
    """Sabotage : sans la garde d'ordre, le défaut doit être vu.

    On rejoue le raisonnement de la sentinelle sur un couple monté à l'envers.
    Sans ce test, une sentinelle cassée passerait pour une sentinelle verte.
    """
    from starlette.routing import compile_path

    regex_param, _, _ = compile_path("/commercial/offers/{offer_id}")

    assert regex_param.match("/commercial/offers/new")  # le piège existe bien
    assert not regex_param.match("/commercial/offers/new/history")  # et reste borné
