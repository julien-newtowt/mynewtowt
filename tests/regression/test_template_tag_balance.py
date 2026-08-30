"""Sentinelle : les templates Jinja ferment exactement les balises qu'ils ouvrent.

Deux pages du module caisse ont livré en production une balise ``</div>`` en
trop (``staff/cashbox/detail.html`` et ``staff/onboard_sales/vessel.html``,
signalées depuis le bord le 2026-08-29). Le navigateur ne signale rien : il
referme silencieusement le conteneur le plus proche — ici le ``<main>`` du
layout — et **tout le contenu suivant sort de la grille**. La page reste
fonctionnelle, seulement illisible, et le défaut n'apparaît que lorsque la
branche fautive est rendue (ici : dès qu'un état de caisse existe). Aucun test
fonctionnel ne peut donc l'attraper.

Le contrôle réduit chaque template à **un** chemin de rendu — première branche
des ``if``/``elif``/``else``, une itération des boucles — puis vérifie que les
balises appariées le sont. Sans cette réduction, un template qui ouvre la même
balise dans deux branches exclusives (``{% if %}<form A>{% else %}<form B>``)
serait compté deux fois pour une seule fermeture : un faux positif.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "templates"

# Balises appariées dont le déséquilibre casse une mise en page. Les balises
# vides (``<br>``, ``<img>``, ``<input>``…) n'y figurent pas : elles ne se
# ferment pas et fausseraient le compte.
PAIRED_TAGS = (
    "div",
    "article",
    "section",
    "main",
    "aside",
    "header",
    "footer",
    "nav",
    "form",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "td",
    "th",
    "ul",
    "ol",
    "li",
    "details",
    "summary",
    "label",
    "select",
    "button",
    "fieldset",
)

_STATEMENT = re.compile(r"\{%-?\s*(\w+)")
_TOKEN = re.compile(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}", re.S)


def _single_render_path(source: str) -> str:
    """Ne garde que la première branche de chaque alternative Jinja.

    ``{% for %}`` est traité comme une itération unique ; son ``{% else %}``
    (corps « collection vide ») est écarté comme celui d'un ``if``.
    """
    out: list[str] = []
    stack: list[dict[str, object]] = []  # {"kind": ..., "emit": bool}
    cursor = 0
    for token in _TOKEN.finditer(source):
        if all(frame["emit"] for frame in stack):
            out.append(source[cursor : token.start()])
        cursor = token.end()
        raw = token.group(0)
        if not raw.startswith("{%"):
            continue
        match = _STATEMENT.match(raw)
        keyword = match.group(1) if match else ""
        if keyword in ("if", "for"):
            stack.append({"kind": keyword, "emit": True})
        elif keyword in ("elif", "else"):
            if stack:
                stack[-1]["emit"] = False  # branche suivante : jamais rendue
        elif keyword in ("endif", "endfor") and stack:
            stack.pop()
    if all(frame["emit"] for frame in stack):
        out.append(source[cursor:])
    return "".join(out)


def _imbalances(source: str) -> dict[str, tuple[int, int]]:
    rendered = _single_render_path(source)
    found: dict[str, tuple[int, int]] = {}
    for tag in PAIRED_TAGS:
        opened = len(re.findall(rf"<{tag}\b", rendered, re.I))
        closed = len(re.findall(rf"</{tag}\s*>", rendered, re.I))
        if opened != closed:
            found[tag] = (opened, closed)
    return found


@pytest.mark.parametrize(
    "template",
    sorted(TEMPLATES.rglob("*.html")),
    ids=lambda p: str(p.relative_to(TEMPLATES)),
)
def test_paired_tags_are_balanced(template: Path) -> None:
    imbalance = _imbalances(template.read_text(encoding="utf-8"))
    assert not imbalance, (
        f"{template.relative_to(TEMPLATES)} : balises déséquilibrées "
        + ", ".join(f"<{tag}> {o} ouvertes / {c} fermées" for tag, (o, c) in imbalance.items())
        + ". Une fermeture en trop referme le conteneur du layout et fait "
        "sortir tout le contenu suivant de la grille."
    )


def test_the_sentinel_catches_a_stray_closing_tag() -> None:
    """Le contrôle doit échouer sur le défaut réellement livré en production."""
    faulty = "<div class='table-scroll'>\n  <table></table>\n</div>\n</div>\n"
    assert _imbalances(faulty) == {"div": (1, 2)}


def test_exclusive_branches_are_not_counted_twice() -> None:
    """Deux branches exclusives ouvrant la même balise ne sont pas un défaut."""
    branching = "{% if edit %}<form action='/edit'>{% else %}<form action='/new'>{% endif %}</form>"
    assert _imbalances(branching) == {}
