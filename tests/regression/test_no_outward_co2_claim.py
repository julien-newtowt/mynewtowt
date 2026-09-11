"""🔴 Sentinelle : aucune surface SORTANTE ne porte d'allégation « CO₂ évité ».

Cette sentinelle aurait dû exister dès le premier retrait. Un commentaire de
``app/services/anemos.py`` en promettait la protection alors que le fichier
n'avait jamais été écrit — et c'est très exactement pourquoi quinze surfaces
ont survécu au premier passage : page ``/preuves`` publiant encore la formule,
rapport RSE annuel remis au client, carnet de bord, kit B2B2C, tunnel de devis,
traçabilité consommateur, tableau de bord client…

**Ce qu'elle défend.** Deux facteurs sont écartés depuis le 2026-09-11 :

- **13,7 gCO₂/t·km** — comparaison à un « porte-conteneurs conventionnel »,
  base retirée par la méthodologie de performance environnementale v3.0
  (§11.1), avec la mention « ne pas réintroduire sans une nouvelle décision
  explicite » ;
- **1,5 gCO₂/t·km** — ancien facteur NEWTOWT, **8,5 fois plus bas** que
  l'intensité que l'entreprise publie elle-même (12,7 en approche Métier).

Tout « CO₂ évité » dérivé de ces deux facteurs est une allégation
environnementale au sens de la directive (UE) 2024/825, en application pleine
au 27/09/2026, et ne peut plus sortir de l'entreprise.

**Ce qu'elle ne défend pas.** Les surfaces INTERNES gardent le droit d'afficher
ces grandeurs : la méthodologie §1.2 bis prévoit un suivi interne et une
communication sur demande. ``services.co2.estimate`` reste le comparateur
conventionnel de devis, légitime comme ordre de grandeur commercial.

**Pourquoi une sentinelle plutôt qu'une relecture.** Parce que la relecture a
déjà échoué une fois. Les valeurs se disséminent par des chemins qu'on
n'inspecte pas : un repli codé dans un script (deux l'ont fait), une clé i18n
laissée vivante, un nombre figé en dur dans un gabarit, une somme SQL dans un
routeur. Ici, la liste des surfaces est explicite et le test lit les fichiers.
"""

from __future__ import annotations

import pathlib
import re

# Surfaces qui SORTENT de l'entreprise.
#
# ⚠️ `app/templates/pdf/` n'y est pas en bloc : ce dossier mélange des documents
# remis à des tiers et des pièces internes (récapitulatif de clôture d'escale,
# export voyage du dashboard, rapports MRV, listes d'équipage). On nomme donc
# les PDF sortants un par un — une liste explicite vaut mieux qu'un filtre qui
# se trompe dans un sens ou dans l'autre.
OUTWARD_DIRS: tuple[str, ...] = (
    "app/templates/public",
    "app/templates/client",
    "app/templates/portal",
    "app/templates/pdf/carnet_bord",
    "app/static/js",
)

OUTWARD_FILES: tuple[str, ...] = (
    "app/templates/pdf/anemos_certificate.html",
    "app/templates/pdf/anemos_annual_report.html",
    "app/templates/pdf/dossier_presse.html",
    "app/templates/pdf/kit.html",
)

# Les deux facteurs écartés, sous les formes qu'ils prennent réellement dans le
# dépôt (séparateur décimal français ET anglais, avec ou sans unité collée).
BANNED_FACTORS: tuple[str, ...] = (
    "13,7",
    "13.7 g",
    "13.7g",
    "1,5 g",
    "1.5 g",
)

# Les catalogues i18n sont scannés à part : une clé peut porter la formule sans
# qu'aucun gabarit ne la cite en clair.
I18N_GLOB = "app/i18n/*.py"

# Motifs de commentaire à retirer AVANT de scanner. Ils expliquent légitimement
# ce qui a été supprimé, et une sentinelle qui accuse sa propre documentation ne
# tient pas une semaine.
_COMMENTS = re.compile(
    r"\{#.*?#\}"  # commentaire Jinja
    r"|/\*.*?\*/"  # bloc JS/CSS
    r"|^[ \t]*//.*?$"  # ligne JS
    r"|^[ \t]*#(?!\s*\{).*?$",  # ligne Python (hors f-string)
    re.DOTALL | re.MULTILINE,
)


def _strip_comments(text: str) -> str:
    """Neutralise les commentaires EN PRÉSERVANT la numérotation des lignes.

    🔴 Une première version les supprimait : les numéros rapportés étaient
    alors décalés, et pointaient des lignes innocentes. Un garde-fou qui
    désigne le mauvais endroit fait perdre plus de temps qu'il n'en gagne.

    On remplace donc chaque commentaire par autant de sauts de ligne qu'il en
    contenait.
    """

    def _blank(match: re.Match[str]) -> str:
        return chr(10) * match.group(0).count(chr(10))

    return _COMMENTS.sub(_blank, text)


def _outward_sources() -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for d in OUTWARD_DIRS:
        root = pathlib.Path(d)
        if not root.exists():
            continue
        paths += [p for p in root.rglob("*") if p.suffix in (".html", ".js")]
    paths += [pathlib.Path(f) for f in OUTWARD_FILES if pathlib.Path(f).exists()]
    return paths


def test_no_outward_surface_carries_a_withdrawn_factor():
    """Ni 13,7 ni 1,5 gCO₂/t·km sur une surface sortante, hors commentaire."""
    offenders: list[str] = []
    for path in _outward_sources():
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for num, line in enumerate(code.splitlines(), start=1):
            if any(f in line for f in BANNED_FACTORS):
                offenders.append(f"{path}:{num}: {line.strip()[:80]}")
    assert not offenders, "facteur écarté sur une surface sortante :\n" + "\n".join(offenders)


def test_no_outward_surface_renders_an_avoided_emissions_figure():
    """Aucune variable « CO₂ évité » n'est RENDUE sur une surface sortante.

    On cherche les interpolations, pas les mots : c'est l'affichage d'un
    **chiffre** qui constitue l'allégation. Une phrase qui parle d'émissions
    évitées sans en donner la valeur relève de la rédaction commerciale, pas de
    cette sentinelle.
    """
    rendered = re.compile(
        r"\{\{[^}]*\b(co2_avoided_kg|avoided_co2_kg|total_avoided_kg|"
        r"co2_conventional_kg|conventional_co2_kg|total_conventional_kg|"
        r"avoided_t|avoided_pct)\b"
    )
    offenders: list[str] = []
    for path in _outward_sources():
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for num, line in enumerate(code.splitlines(), start=1):
            if rendered.search(line):
                offenders.append(f"{path}:{num}: {line.strip()[:80]}")
    assert not offenders, "chiffre d'émissions évitées rendu :\n" + "\n".join(offenders)


def test_no_i18n_catalogue_still_carries_the_formula():
    """Les 5 catalogues ne portent plus les facteurs écartés.

    Une clé de traduction est une surface sortante à part entière : elle finit
    dans une page. C'est par là que la formule a survécu au premier retrait —
    ``prf_formula_l1`` publiait « (13,7 − 1,5) × tonnage × distance ÷ 1 000 »
    sur la page dont l'objet est précisément de SUBSTANTIER nos allégations.
    """
    offenders: list[str] = []
    for path in sorted(pathlib.Path().glob(I18N_GLOB)):
        if path.stem not in ("fr", "en", "es", "pt_br", "vi"):
            continue
        for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if any(f in line for f in BANNED_FACTORS):
                offenders.append(f"{path}:{num}: {stripped[:80]}")
    assert not offenders, "facteur écarté dans un catalogue i18n :\n" + "\n".join(offenders)


def test_no_outward_surface_claims_an_avoided_comparison_in_prose():
    """🔴 Le troisième filet : les MOTS, pas seulement les variables.

    Les deux tests précédents cherchent des facteurs et des noms de variables.
    Le kit B2B2C leur a échappé — il chiffrait l'évitement via une variable
    nommée ``co2_kg``, parfaitement générique. Ce test cherche donc ce qu'un
    lecteur voit : une comparaison à un transport conventionnel, et la
    revendication d'émissions évitées.

    Ce sont ces formulations, et non les identifiants du code, qui constituent
    l'allégation au sens de la directive (UE) 2024/825.
    """
    claims = (
        "CO₂ évité",
        "CO₂ évités",
        "CO2 évité",
        "CO₂ avoided",
        "avoided CO₂",
        "CO₂ evitado",
        "émissions évitées",
        "avoided-CO₂",
        "avoided emissions",
        "cargo conventionnel",
        "conventional cargo",
        "porte-conteneurs conventionnel",
        "conventional container",
        "transport conventionnel",
    )
    offenders: list[str] = []
    for path in _outward_sources():
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for num, line in enumerate(code.splitlines(), start=1):
            if any(c in line for c in claims):
                offenders.append(f"{path}:{num}: {line.strip()[:80]}")
    assert not offenders, (
        "allégation en clair sur une surface sortante :" + chr(10) + chr(10).join(offenders)
    )
