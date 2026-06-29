"""Conformité équipage — FLX-06 (action corrective direction).

Trois responsabilités :

1. **Snapshot Schengen persisté** — calcule la règle 90 j / 180 j et
   écrit le résultat sur les colonnes existantes de ``CrewMember``
   (``schengen_status``, ``schengen_days_in_window``,
   ``schengen_window_end``) afin que le statut soit persisté et
   historisable (au lieu d'un calcul volatil à l'affichage).
2. **Garde-fou passeport** — message de blocage si le passeport est
   expiré ou expire avant la fin d'embarquement prévue.
3. **Armement réglementaire** — ``vessel_readiness()`` indique, par
   navire et à une date donnée, quels postes clés sont pourvus/manquants
   parmi les assignments actifs.

Décompte Schengen — approximation V1 (assumée et documentée) pour un
marin ressortissant d'un pays tiers : compte comme « jour Schengen »
tout jour calendaire de la fenêtre glissante de 180 jours où le marin
était embarqué ET où le navire était au port dans un pays de l'espace
Schengen :

- du début d'embarquement jusqu'au départ du leg (ATD sinon ETD) si le
  port de départ est Schengen ;
- de l'arrivée du leg (ATA sinon ETA) jusqu'au débarquement (ou
  aujourd'hui si toujours à bord) si le port d'arrivée est Schengen ;
- la traversée (eaux internationales) ne compte pas.

Les ressortissants d'un pays Schengen ne sont pas soumis à la règle
(statut ``compliant``, compteurs à ``None``). Une nationalité inconnue
est traitée par prudence comme ressortissant d'un pays tiers.

Seuils (cf. commentaire models/crew.py) : > 90 j → ``non_compliant`` ;
> 80 j → ``warning`` ; sinon ``compliant``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crew import CrewAssignment, CrewMember
from app.models.leg import Leg
from app.models.port import Port

# Espace Schengen (29 États, situation 2026 — BG/RO pleinement membres
# depuis 2025, HR depuis 2023 ; IE et CY hors espace).
SCHENGEN_COUNTRIES = frozenset(
    {
        "AT",
        "BE",
        "BG",
        "CH",
        "CZ",
        "DE",
        "DK",
        "EE",
        "ES",
        "FI",
        "FR",
        "GR",
        "HR",
        "HU",
        "IS",
        "IT",
        "LI",
        "LT",
        "LU",
        "LV",
        "MT",
        "NL",
        "NO",
        "PL",
        "PT",
        "RO",
        "SE",
        "SI",
        "SK",
    }
)

SCHENGEN_WINDOW_DAYS = 180
SCHENGEN_MAX_DAYS = 90
SCHENGEN_WARNING_DAYS = 80

# Armement réglementaire — règle métier : capitaine, second, chef
# mécanicien, cuisinier, lieutenant, bosco. Mapping vers l'enum réel
# CREW_ROLES de routers/crew_router.py (colonne libre String(60)) :
# « cuisinier » → ``cook`` (seule valeur anglophone de l'enum), les cinq
# autres rôles existent tels quels (``capitaine``, ``second``,
# ``chef_mecanicien``, ``lieutenant``, ``bosco``).
REQUIRED_ROLES: tuple[str, ...] = (
    "capitaine",
    "second",
    "chef_mecanicien",
    "cook",
    "lieutenant",
    "bosco",
)

ROLE_LABELS: dict[str, str] = {
    "capitaine": "Capitaine",
    "second": "Second",
    "chef_mecanicien": "Chef mécanicien",
    "cook": "Cuisinier",
    "lieutenant": "Lieutenant",
    "bosco": "Bosco",
}

# Normalisation défensive : d'anciens écrans (cf. staff/crew/new.html)
# ont pu enregistrer des rôles en anglais — on les rabat sur l'enum
# canonique français utilisé par CREW_ROLES.
ROLE_SYNONYMS: dict[str, str] = {
    "captain": "capitaine",
    "master": "capitaine",
    "chief_mate": "second",
    "chief_officer": "second",
    "chief_engineer": "chef_mecanicien",
    "engineer": "chef_mecanicien",
    "cuisinier": "cook",
    "officer": "lieutenant",
    "bosun": "bosco",
    "boatswain": "bosco",
}


def normalize_role(value: str | None) -> str | None:
    """Rabat un rôle libre (FR/EN, casse variable) sur l'enum canonique."""
    if not value:
        return None
    cleaned = value.strip().lower()
    return ROLE_SYNONYMS.get(cleaned, cleaned)


def _as_utc(dt: datetime | None) -> datetime | None:
    """Coercition naïf→UTC pour comparer des datetimes hétérogènes."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _add_presence_days(
    presence: set[date],
    start_dt: datetime | None,
    end_dt: datetime | None,
    window_start: date,
    window_end: date,
) -> None:
    """Ajoute les jours calendaires [start, end] ∩ fenêtre au set."""
    if start_dt is None or end_dt is None or start_dt > end_dt:
        return
    cur = max(start_dt.date(), window_start)
    last = min(end_dt.date(), window_end)
    while cur <= last:
        presence.add(cur)
        cur += timedelta(days=1)


async def refresh_schengen_for_members(
    db: AsyncSession,
    members: list[CrewMember],
    *,
    today: date | None = None,
) -> None:
    """Recalcule et PERSISTE le statut Schengen des membres (FLX-06).

    Snapshot persisté : écrit ``schengen_status``,
    ``schengen_days_in_window`` et ``schengen_window_end`` (= date
    d'évaluation, fin de la fenêtre glissante de 180 j) sur chaque ligne
    ``CrewMember``, puis ``flush`` (jamais de commit — dependency
    ``get_db``).
    """
    if not members:
        return
    today = today or date.today()
    window_end = today
    window_start = today - timedelta(days=SCHENGEN_WINDOW_DAYS - 1)
    now = datetime.now(UTC)

    member_ids = [m.id for m in members if m.id is not None]
    assigns: list[CrewAssignment] = []
    if member_ids:
        assigns = list(
            (
                await db.execute(
                    select(CrewAssignment).where(
                        CrewAssignment.crew_member_id.in_(member_ids),
                        CrewAssignment.embark_at.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    leg_ids = {a.leg_id for a in assigns}
    legs: dict[int, Leg] = {}
    if leg_ids:
        legs = {
            leg.id: leg
            for leg in (await db.execute(select(Leg).where(Leg.id.in_(leg_ids)))).scalars()
        }
    port_ids = {
        p_id for leg in legs.values() for p_id in (leg.departure_port_id, leg.arrival_port_id)
    }
    ports: dict[int, Port] = {}
    if port_ids:
        ports = {
            p.id: p for p in (await db.execute(select(Port).where(Port.id.in_(port_ids)))).scalars()
        }

    assigns_by_member: dict[int, list[CrewAssignment]] = defaultdict(list)
    for a in assigns:
        assigns_by_member[a.crew_member_id].append(a)

    for member in members:
        nationality = (member.nationality or "").strip().upper()
        if nationality in SCHENGEN_COUNTRIES:
            # Ressortissant Schengen : la règle 90/180 ne s'applique pas.
            member.schengen_status = "compliant"
            member.schengen_days_in_window = None
            member.schengen_window_end = None
            continue

        presence: set[date] = set()
        for a in assigns_by_member.get(member.id, ()):
            leg = legs.get(a.leg_id)
            if leg is None:
                continue
            embark = _as_utc(a.embark_at)
            disembark = _as_utc(a.disembark_at) or now
            departure = _as_utc(leg.atd or leg.etd)
            arrival = _as_utc(leg.ata or leg.eta)

            dep_port = ports.get(leg.departure_port_id)
            arr_port = ports.get(leg.arrival_port_id)

            # Au port de départ : de l'embarquement au départ du navire.
            if dep_port and (dep_port.country or "").upper() in SCHENGEN_COUNTRIES:
                dep_end = min(d for d in (departure, disembark) if d is not None)
                _add_presence_days(presence, embark, dep_end, window_start, window_end)
            # Au port d'arrivée : de l'arrivée du navire au débarquement.
            if arr_port and (arr_port.country or "").upper() in SCHENGEN_COUNTRIES:
                arr_start = max(d for d in (arrival, embark) if d is not None)
                _add_presence_days(presence, arr_start, disembark, window_start, window_end)

        days = len(presence)
        if days > SCHENGEN_MAX_DAYS:
            status = "non_compliant"
        elif days > SCHENGEN_WARNING_DAYS:
            status = "warning"
        else:
            status = "compliant"

        member.schengen_status = status
        member.schengen_days_in_window = days
        member.schengen_window_end = window_end

    await db.flush()


async def refresh_member_schengen(
    db: AsyncSession,
    member: CrewMember,
    *,
    today: date | None = None,
) -> CrewMember:
    """Recalcule + persiste le snapshot Schengen d'un seul marin."""
    await refresh_schengen_for_members(db, [member], today=today)
    return member


def passport_blocking_reason(member: CrewMember, deadline: date | None) -> str | None:
    """Motif de blocage passeport (FR) ou ``None`` si rien à signaler.

    ``deadline`` = date de débarquement prévue (sinon embarquement,
    sinon aujourd'hui). Passeport non renseigné → pas de blocage (donnée
    manquante gérée manuellement, V1).
    """
    if member.passport_expires_at is None:
        return None
    today = date.today()
    if member.passport_expires_at < today:
        return f"Passeport expiré depuis le {member.passport_expires_at.strftime('%d/%m/%Y')}."
    if deadline and member.passport_expires_at < deadline:
        return (
            f"Passeport expirant le {member.passport_expires_at.strftime('%d/%m/%Y')}, "
            f"avant la fin d'embarquement prévue le {deadline.strftime('%d/%m/%Y')}."
        )
    return None


async def vessel_readiness(db: AsyncSession, vessel_id: int, at_date: date) -> dict:
    """Armement réglementaire d'un navire à une date donnée (lecture seule).

    Parcourt les assignments actifs couvrant ``at_date`` sur les legs du
    navire et vérifie la présence des postes clés ``REQUIRED_ROLES``
    (rôle à bord ``role_on_board``, repli sur le rôle du marin, normalisé
    via ``normalize_role``). V1 : informatif uniquement — ne bloque pas
    les legs.
    """
    leg_ids_subq = select(Leg.id).where(Leg.vessel_id == vessel_id)
    assigns = list(
        (
            await db.execute(
                select(CrewAssignment).where(
                    CrewAssignment.leg_id.in_(leg_ids_subq),
                    CrewAssignment.embark_at.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    active = [
        a
        for a in assigns
        if a.embark_at.date() <= at_date
        and (a.disembark_at is None or a.disembark_at.date() >= at_date)
    ]

    members: dict[int, CrewMember] = {}
    member_ids = {a.crew_member_id for a in active}
    if member_ids:
        members = {
            m.id: m
            for m in (
                await db.execute(
                    select(CrewMember).where(
                        CrewMember.id.in_(member_ids),
                        CrewMember.is_active.is_(True),
                    )
                )
            ).scalars()
        }

    present: dict[str, list[str]] = {}
    for a in active:
        m = members.get(a.crew_member_id)
        if m is None:
            continue
        role = normalize_role(a.role_on_board) or normalize_role(m.role)
        if role:
            present.setdefault(role, []).append(m.full_name)

    missing = [r for r in REQUIRED_ROLES if r not in present]
    return {
        "vessel_id": vessel_id,
        "at_date": at_date,
        "required": list(REQUIRED_ROLES),
        "labels": dict(ROLE_LABELS),
        "present": present,
        "missing": missing,
        "missing_labels": [ROLE_LABELS.get(r, r) for r in missing],
        "complete": not missing,
    }


# ───────────────────────── CREW-09 — marqueur « étranger » & temps d'embarquement ─────────────────────────
def is_non_schengen_national(nationality: str | None) -> bool:
    """Marqueur « étranger » (hors Schengen) dérivé de la nationalité.

    True si une nationalité est renseignée et hors espace Schengen ; False si
    Schengen ou nationalité inconnue (pas de marqueur)."""
    nat = (nationality or "").strip().upper()
    return bool(nat) and nat not in SCHENGEN_COUNTRIES


def assignment_days_in_year(
    embark_at: datetime | None,
    disembark_at: datetime | None,
    year: int,
    *,
    now: datetime,
) -> int:
    """Jours embarqués d'une affectation, bornés à l'année ``year`` (inclus).

    Affectation toujours en cours (``disembark_at`` None) → comptée jusqu'à
    ``now`` (borné à la fin d'année)."""
    if embark_at is None:
        return 0
    year_start = datetime(year, 1, 1, tzinfo=UTC)
    year_end = datetime(year, 12, 31, tzinfo=UTC)
    start = max(_as_utc(embark_at), year_start)
    end = min(_as_utc(disembark_at or now), year_end)
    if end < start:
        return 0
    return (end.date() - start.date()).days + 1


async def embarked_days_by_member(
    db: AsyncSession, year: int, *, now: datetime | None = None
) -> dict[int, int]:
    """Total de jours embarqués par marin sur l'année (toutes affectations)."""
    now = now or datetime.now(UTC)
    rows = (
        (await db.execute(select(CrewAssignment).where(CrewAssignment.embark_at.is_not(None))))
        .scalars()
        .all()
    )
    totals: dict[int, int] = defaultdict(int)
    for a in rows:
        totals[a.crew_member_id] += assignment_days_in_year(
            a.embark_at, a.disembark_at, year, now=now
        )
    return dict(totals)
