"""Les deux téléchargements de /preuves sont SUSPENDUS — et le disent.

🔴 Ce fichier testait le contraire : que la page servait bien une méthodologie
et un spécimen de rapport annuel en PDF réels, et que le spécimen était
« exactement reproductible par un lecteur qui applique la méthodologie ».

Cette méthode est écartée depuis le 2026-09-11. Le document s'intitulait
*émissions évitées par expédition* et imprimait deux facteurs retirés :

- **13,7 gCO₂/t·km**, base de comparaison à un porte-conteneurs conventionnel,
  écartée par la méthodologie de performance environnementale v3.0 (§11.1) —
  « ne pas réintroduire sans une nouvelle décision explicite » ;
- **1,5 gCO₂/t·km**, facteur NEWTOWT hérité de la V2, **8,5 fois plus bas** que
  l'intensité que l'entreprise publie elle-même (12,7 en approche Métier).

Les tests vérifient donc désormais que ces documents ne sont plus servis, que
le refus est **motivé** plutôt que muet, et que la page ne les propose plus.

Le remplacement est soumis à arbitrage :
``docs/audit/2026-09-11-note-julien-facteur-1-5-vers-profil-velique.md``.
"""

from __future__ import annotations

import pytest

from app.routers import vitrine_router
from app.routers.vitrine_router import (
    preuves_methodology_pdf,
    preuves_sample_annual_report_pdf,
)
from app.templating import templates


class _Req:
    """Requête minimale — ces routes ne lisent que l'IP (rate-limit)."""

    client = type("C", (), {"host": "203.0.113.10"})()
    state = type("S", (), {"lang": "fr"})()


@pytest.mark.parametrize(
    "route",
    (preuves_methodology_pdf, preuves_sample_annual_report_pdf),
    ids=("methodologie", "rapport-annuel-exemple"),
)
@pytest.mark.asyncio
async def test_the_two_preuves_pdfs_are_gone_and_say_why(db, route):
    """410, pas 404 : le document a existé, il est retiré — et on dit pourquoi.

    Un 404 laisserait croire à un lien cassé ; une page blanche, à une panne.
    Ces URL circulent sur des QR de certificats et des supports imprimés : elles
    doivent porter une explication.
    """
    resp = await route(_Req(), db=db)

    assert resp.status_code == 410
    body = resp.body.decode()
    # Le motif est lisible par un humain, pas un code.
    assert "révision" in body
    # Et ce qui reste vrai est rappelé, plutôt que de laisser un vide.
    assert "EU MRV" in body and "THETIS-MRV" in body
    # Aucun des deux facteurs écartés ne reparaît dans le message.
    assert "1,5" not in body and "13,7" not in body
    # Jamais mis en cache : le jour de l'arbitrage, le retour est immédiat.
    assert resp.headers.get("Cache-Control") == "no-store"


@pytest.mark.asyncio
async def test_the_suspended_routes_are_still_rate_limited(db):
    """Un 410 servi sans limite reste un vecteur d'abus.

    Le rate-limit est conservé **avant** le refus : c'est le même garde qu'avant
    la suspension, et il n'a pas de raison de tomber parce que la réponse a
    changé.
    """
    from fastapi import HTTPException

    vitrine_router._PREUVES_PDF_RATE_MAX = 2
    try:
        await preuves_methodology_pdf(_Req(), db=db)
        await preuves_methodology_pdf(_Req(), db=db)
        with pytest.raises(HTTPException) as exc:
            await preuves_methodology_pdf(_Req(), db=db)
        assert exc.value.status_code == 429
    finally:
        vitrine_router._PREUVES_PDF_RATE_MAX = 20


def test_the_preuves_page_no_longer_offers_the_withdrawn_documents():
    """La page ne propose plus ce qu'elle ne sert plus.

    Laisser les boutons mènerait chaque visiteur au message de retrait — une
    impasse présentée comme une preuve, sur la page dont c'est justement
    l'objet.
    """
    src = templates.env.loader.get_source(templates.env, "public/preuves.html")[0]
    assert "/preuves/methodologie.pdf" not in src
    assert "/preuves/rapport-annuel-exemple.pdf" not in src


def test_no_template_reintroduces_the_withdrawn_factors():
    """🔴 Sentinelle : ni 1,5 ni 13,7 ne reviennent sur une surface sortante.

    Les deux valeurs étaient disséminées — gabarits publics, espace client, PDF
    remis aux chargeurs — et **deux scripts les portaient en repli codé**, si
    bien que retirer les attributs de données n'aurait rien désactivé. Ce test
    échoue si l'une d'elles réapparaît ailleurs que dans un commentaire.
    """
    import pathlib
    import re

    roots = (
        "app/templates/public",
        "app/templates/client",
        "app/templates/portal",
        "app/static/js",
    )
    # 🔴 Les commentaires sont retirés en BLOCS, pas ligne à ligne.
    #
    # Une première version filtrait les lignes commençant par `{#` ou `//` : les
    # lignes de CONTINUATION d'un commentaire multi-lignes passaient au travers,
    # et la sentinelle se déclenchait sur les commentaires qui expliquent
    # justement le retrait. Un garde-fou qui accuse sa propre documentation ne
    # tient pas une semaine.
    comment_blocks = re.compile(r"\{#.*?#\}|/\*.*?\*/|^\s*//.*?$", re.DOTALL | re.MULTILINE)
    banned = ("13,7", "13.7 g", "1,5 g")

    offenders: list[str] = []
    for root in roots:
        for path in pathlib.Path(root).rglob("*"):
            if path.suffix not in (".html", ".js"):
                continue
            code = comment_blocks.sub("", path.read_text(encoding="utf-8"))
            for line in code.splitlines():
                if any(b in line for b in banned):
                    offenders.append(f"{path}: {line.strip()[:70]}")
    assert not offenders, "facteurs écartés réintroduits :\n" + "\n".join(offenders)
