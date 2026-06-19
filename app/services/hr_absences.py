"""Décompte des jours ouvrés pour les absences sédentaires (lot L3).

Calcul pur (sans DB) du nombre de jours ouvrés d'une absence, en
décomptant les demi-journées de début/fin. Les jours fériés ne sont pas
gérés en v1 (à paramétrer ultérieurement selon la convention transport /
maritime — cf. cahier des charges §13, question ouverte n°2).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

# 0 = lundi … 4 = vendredi sont ouvrés ; 5 = samedi, 6 = dimanche non.
_WEEKEND = {5, 6}


def count_business_days(
    start: date,
    end: date,
    *,
    half_day_start: bool = False,
    half_day_end: bool = False,
) -> Decimal:
    """Nombre de jours ouvrés entre ``start`` et ``end`` (bornes incluses).

    Décompte une demi-journée si ``half_day_start`` / ``half_day_end`` est
    posé (uniquement lorsque la borne concernée tombe un jour ouvré).
    Lève ``ValueError`` si ``end < start``.
    """
    if end < start:
        raise ValueError("la date de fin précède la date de début")

    full = 0
    cursor = start
    while cursor <= end:
        if cursor.weekday() not in _WEEKEND:
            full += 1
        cursor += timedelta(days=1)

    total = Decimal(full)
    if half_day_start and start.weekday() not in _WEEKEND:
        total -= Decimal("0.5")
    # Si début == fin, une seule borne peut être en demi-journée.
    if half_day_end and end != start and end.weekday() not in _WEEKEND:
        total -= Decimal("0.5")

    return total if total >= 0 else Decimal("0")
