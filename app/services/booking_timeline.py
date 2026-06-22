"""Timeline d'expédition d'une réservation — 6 jalons du parcours marchandise.

Assemble, dans l'ordre chronologique du transport, les jalons à partir des
horodatages déjà présents en base :

    1. Arrivée de la marchandise au port de chargement  (booking.goods_arrived_pol_at)
    2. Chargement                                       (booking.loaded_at)
    3. Départ du navire du port de chargement           (leg.atd → fallback booking.at_sea_at)
    4. Arrivée au port de déchargement                  (leg.ata)
    5. Déchargement                                     (booking.discharged_at)
    6. Retrait de la marchandise par le client          (booking.delivered_at)

Chaque jalon non renseigné est marqué ``available=False`` : la page Label
Anemos propose alors de le compléter depuis la fiche réservation.
"""

from __future__ import annotations

from typing import Any


def build_shipment_timeline(booking: Any, leg: Any | None = None) -> list[dict]:
    """Retourne les 6 jalons ``{key, label, at, available}`` dans l'ordre."""
    atd = getattr(leg, "atd", None) if leg is not None else None
    ata = getattr(leg, "ata", None) if leg is not None else None

    raw: list[tuple[str, str, Any]] = [
        (
            "goods_arrived_pol",
            "Arrivée de la marchandise au port de chargement",
            getattr(booking, "goods_arrived_pol_at", None),
        ),
        ("loaded", "Chargement", getattr(booking, "loaded_at", None)),
        (
            "departed_pol",
            "Départ du navire du port de chargement",
            atd or getattr(booking, "at_sea_at", None),
        ),
        ("arrived_pod", "Arrivée au port de déchargement", ata),
        ("discharged", "Déchargement", getattr(booking, "discharged_at", None)),
        (
            "collected",
            "Retrait de la marchandise par le client",
            getattr(booking, "delivered_at", None),
        ),
    ]
    return [
        {"key": key, "label": label, "at": at, "available": at is not None}
        for key, label, at in raw
    ]
