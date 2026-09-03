"""Support applicatif (« Assistance ») — logique métier des demandes.

⚠️ NE PAS CONFONDRE avec ``app.services.tickets`` (module ``tickets``), qui gère
les incidents d'exploitation portuaire en escale. Ici : les difficultés
rencontrées **dans MyTOWT lui-même**.

**Aucun import croisé** avec ``services.tickets`` — règle de différenciation
vérifiée par ``tests/unit/test_support_no_confusion.py``.

Vocabulaire (spec §1) : l'objet est une **demande d'assistance**, jamais un
« ticket » côté interface. Les fonctions publiques disent donc ``request``.

Ce module n'expose AUCUN libellé en clair : il livre des **clés i18n**, que le
gabarit traduit via ``t()``. Le module ``tickets`` porte ses 19 libellés en
français dur — on ne reproduit pas ça (des marins vietnamiens doivent pouvoir
signaler un problème dans leur langue).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.support import SupportTicket, SupportTicketAttachment, SupportTicketComment
from app.services.planning import ensure_utc

# ---------------------------------------------------------------------------
# Nomenclature — clés i18n, pas de libellé en clair
# ---------------------------------------------------------------------------

KINDS: tuple[str, ...] = ("bug", "question", "amelioration")
KIND_LABEL_KEYS: dict[str, str] = {k: f"sup_kind_{k}" for k in KINDS}

SEVERITIES: tuple[str, ...] = ("bloquant", "genant", "mineur")
SEVERITY_LABEL_KEYS: dict[str, str] = {s: f"sup_sev_{s}" for s in SEVERITIES}

STATUSES: tuple[str, ...] = (
    "nouveau",
    "en_cours",
    "en_attente_utilisateur",
    "resolu",
    "clos",
    "rejete",
)
STATUS_LABEL_KEYS: dict[str, str] = {s: f"sup_status_{s}" for s in STATUSES}

#: États terminaux : point de départ du compte à rebours d'archivage.
TERMINAL_STATUSES: tuple[str, ...] = ("clos", "rejete")

#: Transitions autorisées (spec §7).
_TRANSITIONS: dict[str, set[str]] = {
    "nouveau": {"en_cours", "rejete"},
    "en_cours": {"en_attente_utilisateur", "resolu", "rejete"},
    "en_attente_utilisateur": {"en_cours", "resolu"},
    "resolu": {"clos", "en_cours"},  # une correction incomplète se rouvre
    "clos": set(),
    "rejete": set(),
}

#: Les deux SEULES transitions que le demandeur peut déclencher lui-même :
#: répondre à une demande d'information, et signaler que ce n'est pas corrigé.
#: Tout le reste est réservé à ``administrateur`` (garde dans le routeur).
REPORTER_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("en_attente_utilisateur", "en_cours"),
        ("resolu", "en_cours"),
    }
)

#: Délai d'archivage. Une demande terminale plus vieille que ça sort de la vue
#: courante — **sans être supprimée** (cf. ``is_archived``).
ARCHIVE_AFTER_DAYS = 90

#: Pièces jointes par demande (spec §8). Borne le stockage sans gêner l'usage.
MAX_ATTACHMENTS = 5

#: Sous-répertoire de ``settings.upload_dir`` confié à ``safe_files``.
UPLOAD_SUBDIR = "support"


class SupportError(Exception):
    """Erreur métier du support applicatif."""


class InvalidSupportTransition(SupportError):
    pass


@dataclass(frozen=True)
class SupportStats:
    open_count: int
    new_count: int
    blocking_open: int
    oldest_open_days: int | None


# ---------------------------------------------------------------------------
# Contexte technique — assainissement de la seule valeur venant du client
# ---------------------------------------------------------------------------


def sanitize_page_url(value: str | None) -> str | None:
    """Ne garde qu'un chemin **relatif au site**, sinon ``None``.

    ``page_url`` est la seule donnée de contexte technique qui transite par le
    client (champ caché pré-rempli par le lien « Signaler un problème »). Non
    filtrée, elle devient un vecteur de redirection ouverte et une injection
    potentielle dans le gabarit.

    Refusé : tout ce qui porte un schéma (``http:``, ``javascript:``, ``data:``),
    les URL protocol-relative (``//hote``), et tout ce qui ne commence pas par
    ``/``.
    """
    if not value:
        return None
    candidate = value.strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return None
    # Un « : » avant le premier « / » signalerait un schéma ; ici la chaîne
    # commence déjà par « / », mais on refuse les caractères de contrôle et les
    # retours à la ligne, qui n'ont rien à faire dans un chemin.
    if any(ch in candidate for ch in ("\r", "\n", "\t")):
        return None
    return candidate[:500]


# ---------------------------------------------------------------------------
# Archivage — état DÉRIVÉ (ni colonne, ni tâche de fond)
# ---------------------------------------------------------------------------


def terminal_at(ticket: SupportTicket) -> datetime | None:
    """Horodatage d'entrée en état terminal, aware UTC — ``None`` si non terminale."""
    if ticket.status == "clos":
        return ensure_utc(ticket.closed_at)
    if ticket.status == "rejete":
        return ensure_utc(ticket.rejected_at)
    return None


def archive_cutoff(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) - timedelta(days=ARCHIVE_AFTER_DAYS)


def is_archived(ticket: SupportTicket, *, now: datetime | None = None) -> bool:
    """``True`` si la demande est sortie de la vue courante.

    Critère : **état terminal ET** plus de ``ARCHIVE_AFTER_DAYS`` jours depuis
    l'entrée dans cet état. L'âge seul ne suffit pas — une demande ouverte
    depuis deux ans n'est jamais archivée.

    Passe par ``ensure_utc`` : les colonnes ``DateTime(timezone=True)``
    reviennent aware sous Postgres mais **naïves sous SQLite** (les tests), et
    comparer naïf à aware lève un ``TypeError``. Ce défaut a déjà été rencontré
    dans ``voyage_track.leg_window``.
    """
    at = terminal_at(ticket)
    if at is None:
        return False
    return at < archive_cutoff(now)


def _archived_clause(now: datetime | None = None):
    """Même règle que :func:`is_archived`, exprimée en SQL.

    ⚠️ Deux expressions de la même règle : un test vérifie qu'elles **ne
    divergent pas** (``test_sql_and_python_archive_predicates_agree``).
    """
    at = func.coalesce(SupportTicket.closed_at, SupportTicket.rejected_at)
    return and_(
        SupportTicket.status.in_(TERMINAL_STATUSES),
        at.isnot(None),
        at < archive_cutoff(now),
    )


# ---------------------------------------------------------------------------
# Référence séquentielle
# ---------------------------------------------------------------------------


def format_reference(year: int, number: int) -> str:
    return f"SUP-{year}-{number:04d}"


async def _next_seq_number(db: AsyncSession, year: int) -> int:
    """``MAX(seq_number) + 1`` pour l'année — jamais ``COUNT + 1``.

    Compter recycle : si 0001 est supprimée alors que 0002 existe, ``count + 1``
    redonne 0002. C'est le défaut corrigé côté connaissements.
    """
    stmt = select(func.coalesce(func.max(SupportTicket.seq_number), 0)).where(
        SupportTicket.seq_year == year
    )
    return int((await db.execute(stmt)).scalar_one()) + 1


# ---------------------------------------------------------------------------
# Création
# ---------------------------------------------------------------------------


async def create_request(
    db: AsyncSession,
    *,
    reporter_id: int,
    reporter_role: str,
    kind: str,
    severity: str,
    title: str,
    description: str,
    page_url: str | None = None,
    http_referer: str | None = None,
    user_agent: str | None = None,
    app_version: str | None = None,
    occurred_at: datetime | None = None,
) -> SupportTicket:
    """Crée une demande d'assistance. ``await db.flush()`` seulement.

    La référence est ``SUP-{année}-{MAX(seq_number) + 1}``, avec vérification de
    disponibilité **avant** insertion (cf. commentaire dans le corps).

    ⚠️ **Ce que cela garantit, et ce que cela ne garantit pas.** La séquence ne
    recycle pas les numéros, et la contrainte d'unicité
    ``(seq_year, seq_number)`` interdit tout doublon en base. En revanche, sous
    concurrence réelle — deux créations entrelacées entre le calcul du ``MAX`` et
    l'insertion — le second perdant reçoit une erreur et doit réessayer.

    Ce résidu n'est pas rattrapable dans la même session : une fois le flush en
    échec, la session entière exige un rollback (mesuré), donc aucune reprise
    interne n'est possible. Le choix assumé est de préserver l'intégrité plutôt
    que de masquer l'échec. En pratique une demande d'assistance est saisie à la
    main, une à la fois — très loin du régime concurrent.

    À comparer au module ``tickets``, qui tire un suffixe aléatoire sur 65 536
    valeurs par an **sans aucune reprise** : ~50 % de risque de collision à 300
    tickets/an, pour le même symptôme.
    """
    if kind not in KINDS:
        raise SupportError(f"type inconnu : {kind}")
    if severity not in SEVERITIES:
        raise SupportError(f"gravité inconnue : {severity}")
    if not title.strip() or not description.strip():
        raise SupportError("titre et description obligatoires")

    year = datetime.now(UTC).year
    for _attempt in range(5):
        number = await _next_seq_number(db, year)
        candidate = format_reference(year, number)
        # Disponibilité vérifiée AVANT l'insertion — et non « on insère, on
        # rattrape l'IntegrityError ». Mesuré : un flush en échec met la session
        # ENTIÈRE en état « rollback requis », et un ``begin_nested`` n'en
        # protège pas quand l'objet fautif est un INSERT en attente. Le patron
        # `assign_bl_number` (mutation en place dans un savepoint) échoue sur ce
        # cas — sa boucle de reprise n'a jamais été exercée par un test.
        taken = (
            await db.execute(select(SupportTicket.id).where(SupportTicket.reference == candidate))
        ).first()
        if taken is not None:
            continue  # référence incohérente avec la séquence → on avance
        ticket = SupportTicket(
            reference=candidate,
            seq_year=year,
            seq_number=number,
            reporter_id=reporter_id,
            reporter_role=reporter_role,
            kind=kind,
            severity=severity,
            title=title.strip()[:200],
            description=description.strip(),
            status="nouveau",
            page_url=sanitize_page_url(page_url),
            http_referer=(http_referer or "").strip()[:500] or None,
            user_agent=(user_agent or "").strip()[:400] or None,
            app_version=(app_version or "").strip()[:20] or None,
            occurred_at=ensure_utc(occurred_at),
        )
        db.add(ticket)
        await db.flush()
        return ticket
    raise SupportError("impossible d'attribuer une référence après 5 tentatives")


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def allowed_targets(current: str) -> set[str]:
    return set(_TRANSITIONS.get(current, set()))


def assert_transition(current: str, target: str) -> None:
    if target not in _TRANSITIONS.get(current, set()):
        raise InvalidSupportTransition(f"{current} → {target} interdit")


def is_reporter_transition(current: str, target: str) -> bool:
    """``True`` si le DEMANDEUR peut déclencher cette transition lui-même."""
    return (current, target) in REPORTER_TRANSITIONS


async def change_status(
    db: AsyncSession,
    ticket: SupportTicket,
    new_status: str,
    *,
    resolution: str | None = None,
) -> SupportTicket:
    """Applique une transition. ``resolution`` est **obligatoire** au rejet."""
    assert_transition(ticket.status, new_status)
    if new_status == "rejete" and not (resolution or "").strip():
        raise SupportError("un rejet exige un motif")

    now = datetime.now(UTC)
    ticket.status = new_status
    if new_status == "en_cours" and ticket.triaged_at is None:
        ticket.triaged_at = now
    if new_status == "resolu":
        ticket.resolved_at = now
        cleaned = (resolution or "").strip()
        if cleaned:
            ticket.resolution = cleaned
    elif new_status == "clos":
        ticket.closed_at = now
    elif new_status == "rejete":
        ticket.rejected_at = now
        ticket.resolution = (resolution or "").strip()
    elif new_status == "en_cours":
        # Réouverture : on efface la résolution, pas l'historique (commentaires).
        ticket.resolved_at = None
    await db.flush()
    return ticket


async def assign(db: AsyncSession, ticket: SupportTicket, user_id: int | None) -> SupportTicket:
    ticket.assigned_to_id = user_id
    if ticket.status == "nouveau" and user_id is not None:
        ticket.status = "en_cours"
        ticket.triaged_at = ticket.triaged_at or datetime.now(UTC)
    await db.flush()
    return ticket


# ---------------------------------------------------------------------------
# Commentaires et pièces jointes
# ---------------------------------------------------------------------------


async def add_comment(
    db: AsyncSession,
    ticket: SupportTicket,
    *,
    body: str,
    author_id: int | None,
    author_name: str | None,
    is_internal: bool = False,
) -> SupportTicketComment:
    if not body.strip():
        raise SupportError("commentaire vide")
    comment = SupportTicketComment(
        support_ticket_id=ticket.id,
        author_id=author_id,
        author_name=author_name,
        body=body.strip(),
        is_internal=is_internal,
    )
    db.add(comment)
    await db.flush()
    return comment


def visible_comments(ticket: SupportTicket, *, is_admin: bool) -> list[SupportTicketComment]:
    """Commentaires visibles par le lecteur.

    ⚠️ Un commentaire interne servi au demandeur serait une fuite : ce filtre
    est la seule barrière, le gabarit s'appuie dessus.
    """
    if is_admin:
        return list(ticket.comments)
    return [c for c in ticket.comments if not c.is_internal]


async def add_attachment(
    db: AsyncSession,
    ticket: SupportTicket,
    *,
    content: bytes,
    original_name: str,
    uploaded_by_id: int | None,
) -> SupportTicketAttachment:
    """Valide et stocke une pièce jointe via ``safe_files``.

    Refuse si la demande est terminale, ou si le plafond est atteint.
    """
    from app.services.safe_files import save_upload

    if ticket.status in TERMINAL_STATUSES:
        raise SupportError("demande terminée : ajout de pièce jointe impossible")
    # COUNT plutôt que ``len(ticket.attachments)`` : la relation peut être restée
    # paresseuse, et y toucher en contexte async lève ``MissingGreenlet``. Le
    # plafond ne doit pas dépendre de la façon dont l'objet a été chargé.
    existing = (
        await db.execute(
            select(func.count())
            .select_from(SupportTicketAttachment)
            .where(SupportTicketAttachment.support_ticket_id == ticket.id)
        )
    ).scalar_one()
    if existing >= MAX_ATTACHMENTS:
        raise SupportError(f"plafond de {MAX_ATTACHMENTS} pièces jointes atteint")

    rel_path, mime = save_upload(content, original_name, subdir=UPLOAD_SUBDIR)
    att = SupportTicketAttachment(
        support_ticket_id=ticket.id,
        file_path=rel_path,
        original_name=original_name[:255],
        file_mime=mime,
        size_bytes=len(content),
        uploaded_by_id=uploaded_by_id,
    )
    db.add(att)
    await db.flush()
    return att


# ---------------------------------------------------------------------------
# Cloisonnement de lecture
# ---------------------------------------------------------------------------


def can_view(ticket: SupportTicket, *, user_id: int, is_admin: bool) -> bool:
    """Le demandeur voit la sienne ; ``administrateur`` voit toutes.

    ⚠️ La matrice de permissions ne sait PAS exprimer « les siennes » vs
    « toutes » : cette règle vit ici et dans le routeur, pas dans
    ``permissions.py``.
    """
    return is_admin or ticket.reporter_id == user_id


def can_download_attachment(ticket: SupportTicket, *, user_id: int, is_admin: bool) -> bool:
    """Téléchargement : demandeur ou administrateur — **jamais** le simple
    porteur de ``support:C``.

    Une capture d'écran est une exfiltration potentielle : elle peut contenir
    des données d'un autre module (finance, RH). Le droit de consulter le module
    ne donne donc pas le droit d'ouvrir les pièces d'autrui.

    ⚠️ **Où la protection s'applique vraiment.** Dans le routeur actuel, un tiers
    est déjà arrêté en amont par ``_load_or_404`` (qui appelle :func:`can_view`) :
    cette fonction est donc une **seconde barrière**, pas la barrière portante.
    Constaté au sabotage — la neutraliser ne fait tomber aucun test, alors que
    neutraliser :func:`can_view` les fait tomber tous.

    Elle est conservée délibérément, pour qu'un futur chemin d'accès qui ne
    passerait pas par ``_load_or_404`` reste couvert. Mais ne pas s'y fier seule,
    et ne pas croire qu'un test vert sur le téléchargement la valide.
    """
    return can_view(ticket, user_id=user_id, is_admin=is_admin)


def is_read_only(ticket: SupportTicket, *, now: datetime | None = None) -> bool:
    """Une demande archivée ou terminale n'accepte plus d'écriture métier."""
    return ticket.status in TERMINAL_STATUSES or is_archived(ticket, now=now)


# ---------------------------------------------------------------------------
# Requêtes
# ---------------------------------------------------------------------------


async def list_requests(
    db: AsyncSession,
    *,
    viewer_id: int,
    is_admin: bool,
    archived: bool = False,
    status: str | None = None,
    kind: str | None = None,
    severity: str | None = None,
    now: datetime | None = None,
) -> list[SupportTicket]:
    """Liste cloisonnée. ``archived=False`` = vue courante, ``True`` = archives."""
    stmt = select(SupportTicket)
    if not is_admin:
        stmt = stmt.where(SupportTicket.reporter_id == viewer_id)
    clause = _archived_clause(now)
    stmt = stmt.where(clause) if archived else stmt.where(~clause)
    if status:
        stmt = stmt.where(SupportTicket.status == status)
    if kind:
        stmt = stmt.where(SupportTicket.kind == kind)
    if severity:
        stmt = stmt.where(SupportTicket.severity == severity)
    stmt = stmt.order_by(SupportTicket.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


async def get_by_reference(db: AsyncSession, ref: str) -> SupportTicket | None:
    """Charge une demande AVEC ses collections.

    ``selectinload`` est indispensable : en contexte async, toucher une relation
    restée paresseuse lève ``MissingGreenlet``. Les charger ici évite de le
    redécouvrir dans chaque appelant (et dans chaque gabarit).
    """
    stmt = (
        select(SupportTicket)
        .where(SupportTicket.reference == ref)
        .options(
            selectinload(SupportTicket.comments),
            selectinload(SupportTicket.attachments),
        )
        # `populate_existing` force le rafraîchissement des collections déjà
        # chargées pour cet objet dans la session. Sans lui, un appelant qui
        # charge la demande, ajoute un commentaire, puis la recharge, obtient la
        # collection PÉRIMÉE de l'identity map — un piège discret, invisible en
        # production (une session par requête) mais bien réel dès qu'un même
        # flux enchaîne lecture, écriture et relecture.
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def stats(db: AsyncSession) -> SupportStats:
    """Compteurs pour l'administrateur (badge « nouvelles » + petit tableau de bord)."""
    rows = list((await db.execute(select(SupportTicket))).scalars().all())
    open_rows = [t for t in rows if t.status not in TERMINAL_STATUSES]
    new_count = sum(1 for t in rows if t.status == "nouveau")
    blocking_open = sum(1 for t in open_rows if t.severity == "bloquant")
    oldest_open_days = None
    if open_rows:
        oldest = min(ensure_utc(t.created_at) for t in open_rows)
        oldest_open_days = (datetime.now(UTC) - oldest).days
    return SupportStats(
        open_count=len(open_rows),
        new_count=new_count,
        blocking_open=blocking_open,
        oldest_open_days=oldest_open_days,
    )
