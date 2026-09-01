"""Journal des événements Stripe traités — idempotence au niveau **événement**.

L'idempotence du module « Vente à bord » reposait entièrement sur l'état métier
(``OnboardSale.cashbox_movement_id`` posé ⇒ ne pas re-encaisser). C'est correct
tant que l'événement porte sur une vente, mais cela a deux limites :

* la garde lit un état **après** l'avoir chargé, sans verrou : deux livraisons
  concurrentes du même événement pouvaient la franchir toutes les deux ;
* elle ne couvre que les types d'événements déjà branchés — tout futur type
  (remboursement, litige) devrait réinventer sa propre protection.

Enregistrer l'``event.id`` **avant** tout traitement, sous contrainte d'unicité,
règle les deux cas d'un coup : la seconde livraison échoue à l'insertion et
repart en 200 sans avoir rien touché. C'est la protection recommandée par
Stripe, dont la livraison est « au moins une fois ».
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StripeWebhookEvent(Base):
    """Un événement Stripe reçu et accepté (une ligne = un ``event.id``)."""

    __tablename__ = "stripe_webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Identifiant Stripe (`evt_…`) — la clé d'idempotence.
    event_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StripeWebhookEvent {self.event_id} {self.event_type}>"
