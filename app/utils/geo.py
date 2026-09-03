"""Conversion de coordonnées géographiques décimales → DMS.

Déplacé depuis ``app.services.mrv_compute`` (legacy MRV supprimé) : fonction
pure sans dépendance DB/modèle, consommée par ``app.services.mrv_dataset``
pour les exports réglementaires OVDLA/OVDBR.
"""

from __future__ import annotations

from decimal import Decimal


def decimal_to_dms(value: float, *, is_lat: bool) -> tuple[int, Decimal, str]:
    """Convertit une coordonnée décimale en (degrés, minutes, hémisphère).

    Minutes arrondies à 3 décimales (format attendu par les exports DNV).
    """
    positive, negative = ("N", "S") if is_lat else ("E", "W")
    hemi = positive if value >= 0 else negative
    av = abs(value)
    deg = int(av)
    minutes = (Decimal(str(av)) - Decimal(deg)) * Decimal("60")
    return deg, minutes.quantize(Decimal("0.001")), hemi
