"""Référentiel IMDG — classes & divisions de marchandises dangereuses (bilingue).

STO-08 — réintroduit le référentiel des classes IMO/IMDG (V2 : ``IMO_CLASSES``)
pour alimenter les sélecteurs « classe IMDG » au lieu d'une saisie de codes
bruts. Couvre les 9 classes et leurs divisions officielles, avec libellé FR/EN.

Source : Code maritime international des marchandises dangereuses (IMDG).
"""

from __future__ import annotations

# (code, libellé FR, libellé EN). Ordre officiel (classe.division croissante).
IMDG_CLASSES: list[dict[str, str]] = [
    {
        "code": "1.1",
        "label_fr": "Explosifs — risque d'explosion en masse",
        "label_en": "Explosives — mass explosion hazard",
    },
    {
        "code": "1.2",
        "label_fr": "Explosifs — risque de projection",
        "label_en": "Explosives — projection hazard",
    },
    {
        "code": "1.3",
        "label_fr": "Explosifs — risque d'incendie",
        "label_en": "Explosives — fire hazard",
    },
    {
        "code": "1.4",
        "label_fr": "Explosifs — risque mineur",
        "label_en": "Explosives — minor hazard",
    },
    {
        "code": "1.5",
        "label_fr": "Explosifs — très peu sensibles",
        "label_en": "Explosives — very insensitive",
    },
    {
        "code": "1.6",
        "label_fr": "Explosifs — extrêmement peu sensibles",
        "label_en": "Explosives — extremely insensitive",
    },
    {"code": "2.1", "label_fr": "Gaz inflammables", "label_en": "Flammable gases"},
    {
        "code": "2.2",
        "label_fr": "Gaz non inflammables, non toxiques",
        "label_en": "Non-flammable, non-toxic gases",
    },
    {"code": "2.3", "label_fr": "Gaz toxiques", "label_en": "Toxic gases"},
    {"code": "3", "label_fr": "Liquides inflammables", "label_en": "Flammable liquids"},
    {"code": "4.1", "label_fr": "Solides inflammables", "label_en": "Flammable solids"},
    {
        "code": "4.2",
        "label_fr": "Matières sujettes à inflammation spontanée",
        "label_en": "Substances liable to spontaneous combustion",
    },
    {
        "code": "4.3",
        "label_fr": "Matières dégageant des gaz inflammables au contact de l'eau",
        "label_en": "Substances emitting flammable gases on contact with water",
    },
    {"code": "5.1", "label_fr": "Matières comburantes", "label_en": "Oxidizing substances"},
    {"code": "5.2", "label_fr": "Peroxydes organiques", "label_en": "Organic peroxides"},
    {"code": "6.1", "label_fr": "Matières toxiques", "label_en": "Toxic substances"},
    {"code": "6.2", "label_fr": "Matières infectieuses", "label_en": "Infectious substances"},
    {"code": "7", "label_fr": "Matières radioactives", "label_en": "Radioactive material"},
    {"code": "8", "label_fr": "Matières corrosives", "label_en": "Corrosive substances"},
    {
        "code": "9",
        "label_fr": "Matières dangereuses diverses",
        "label_en": "Miscellaneous dangerous substances",
    },
]

# Codes valides (set) pour validation tolérante.
IMDG_CODES = frozenset(c["code"] for c in IMDG_CLASSES)


def imdg_label(code: str | None, lang: str = "fr") -> str:
    """Libellé d'une classe IMDG (``"3 — Liquides inflammables"``), code brut si
    inconnu, ``""`` si vide. ``lang`` non EN ⇒ FR."""
    if not code:
        return ""
    key = "label_en" if lang == "en" else "label_fr"
    for c in IMDG_CLASSES:
        if c["code"] == code:
            return f"{code} — {c[key]}"
    return code
