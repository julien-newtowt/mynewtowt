"""Support applicatif (« Assistance ») — demandes d'assistance sur le LOGICIEL.

⚠️ NE PAS CONFONDRE AVEC ``app.models.ticket`` (module ``tickets``), qui traite
les incidents d'EXPLOITATION PORTUAIRE pendant une escale (avarie, avitaillement
urgent, formalité douanière…). Ici il s'agit exclusivement des difficultés
rencontrées **dans MyTOWT lui-même**.

Différenciation stricte (cf. ``docs/strategy/SPEC_SUPPORT_TICKETING.md`` §1) :
préfixe ``Support`` obligatoire sur les classes et les tables, référence
``SUP-…`` et non ``TKT-…``, aucun import croisé entre les deux modules.

Workflow : nouveau → en_cours → resolu → clos
                        ↕ en_attente_utilisateur
           nouveau|en_cours → rejete (motif obligatoire)

L'archivage (90 j après l'entrée en état terminal) est un état **dérivé** — ni
colonne ni tâche de fond, cf. ``app.services.support.is_archived``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Référence affichée : ``SUP-{seq_year}-{seq_number:04d}``.
    #
    # La séquence est portée par DEUX ENTIERS explicites, et non déduite d'un
    # COUNT() des lignes existantes, parce que compter RECYCLE : si 0001 est
    # supprimée alors que 0002 existe, ``count + 1`` redonne 0002. C'est le
    # défaut corrigé côté connaissements (« la numérotation cesse de se
    # recycler »). On prend donc ``MAX(seq_number) + 1``.
    #
    # Reste un cas résiduel assumé : supprimer la DERNIÈRE demande de l'année
    # libère son numéro. La suppression est réservée à ``support:S``, et la
    # contrainte d'unicité ci-dessous fait échouer bruyamment une éventuelle
    # collision au lieu de produire un doublon silencieux.
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    seq_year: Mapped[int] = mapped_column(Integer, nullable=False)
    seq_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Auteur. ``reporter_role`` est FIGÉ à la création : le rôle d'un
    # utilisateur peut changer ensuite, le contexte de la demande ne doit pas
    # bouger avec lui.
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    reporter_role: Mapped[str] = mapped_column(String(30), nullable=False)

    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # bug|question|amelioration
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # bloquant|genant|mineur
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="nouveau", nullable=False)

    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    # Ce qui a été fait, ou pourquoi la demande est rejetée (obligatoire au rejet).
    resolution: Mapped[str | None] = mapped_column(Text)

    # RÉSERVÉ — place laissée pour l'ouverture aux clients (hors v1). Jamais
    # renseigné tant que le canal client n'est pas ouvert.
    client_id: Mapped[int | None] = mapped_column(ForeignKey("client_accounts.id"))

    # ── Contexte technique, renseigné CÔTÉ SERVEUR (cf. spec §5) ──
    # Rien ne capte les erreurs applicatives aujourd'hui (Sentry/OTel/Prometheus
    # sont configurés mais jamais initialisés, et il n'y a pas de handler 500) :
    # ces colonnes sont la seule chance de retrouver un bug à partir d'une
    # demande utilisateur.
    page_url: Mapped[str | None] = mapped_column(String(500))
    http_referer: Mapped[str | None] = mapped_column(String(500))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    app_version: Mapped[str | None] = mapped_column(String(20))
    # Quand l'UTILISATEUR dit que le problème est survenu (déclaratif).
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    triaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    comments: Mapped[list[SupportTicketComment]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="SupportTicketComment.created_at",
    )
    attachments: Mapped[list[SupportTicketAttachment]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="SupportTicketAttachment.created_at",
    )

    __table_args__ = (
        UniqueConstraint("seq_year", "seq_number", name="uq_support_tickets_seq"),
        Index("ix_support_tickets_status", "status"),
        Index("ix_support_tickets_status_severity", "status", "severity"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SupportTicket {self.reference} {self.kind}/{self.status}>"


class SupportTicketComment(Base):
    __tablename__ = "support_ticket_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    support_ticket_id: Mapped[int] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    author_name: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Note visible du seul administrateur. ⚠️ Le gabarit DOIT filtrer : un
    # commentaire interne affiché au demandeur serait une fuite.
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ticket: Mapped[SupportTicket] = relationship(back_populates="comments")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SupportTicketComment #{self.id} interne={self.is_internal}>"


class SupportTicketAttachment(Base):
    __tablename__ = "support_ticket_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    support_ticket_id: Mapped[int] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Chemin RELATIF retourné par ``services.safe_files.save_upload`` — le nom
    # sur disque est aléatoire, celui fourni par l'utilisateur n'y touche jamais.
    file_path: Mapped[str] = mapped_column(String(300), nullable=False)
    original_name: Mapped[str | None] = mapped_column(String(255))
    file_mime: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ticket: Mapped[SupportTicket] = relationship(back_populates="attachments")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SupportTicketAttachment #{self.id} {self.original_name}>"
