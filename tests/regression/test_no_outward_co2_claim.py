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

**Contre-audit du 2026-09-14 : deux angles morts comblés.** Le test de prose
(``test_no_outward_surface_claims_an_avoided_comparison_in_prose``) ne
scannait ni les catalogues i18n (22 clés × 5 langues ont survécu à la vague
précédente : la formule anglaise/espagnole/portugaise/vietnamienne était
absente de la liste), ni les services Python qui composent du texte sortant
sans gabarit Jinja (``_social_readme`` du kit ZIP portait encore l'allégation
en toutes lettres). Les deux surfaces sont désormais couvertes
(``_i18n_sources()``, ``OUTWARD_PY_FILES``).

**Décision délibérée : ne pas bannir l'identifiant ``co2_kg``.** C'est le nom
de variable qui a fait échapper le kit B2B2C au premier passage — mais il sert
aussi, légitimement, à porter des émissions RÉELLEMENT MESURÉES ailleurs dans
l'application (grand livre, KPI, PDF interne). Le bannir comme nom de
variable produirait des faux positifs sans rien défendre de plus que la garde
structurelle déjà en place : ``social_kit._co2_block``/``_co2_summary`` et
``coffee_stories._co2_phrase_short`` reçoivent ``co2_kg`` puis l'ignorent
explicitement (``del co2_kg``), documenté comme tel. Le filet qui protège
cette variable est donc le test de prose ci-dessus, pas un filtre de nom.
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

# Services/routeurs Python qui GÉNÈRENT du texte sortant sans passer par un
# gabarit Jinja (SVG assemblé par f-string, notice ZIP, récits d'origine). Le
# kit social a échappé au premier retrait précisément parce que ces fichiers
# n'étaient pas lus par cette sentinelle : ni gabarit (`{{ }}`), ni chiffre en
# dur détectable par le test de facteur seul.
OUTWARD_PY_FILES: tuple[str, ...] = (
    "app/routers/client_dashboard_router.py",
    "app/services/social_kit.py",
    "app/services/coffee_stories.py",
    "app/services/cacao_stories.py",
)

# Un même catalogue i18n sert à la fois les gabarits `staff/` (internes) et
# les gabarits `public/`/`client/`/`portal/` (sortants) : contrairement à
# OUTWARD_DIRS, il n'y a pas de dossier qui sépare les deux. Deux exceptions
# documentées, vérifiées ligne à ligne, plutôt qu'un filtre qui devine :
#
# 1. Clés dont le rendu est vérifié comme exclusivement interne (aucun
#    gabarit hors `app/templates/staff/` ne les référence) — la méthodologie
#    §1.2 bis réserve précisément aux surfaces internes le droit de porter un
#    chiffre d'émissions évitées.
INTERNAL_ONLY_I18N_KEYS: frozenset[str] = frozenset(
    {
        "kpi_co2_avoided",
        "kpi_vs_conv",
        "kpi_co2_avoided_short",
        "kpi_co2_avoided_kg",
        "dashperf_filter_method",
        "dashperf_kpi_avoided_airfreight",
        "dashperf_admin_table_subtitle",
    }
)

# 2. Clés où « conteneur/cargo conventionnel » qualifie la PROTECTION DE LA
#    CARGAISON (chaleur, humidité) — jamais une comparaison d'émissions. Lu en
#    entier avant d'exempter : aucune des deux ne cite CO₂, évité ni Anemos.
QUALITY_NOT_EMISSIONS_I18N_KEYS: frozenset[str] = frozenset(
    {
        "cafe_s1_lead",
        "cacao_s1_lead",
    }
)

_I18N_KEY_RE = re.compile(r'^\s*"([A-Za-z0-9_]+)"\s*:')

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
    r"|^[ \t]*#(?!\s*\{).*?$"  # ligne Python (hors f-string)
    r'|"""[\s\S]*?"""'  # docstring Python (triple guillemets doubles)
    r"|'''[\s\S]*?'''",  # docstring Python (triple guillemets simples)
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


def _outward_py_sources() -> list[pathlib.Path]:
    return [pathlib.Path(f) for f in OUTWARD_PY_FILES if pathlib.Path(f).exists()]


def _i18n_sources() -> list[pathlib.Path]:
    return [
        p
        for p in sorted(pathlib.Path().glob(I18N_GLOB))
        if p.stem in ("fr", "en", "es", "pt_br", "vi")
    ]


def test_no_outward_surface_carries_a_withdrawn_factor():
    """Ni 13,7 ni 1,5 gCO₂/t·km sur une surface sortante, hors commentaire."""
    offenders: list[str] = []
    for path in [*_outward_sources(), *_outward_py_sources()]:
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
    for path in _i18n_sources():
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
        "emisiones evitadas",
        "emissões evitadas",
        "avoided-CO₂",
        "avoided emissions",
        "CO₂ tránh được",
        "đã tránh",
        "cargo conventionnel",
        "conventional cargo",
        "porte-conteneurs conventionnel",
        "conventional container",
        "transport conventionnel",
        "cargueiro convencional",
        "carguero convencional",
        "tàu chở hàng thông thường",
    )
    exempt_keys = INTERNAL_ONLY_I18N_KEYS | QUALITY_NOT_EMISSIONS_I18N_KEYS
    offenders: list[str] = []
    for path in [*_outward_sources(), *_outward_py_sources(), *_i18n_sources()]:
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for num, line in enumerate(code.splitlines(), start=1):
            key_match = _I18N_KEY_RE.match(line)
            if key_match and key_match.group(1) in exempt_keys:
                continue
            if any(c in line for c in claims):
                offenders.append(f"{path}:{num}: {line.strip()[:80]}")
    assert not offenders, (
        "allégation en clair sur une surface sortante :" + chr(10) + chr(10).join(offenders)
    )
