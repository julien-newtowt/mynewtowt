"""Parsing et validation des saisies numériques monétaires / quantitatives.

Point de passage **unique** pour toute valeur numérique venant d'un formulaire
avant écriture en base. Motivation, constatée à l'audit du 2026-08-27 :

``Decimal("nan")`` et ``Decimal("Infinity")`` sont des littéraux **valides** —
ils ne lèvent pas ``InvalidOperation``. Les parseurs qui se contentaient
d'attraper cette exception les laissaient donc passer, et les gardes en aval
ne les rattrapaient pas non plus (``Decimal("nan") == 0`` vaut ``False``, donc
un « montant non nul ? » réputé sûr était franchi). PostgreSQL ``numeric``
accepte ces valeurs spéciales, et ``SUM()`` sur une colonne qui en contient une
renvoie ``NaN`` : un seul mouvement suffisait à rendre définitivement
inexploitable le solde d'une caisse ou l'inventaire d'un navire — dans des
tables **append-only sans route de suppression**, donc sans correction possible
depuis l'IHM.

D'où la règle : on ne valide pas « ce n'est pas zéro », on valide **« c'est un
nombre fini et borné »**, et on le fait au plus près de la saisie comme au plus
près de l'écriture (les services revalident, cf. ``ensure_finite``).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Borne de garde volontairement large : elle n'exprime aucune règle métier (les
# seuils métier vivent dans les services), elle écarte seulement les valeurs
# absurdes et les notations exponentielles du type ``1e500`` qui débordent les
# colonnes ``Numeric(12, 2)`` / ``Numeric(14, 2)`` du schéma.
MAX_ABS = Decimal("1e10")

CENTS = Decimal("0.01")
QTY_STEP = Decimal("0.001")


class DecimalInputError(ValueError):
    """Saisie numérique invalide (message affichable à l'utilisateur)."""


def ensure_finite(value: Decimal, *, label: str = "valeur") -> Decimal:
    """Refuse ``NaN`` / ``Infinity`` et les valeurs hors borne de garde.

    À appeler dans les **services**, en plus du parsing des routeurs : une
    valeur peut arriver d'un import, d'un script ou d'un appelant interne qui
    n'est pas passé par ``parse_decimal``.
    """
    if not value.is_finite():
        raise DecimalInputError(f"{label.capitalize()} numérique invalide.")
    if abs(value) >= MAX_ABS:
        raise DecimalInputError(f"{label.capitalize()} hors limites.")
    return value


def parse_decimal(
    raw: str | None,
    *,
    label: str = "valeur",
    min_value: Decimal | None = None,
    quantize: Decimal | None = None,
) -> Decimal:
    """Convertit une saisie de formulaire en ``Decimal`` fini et borné.

    Accepte la virgule décimale et les espaces de groupement (saisie FR).
    ``min_value`` pose une borne basse inclusive ; ``quantize`` arrondit au pas
    donné (``CENTS`` pour un montant, ``QTY_STEP`` pour une quantité) en
    ``ROUND_HALF_UP`` — l'arrondi est fait **ici** plutôt que laissé à la base,
    dont le comportement diffère entre PostgreSQL et SQLite (tests).

    Lève ``DecimalInputError`` sur toute entrée non convertible, non finie,
    hors borne de garde ou sous ``min_value``.
    """
    text = (raw or "").strip().replace(",", ".").replace(" ", "").replace(" ", "")
    if not text:
        raise DecimalInputError(f"{label.capitalize()} requise.")
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        raise DecimalInputError(f"{label.capitalize()} numérique invalide.") from None
    ensure_finite(value, label=label)
    if quantize is not None:
        value = value.quantize(quantize, rounding=ROUND_HALF_UP)
    if min_value is not None and value < min_value:
        raise DecimalInputError(f"{label.capitalize()} hors bornes (minimum {min_value}).")
    return value
