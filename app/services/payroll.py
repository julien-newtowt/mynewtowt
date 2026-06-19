"""Helpers de période de paie (lot L4).

Fonctions pures autour de la période ``AAAA-MM`` : validation, bornes du
mois, période courante, chevauchement d'une absence avec le mois. La
logique d'accès DB (synchronisation des absences, verrouillage) reste dans
le routeur.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def is_valid_period(value: str | None) -> bool:
    return bool(value and _PERIOD_RE.match(value))


def current_period(today: date | None = None) -> str:
    d = today or date.today()
    return f"{d.year:04d}-{d.month:02d}"


def period_bounds(period: str) -> tuple[date, date]:
    """Premier et dernier jour du mois d'une période ``AAAA-MM``."""
    if not is_valid_period(period):
        raise ValueError(f"période invalide: {period!r} (attendu AAAA-MM)")
    year, month = (int(p) for p in period.split("-"))
    last_day = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def overlaps_period(start: date, end: date, period: str) -> bool:
    """Vrai si l'intervalle [start, end] recoupe le mois de ``period``."""
    first, last = period_bounds(period)
    return start <= last and end >= first


def shift_period(period: str, delta_months: int) -> str:
    """Décale une période de ``delta_months`` mois (ex. -1 = mois précédent)."""
    year, month = (int(p) for p in period.split("-"))
    index = (year * 12 + (month - 1)) + delta_months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"
