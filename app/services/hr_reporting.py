"""Helpers de reporting RH — lot L6 du SIRH.

Fonctions pures (calcul d'âge, tranche d'âge, taux de turnover, ancienneté)
réutilisées par la page ``/rh/reporting``. Les agrégations DB restent dans
le routeur. Voir ``docs/strategy/CAHIER_DES_CHARGES_SIRH.md`` module RH-8.
"""

from __future__ import annotations

from datetime import date

AGE_BRACKETS: tuple[str, ...] = ("<25", "25-34", "35-44", "45-54", "55+")


def age_on(birth_date: date, on: date | None = None) -> int:
    ref = on or date.today()
    years = ref.year - birth_date.year
    if (ref.month, ref.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def age_bracket(age: int) -> str:
    if age < 25:
        return "<25"
    if age < 35:
        return "25-34"
    if age < 45:
        return "35-44"
    if age < 55:
        return "45-54"
    return "55+"


def seniority_years(entry_date: date, on: date | None = None) -> float:
    ref = on or date.today()
    return round((ref - entry_date).days / 365.25, 1)


def turnover_rate(exits: int, headcount: int) -> float:
    """Taux de rotation = sorties / effectif moyen, en % arrondi à 0,1."""
    if headcount <= 0:
        return 0.0
    return round(exits / headcount * 100, 1)
