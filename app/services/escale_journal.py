"""Journal d'escale — timeline unifiée du dossier voyage (reprise UX Phase 2).

Agrège en LECTURE SEULE tout ce que le bord et la terre produisent sur un
leg — statut portuaire, événements SOF, opérations réelles, documents
cargo, transitions de connaissement, pièces jointes, tickets, sinistres,
verrous/clôture — en une chronologie unique. Aucune écriture : le journal
raconte, il ne modifie rien.

Deux fonctions publiques :
- ``build_journal`` : la timeline (liste de ``JournalEntry`` triée).
- ``sof_reconciliation`` : le rapprochement des deux registres SOF — les
  événements du bord (``SofEvent``) vs les opérations d'escale de la terre
  (``EscaleOperation``) — limité aux 7 actions synchronisables
  (``ESCALE_ACTION_TO_SOF``) : au-delà, la comparaison n'aurait pas de sens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim import Claim
from app.models.escale import ESCALE_ACTION_TO_SOF, EscaleOperation
from app.models.leg import Leg
from app.models.leg_attachment import LegAttachment
from app.models.sof_event import CargoDocument, SofEvent
from app.models.ticket import Ticket
from app.services.bl_workflow import batches_for_leg

# Types d'entrées — servent aussi de filtres à l'écran.
JOURNAL_KINDS: tuple[str, ...] = (
    "statut",
    "sof",
    "operation",
    "document",
    "bl",
    "pj",
    "ticket",
    "sinistre",
    "verrou",
)


@dataclass
class JournalEntry:
    at: datetime
    kind: str  # ∈ JOURNAL_KINDS
    label: str
    detail: str = ""
    badge: str | None = None  # texte court (« signé », « P1 »…)
    badge_tone: str = "neutral"  # ok | warn | error | info | neutral
    link: str | None = None
    sort_at: datetime = field(init=False)

    def __post_init__(self) -> None:
        # Les datetimes stockés peuvent être naïfs (SQLite en test) ou aware
        # (PostgreSQL) : la clé de tri est normalisée en naïf.
        self.sort_at = self.at.replace(tzinfo=None) if self.at.tzinfo else self.at


def _entry(entries: list[JournalEntry], at: datetime | None, **kwargs) -> None:
    """N'ajoute une entrée que si elle a un horodatage — jamais de date inventée."""
    if at is None:
        return
    entries.append(JournalEntry(at=at, **kwargs))


_BL_STATE_LABELS = {
    "draft": "projet généré",
    "client_validated": "validé par le client",
    "master_signed": "signé par le commandant",
    "final": "émis en définitif",
}


async def build_journal(db: AsyncSession, leg: Leg) -> list[JournalEntry]:
    """Chronologie unifiée du leg, triée du plus ancien au plus récent."""
    entries: list[JournalEntry] = []

    # ── Statut portuaire + verrous/clôture (portés par le leg) ──────────
    _entry(
        entries,
        leg.ata,
        kind="statut",
        label="ATA posée — navire à quai",
        detail="Statut portuaire",
        badge="statut",
        badge_tone="info",
    )
    _entry(
        entries,
        leg.atd,
        kind="statut",
        label="ATD posée — pilote départ",
        detail="Statut portuaire",
        badge="statut",
        badge_tone="info",
    )
    _entry(
        entries,
        leg.escale_locked_at,
        kind="verrou",
        label="Escale verrouillée",
        detail=(f"par {leg.escale_locked_by}" if leg.escale_locked_by else ""),
        badge="verrou",
        badge_tone="neutral",
    )
    _entry(
        entries,
        leg.closure_submitted_at,
        kind="verrou",
        label="Clôture de voyage soumise",
        detail="Étape 1/3 — commandant",
        badge="clôture",
        badge_tone="warn",
    )
    _entry(
        entries,
        leg.closure_reviewed_at,
        kind="verrou",
        label="Clôture validée par les Opérations",
        detail="Étape 2/3",
        badge="clôture",
        badge_tone="warn",
    )
    _entry(
        entries,
        leg.closure_approved_at,
        kind="verrou",
        label="Clôture approuvée — voyage terminé",
        detail="Étape 3/3 — manager",
        badge="clôture",
        badge_tone="ok",
    )

    # ── Événements SOF (bord) ────────────────────────────────────────────
    sof_events = (
        (await db.execute(select(SofEvent).where(SofEvent.leg_id == leg.id))).scalars().all()
    )
    for ev in sof_events:
        signed = ev.signed_at is not None
        _entry(
            entries,
            ev.occurred_at,
            kind="sof",
            label=f"SOF — {ev.event_type}" + (f" · {ev.label}" if ev.label else ""),
            detail=(
                "Signé" + (f" par {ev.signed_by_name}" if ev.signed_by_name else "")
                if signed
                else (f"Saisi par {ev.recorded_by_name}" if ev.recorded_by_name else "")
            ),
            badge=("signé" if signed else "à signer"),
            badge_tone=("ok" if signed else "warn"),
        )

    # ── Opérations d'escale réelles (terre) ─────────────────────────────
    operations = (
        (await db.execute(select(EscaleOperation).where(EscaleOperation.leg_id == leg.id)))
        .scalars()
        .all()
    )
    for op in operations:
        base = f"{op.action}" + (f" — {op.label}" if op.label else "")
        who = f"Intervenant : {op.intervenant}" if op.intervenant else op.operation_type
        _entry(
            entries,
            op.actual_start,
            kind="operation",
            label=f"Opération démarrée — {base}",
            detail=who,
            badge=(op.direction or "commun").lower(),
            badge_tone="info",
        )
        _entry(
            entries,
            op.actual_end,
            kind="operation",
            label=f"Opération terminée — {base}",
            detail=who,
            badge=(op.direction or "commun").lower(),
            badge_tone="ok",
        )

    # ── Documents cargo guidés (bord) ────────────────────────────────────
    docs = (
        (await db.execute(select(CargoDocument).where(CargoDocument.leg_id == leg.id)))
        .scalars()
        .all()
    )
    for doc in docs:
        ref = f" {doc.reference}" if doc.reference else ""
        _entry(
            entries,
            doc.issued_at,
            kind="document",
            label=f"Document émis — {doc.kind}{ref}",
            detail=(doc.party_name or ""),
            badge="émis",
            badge_tone="info",
        )
        _entry(
            entries,
            doc.signed_at,
            kind="document",
            label=f"Document signé — {doc.kind}{ref}",
            detail=(doc.signed_by_name or ""),
            badge="signé",
            badge_tone="ok",
        )

    # ── Connaissements — transitions dérivées des horodatages du lot ────
    for b in await batches_for_leg(db, leg_id=leg.id):
        num = b.bl_number or f"lot {b.id}"
        _entry(
            entries,
            b.bl_draft_at,
            kind="bl",
            label=f"BL {num} — projet généré",
            badge="draft",
            badge_tone="info",
        )
        _entry(
            entries,
            b.bl_client_validated_at,
            kind="bl",
            label=f"BL {num} — validé par le client",
            detail=(b.bl_client_validated_by or ""),
            badge="validé",
            badge_tone="warn",
        )
        _entry(
            entries,
            b.bl_signed_at,
            kind="bl",
            label=f"BL {num} — signé par le commandant",
            detail=(b.bl_signed_by_name or ""),
            badge="signé · gelé",
            badge_tone="ok",
        )
        _entry(
            entries,
            b.bl_issued_at,
            kind="bl",
            label=f"BL {num} — émis en définitif",
            badge="final",
            badge_tone="ok",
        )

    # ── Pièces jointes du leg ────────────────────────────────────────────
    attachments = (
        (await db.execute(select(LegAttachment).where(LegAttachment.leg_id == leg.id)))
        .scalars()
        .all()
    )
    for a in attachments:
        name = a.label or a.original_name or f"pièce {a.id}"
        _entry(
            entries,
            a.uploaded_at,
            kind="pj",
            label=f"Pièce jointe déposée — {name}",
            detail=f"catégorie {a.category}"
            + (f" · {a.uploaded_by_name}" if a.uploaded_by_name else ""),
            badge="pj",
            badge_tone="neutral",
        )

    # ── Tickets d'escale ─────────────────────────────────────────────────
    tickets = (await db.execute(select(Ticket).where(Ticket.leg_id == leg.id))).scalars().all()
    for t in tickets:
        _entry(
            entries,
            t.created_at,
            kind="ticket",
            label=f"Ticket ouvert — {t.title}",
            detail=f"{t.reference} · {t.category}",
            badge=t.priority,
            badge_tone=("error" if t.priority == "P1" else "warn"),
            link=f"/tickets/{t.reference}",
        )
        _entry(
            entries,
            t.resolved_at,
            kind="ticket",
            label=f"Ticket résolu — {t.title}",
            detail=t.reference,
            badge="résolu",
            badge_tone="ok",
            link=f"/tickets/{t.reference}",
        )

    # ── Sinistres ────────────────────────────────────────────────────────
    claims = (await db.execute(select(Claim).where(Claim.leg_id == leg.id))).scalars().all()
    for c in claims:
        _entry(
            entries,
            c.declared_at,
            kind="sinistre",
            label=f"Sinistre déclaré — {c.title}",
            detail=f"{c.reference} · {c.claim_type}",
            badge="sinistre",
            badge_tone="error",
            link=f"/claims/{c.id}",
        )

    entries.sort(key=lambda e: e.sort_at)
    return entries


def sof_reconciliation(operations: list[EscaleOperation], sof_events: list[SofEvent]) -> dict:
    """Rapproche les deux registres SOF sur les 7 actions synchronisables.

    - ``missing_on_board`` : types SOF attendus depuis une opération de la
      terre, absents du registre du bord.
    - ``missing_on_shore`` : types SOF présents au bord sans opération
      d'escale correspondante côté terre.
    Un type suffit (pas de comptage par occurrence) : le rapprochement
    signale une divergence à investiguer, il ne prétend pas l'expliquer.
    """
    mapped_types = set(ESCALE_ACTION_TO_SOF.values())
    expected = {
        ESCALE_ACTION_TO_SOF[op.action] for op in operations if op.action in ESCALE_ACTION_TO_SOF
    }
    present = {ev.event_type for ev in sof_events}
    return {
        "sof_total": len(sof_events),
        "sof_signed": sum(1 for ev in sof_events if ev.signed_at is not None),
        "ops_total": len(operations),
        "ops_synced": len(expected),
        "missing_on_board": sorted(expected - present),
        "missing_on_shore": sorted((present & mapped_types) - expected),
    }
