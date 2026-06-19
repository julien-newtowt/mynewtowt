"""Décompte des jours ouvrables pour les absences sédentaires (lot L3).

Calcul pur (sans DB) du nombre de **jours ouvrables** d'une absence (du lundi
au samedi inclus ; seul le dimanche est exclu), en décomptant les
demi-journées de début/fin. Convention transport/maritime, décision de
cadrage : décompte en jours **ouvrables**, pas de RTT. Les jours fériés ne
sont pas gérés en v1 (à paramétrer ultérieurement).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

# Seul le dimanche (6) n'est pas ouvrable. Lundi..samedi (0..5) sont ouvrables.
_NON_WORKING = {6}


def count_business_days(
    start: date,
    end: date,
    *,
    half_day_start: bool = False,
    half_day_end: bool = False,
) -> Decimal:
    """Nombre de jours **ouvrables** entre ``start`` et ``end`` (bornes incluses).

    Décompte une demi-journée si ``half_day_start`` / ``half_day_end`` est
    posé (uniquement lorsque la borne concernée est un jour ouvrable).
    Lève ``ValueError`` si ``end < start``.
    """
    if end < start:
        raise ValueError("la date de fin précède la date de début")

    full = 0
    cursor = start
    while cursor <= end:
        if cursor.weekday() not in _NON_WORKING:
            full += 1
        cursor += timedelta(days=1)

    total = Decimal(full)
    if half_day_start and start.weekday() not in _NON_WORKING:
        total -= Decimal("0.5")
    # Si début == fin, une seule borne peut être en demi-journée.
    if half_day_end and end != start and end.weekday() not in _NON_WORKING:
        total -= Decimal("0.5")

    return total if total >= 0 else Decimal("0")
