"""Sentinelle — aucun gabarit ne référence ``/static/`` sans ``asset()``.

Constat des Opérations le 2026-09-03 : sur la page d'édition d'un leg, « les
filtres et le moteur de recherche sont HS ». Le référentiel était plein
(18 885 ports géolocalisés) et l'API répondait ``200`` avec des données. La
sonde navigateur a montré la vraie cause :

    appel countries : false        ← l'endpoint posé par la PR #180 n'est pas appelé
    appel search    : 1            ← un seul appel groupé, comme dans l'ancien JS
    options Zone    : 7 (Afrique…) ← liste codée en dur, alphabétique
    options Pays    : 0            ← impossible : fillCountry écrit toujours « — Tous — »

Le navigateur exécutait la version de ``leg-cascade.js`` **d'avant** la PR
#180 — celle qui rapatriait tout le référentiel en un appel tronqué à 10 000
lignes, faisant disparaître 123 pays dont le Viêt Nam. Le correctif était
déployé ; le cache du navigateur servait l'ancien fichier.

Cause : ``leg_form.html`` référençait ses scripts par un chemin nu
(``src="/static/js/leg-cascade.js"``) alors que le dépôt possède depuis
longtemps un helper de cache-busting — ``asset()``, qui suffixe ``?v=<mtime>``
— utilisé par 26 gabarits. 54 références y échappaient, dont les cinq scripts
de ``staff/_layout.html``, chargés sur **toutes** les pages ERP, et les trois
scripts de la file d'attente d'encaissement hors connexion.

Un correctif JS déployé mais servi périmé est indétectable côté serveur : les
tests passent, les journaux sont propres, et le défaut reste. D'où cette
sentinelle.
"""

from __future__ import annotations

import pathlib
import re

_TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "app" / "templates"

# ``src="/static/…"`` ou ``href="/static/…"`` — le chemin nu, sans ``asset()``.
_BARE = re.compile(r'(?:src|href)="/static/[^"]+"')


def test_no_template_references_static_without_asset():
    """Toute ressource statique passe par ``asset()``, donc porte ``?v=<mtime>``."""
    fautifs: dict[str, list[str]] = {}
    for f in sorted(_TEMPLATES.rglob("*.html")):
        trouves = _BARE.findall(f.read_text(encoding="utf-8"))
        if trouves:
            fautifs[str(f.relative_to(_TEMPLATES))] = trouves

    assert not fautifs, (
        "Références statiques sans cache-busting — le navigateur servira une "
        "version périmée après déploiement (cf. l'incident du 2026-09-03 sur "
        "leg-cascade.js). Remplacer par {{ asset('chemin/sans/static') }} :\n"
        + "\n".join(f"  {nom} : {', '.join(refs)}" for nom, refs in fautifs.items())
    )


def test_asset_appends_a_version_query():
    """``asset()`` suffixe bien une version — sinon la sentinelle ne garantit rien."""
    from app.templating import _asset

    url = _asset("js/leg-cascade.js")
    assert url.startswith("/static/js/leg-cascade.js?v=")
    assert url.split("?v=")[1].isdigit()
    # Le préfixe ``/static/`` est toléré en entrée et non redoublé en sortie.
    assert _asset("/static/js/leg-cascade.js") == url
