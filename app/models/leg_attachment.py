"""ONB-03 — pièces jointes rattachées à un leg (documents reçus du bord /
agent d'escale).

Reprise V2 (``OnboardAttachment``) : le capitaine et l'escale déposent les
documents reçus (BL signés, lettres de protestation, constats, factures
agent, photos…) catégorisés. Les fichiers sont stockés via
``services.safe_files`` (validation extension/taille/magic + nom aléatoire) ;
la table ne porte que les métadonnées + le chemin relatif.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Catégories de pièces jointes (parité V2 — 8 catégories).
LEG_ATTACHMENT_CATEGORIES: tuple[str, ...] = (
    "port_agent",  # documents reçus de l'agent d'escale
    "bl_signed",  # connaissements signés
    "letter_protest",  # lettre de protestation (LOP)
    "survey",  # constats / rapports d'expertise
    "customs",  # documents douaniers
    "invoice",  # factures (agent, fournisseurs)
    "photo",  # photos cargo / avaries
    "other",  # divers
)

# Sous-ensemble « documents agent d'escale » (zone filtrée de l'écran).
PORT_AGENT_CATEGORIES: tuple[str, ...] = ("port_agent", "bl_signed", "letter_protest")


class LegAttachment(Base):
    """Fichier catégorisé rattaché à un leg (métadonnées + chemin relatif)."""

    __tablename__ = "leg_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(
        ForeignKey("legs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="other")
    label: Mapped[str | None] = mapped_column(String(200))
    original_name: Mapped[str | None] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_mime: Mapped[str | None] = mapped_column(String(80))
    file_size: Mapped[int | None] = mapped_column(Integer)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    uploaded_by_name: Mapped[str | None] = mapped_column(String(200))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
