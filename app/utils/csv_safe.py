"""Neutralisation de l'injection de formules dans les exports CSV.

Un tableur (Excel/LibreOffice) interprète une cellule commençant par ``=``,
``+``, ``-``, ``@``, une tabulation ou un retour chariot comme une **formule**.
Si une telle cellule contient une valeur saisie par un utilisateur (nom de
navire, de port, note…), elle peut exfiltrer des données ou, via DDE hérité,
exécuter une commande à l'ouverture du fichier.

``sanitize_cell`` préfixe une apostrophe aux cellules **texte** à risque. Les
valeurs numériques (``Decimal``/``float``/``int``) sont laissées intactes — un
nombre négatif (``-150.00``) ne doit jamais être corrompu en ``'-150.00``.
"""

from __future__ import annotations

from typing import Any

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value: Any) -> Any:
    """Préfixe une apostrophe si ``value`` est une chaîne déclenchant une formule.

    Les types non-``str`` (nombres, dates) sont renvoyés tels quels.
    """
    if isinstance(value, str) and value[:1] in _FORMULA_PREFIXES:
        return "'" + value
    return value


def sanitize_row(row: list[Any]) -> list[Any]:
    """Applique ``sanitize_cell`` à chaque cellule d'une ligne."""
    return [sanitize_cell(c) for c in row]
