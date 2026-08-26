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

import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crew import CrewAssignment, CrewMember, MaradCrewSchedule
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

# Valeurs possibles de ``CrewMember.schengen_status``.
#
# ``indetermine`` (ajouté le 2026-07-30) comble un mensonge : l'indicateur
# affichait « conforme » dès que le décompte tombait à zéro, y compris quand
# AUCUNE donnée exploitable n'existait. Deux chemins y menaient — le défaut de
# colonne (`compliant`) et un ensemble de jours vide — et le résultat était le
# même : l'outil affirmait la conformité sans rien savoir.
#
# ⚠️ La source de vérité de la conformité Schengen est **Marad**, qui notifie
# l'Armement en amont des expirations (passeports, titres de séjour, 90/180).
# Le calcul de mynewtowt ne lit QUE ``crew_assignments``, alimenté par la seule
# saisie d'escale : il est structurellement incomplet. ``indetermine`` le dit,
# au lieu de le masquer. Ce statut n'est **pas une alerte** — c'est une absence
# d'information, et il ne remonte donc pas dans les alertes du module.
SCHENGEN_STATUSES: tuple[str, ...] = (
    "compliant",
    "warning",
    "non_compliant",
    "indetermine",
)

# Armement réglementaire — règle métier : capitaine, second, chef
# mécanicien, cuisinier, lieutenant, bosco. Mapping vers l'enum réel
# CREW_ROLES de routers/crew_router.py (colonne libre String(60)) :
# « cuisinier » → ``cook`` (seule valeur anglophone de l'enum), les cinq
# autres rôles existent tels quels (``capitaine``, ``second``,
# ``chef_mecanicien``, ``lieutenant``, ``bosco``).
# Enum canonique des fonctions à bord. Duplique volontairement `CREW_ROLES` de
# `routers/crew_router` : le routeur importe ce service, l'inverse serait
# circulaire. La dérive entre les deux est interdite par un test de parité
# (`tests/integration/test_crew_role_vocabulary.py`) — préféré à un refactor du
# routeur, qui n'apporterait rien de plus et toucherait des formulaires en place.
CANONICAL_ROLES: tuple[str, ...] = (
    "capitaine",
    "second",
    "chef_mecanicien",
    "cook",
    "lieutenant",
    "bosco",
    "marin",
    "eleve_officier",
    "electricien",
    "ajusteur",
    "matelot_cuisinier",
)

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
#
# ⚠️ L'enum canonique est **en français** (`CREW_ROLES` de `routers/crew_router`),
# à une exception près : `cook`. Toute nouvelle table d'alias doit s'y rabattre —
# ne PAS introduire un vocabulaire supplémentaire.
#
# Bloc « Excel Armement » ajouté le 2026-08-03 : les classeurs de relèves
# emploient QUATRE vocabulaires pour les mêmes fonctions (anglais complet dans le
# bloc équipage, abréviations dans le bloc « Crew Change », français dans la
# feuille de manning, codes étoilés dans la feuille `data`). Sans ces alias, un
# import ou un rapprochement laisse le rôle non résolu et l'armement réglementaire
# se croit incomplet. Cf. `docs/strategy/REFERENCE_METIER_RELEVES_EQUIPAGE.md`
# §3.5.
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
    # ── Vocabulaires des classeurs Excel de l'Armement ──────────────────────
    # Abréviations du bloc « Crew Change »
    "choff": "second",
    "cheng": "chef_mecanicien",
    "ce": "chef_mecanicien",
    "co": "second",
    # ⚠️ « BOSCO » est ici la graphie EXCEL du bosco — elle coïncide avec la
    # valeur canonique, l'alias est donc l'identité. Conservé explicitement pour
    # que la table documente le vocabulaire source.
    "bosco": "bosco",
    # Pont
    "mate": "lieutenant",
    "lieutenant pont": "lieutenant",
    "second capitaine": "second",
    # Matelots — l'enum canonique ne distingue pas AB1 / AB2 : les deux sont
    # des matelots. Le rang (1/2) vit dans le poste de la relève, pas dans la
    # fonction réglementaire.
    "ab": "marin",
    "ab1": "marin",
    "ab2": "marin",
    "ab 1": "marin",
    "ab 2": "marin",
    "matelot": "marin",
    # Mécanique / technique
    "fitter": "ajusteur",
    "fitter / oiler": "ajusteur",
    "fitter/oiler": "ajusteur",
    "oiler": "ajusteur",
    "electrotech": "electricien",
    "elect": "electricien",
    "electrical engineering officer assistant": "electricien",
    # Élèves
    "cadet": "eleve_officier",
    "deck cadet": "eleve_officier",
    "deck_cadet": "eleve_officier",
    "eleve": "eleve_officier",
    "élève": "eleve_officier",
}

# Marqueurs suffixés observés dans la feuille `data` des classeurs :
#   `MASTER*` → poste obligatoire · `MASTER Db` → doublure obligatoire.
# Yasmin (2026-08-03) : « doublure et l'étoile, ça veut dire obligatoire ».
# ⚠️ Modélisés SÉPARÉMENT : rien dans les classeurs ne prouve qu'ils sont
# équivalents, et deux booléens coûtent moins cher qu'une confusion figée.
_ROLE_MARKER_MANDATORY = "*"
_ROLE_MARKER_UNDERSTUDY = "db"


@dataclass(frozen=True)
class RoleToken:
    """Libellé de poste des classeurs Excel, décomposé.

    ``role`` est la valeur canonique (``CREW_ROLES``) ou ``None`` si le libellé
    reste non résolu — dans ce cas ``raw`` permet de le remonter à l'utilisateur
    plutôt que de le perdre en silence.
    """

    role: str | None
    is_mandatory: bool
    requires_understudy: bool
    raw: str
    # Libellé débarrassé de ses marqueurs et normalisé en minuscules — sert de
    # repli d'affichage quand la résolution échoue (cf. `normalize_role`).
    cleaned: str = ""


def normalize_role(value: str | None) -> str | None:
    """Rabat un rôle libre (FR/EN, casse variable) sur l'enum canonique.

    Tolère désormais les marqueurs suffixés des classeurs (``MASTER*``,
    ``CE Db``) : ils sont retirés avant résolution.

    ⚠️ **Comportement délibérément conservé** : un libellé non résolu est renvoyé
    sous sa forme nettoyée (minuscules), **pas** ``None``. Renvoyer ``None``
    ferait *disparaître* le marin des postes présents de
    :func:`vessel_readiness` — remplacer une donnée douteuse par une absence
    silencieuse serait le défaut miroir de celui qu'on corrige.

    Le code **neuf** qui a besoin de savoir si la résolution a abouti doit
    utiliser :func:`parse_role_token` et tester ``token.role is None``.
    """
    token = parse_role_token(value)
    if token.role is not None:
        return token.role
    return token.cleaned or None


def _fold_accents(value: str) -> str:
    """Retire les diacritiques : ``Mécanicien`` → ``mecanicien``.

    Indispensable ici : l'enum canonique est **sans accent**
    (``chef_mecanicien``, ``eleve_officier``) alors que les libellés français des
    classeurs en portent (``Chef Mécanicien``, ``Élève``). Sans ce repliement, la
    moitié du vocabulaire français reste non résolue — c'est un test de la table
    d'alias qui l'a révélé.
    """
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def parse_role_token(value: str | None) -> RoleToken:
    """Décompose un libellé de poste en (rôle canonique, obligatoire, doublure).

    Exemples : ``"MASTER*"`` → ``capitaine`` + obligatoire ·
    ``"CE Db"`` → ``chef_mecanicien`` + doublure · ``"AB 2"`` → ``marin``.

    Un libellé inconnu renvoie ``role=None`` **sans lever** : l'appelant décide
    de l'ignorer ou de le signaler. Retourner silencieusement le libellé brut
    comme s'il était canonique — ce que faisait l'ancienne implémentation —
    laissait passer des rôles non résolus dans l'armement réglementaire.
    """
    raw = (value or "").strip()
    if not raw:
        return RoleToken(None, False, False, raw, "")

    cleaned = raw.lower()
    mandatory = cleaned.endswith(_ROLE_MARKER_MANDATORY)
    if mandatory:
        cleaned = cleaned.rstrip(_ROLE_MARKER_MANDATORY).strip()

    understudy = cleaned.endswith(f" {_ROLE_MARKER_UNDERSTUDY}")
    if understudy:
        cleaned = cleaned[: -len(_ROLE_MARKER_UNDERSTUDY)].strip()

    # Espaces et tirets internes ramenés à une forme unique : les classeurs
    # écrivent « AB 1 », « AB1 », « Fitter / Oiler », « Fitter/Oiler »…
    collapsed = " ".join(cleaned.replace("_", " ").split())
    # Chaque forme est essayee accentuee PUIS repliee : les alias sont ecrits sans
    # accent, mais on ne veut pas casser une cle qui en porterait.
    candidates = []
    for form in (cleaned, collapsed, collapsed.replace(" ", ""), collapsed.replace(" ", "_")):
        candidates.append(form)
        folded = _fold_accents(form)
        if folded != form:
            candidates.append(folded)
    resolved = next((ROLE_SYNONYMS[c] for c in candidates if c in ROLE_SYNONYMS), None)
    if resolved is None:
        resolved = next((c for c in candidates if c in CANONICAL_ROLES), None)
    return RoleToken(resolved, mandatory, understudy, raw, cleaned)


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

    # Marins dont Marad connaît des embarquements. Ce calcul ne sait PAS les
    # exploiter (il n'a ni port ni fenêtre de voyage pour un planning Marad) : leur
    # simple existence suffit donc à rendre le décompte non concluant. C'est le
    # cas dominant en pratique, la décision de relève vivant côté Armement.
    marad_embarked_members: set[int] = set()
    if member_ids:
        for s in (
            (
                await db.execute(
                    select(MaradCrewSchedule).where(
                        MaradCrewSchedule.crew_member_id.in_(member_ids)
                    )
                )
            )
            .scalars()
            .all()
        ):
            # `crew_member_id` est une FK NULLABLE (un planning Marad peut ne pas
            # être rapproché d'un marin de l'ERP) : le filtre `in_` l'exclut en
            # pratique, on le vérifie quand même plutôt que de le supposer.
            if s.crew_member_id is not None and schedule_is_embarkation(s):
                marad_embarked_members.add(s.crew_member_id)

    for member in members:
        nationality = (member.nationality or "").strip().upper()
        if nationality in SCHENGEN_COUNTRIES:
            # Ressortissant Schengen : la règle 90/180 ne s'applique pas.
            member.schengen_status = "compliant"
            member.schengen_days_in_window = None
            member.schengen_window_end = None
            continue

        presence: set[date] = set()
        # Toute source d'embarquement que ce calcul ne SAIT PAS exploiter rend le
        # décompte incomplet — donc non concluant, jamais « conforme ».
        incomplete = member.id in marad_embarked_members
        for a in assigns_by_member.get(member.id, ()):
            leg = legs.get(a.leg_id)
            if leg is None:
                # `leg_id` absent (embarquement hors voyage, arbitrage A4) ou leg
                # introuvable : l'affectation existe mais reste inexploitable ici.
                incomplete = True
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
            # Dépassement établi sur les seules données exploitables : certain,
            # et un dépassement certain prime sur l'incertitude du reste.
            status = "non_compliant"
        elif days > SCHENGEN_WARNING_DAYS:
            status = "warning"
        elif incomplete:
            # Des embarquements existent hors de portée de ce calcul : le
            # décompte n'est qu'un plancher, on ne conclut pas.
            status = "indetermine"
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


def schedule_is_embarkation(s: MaradCrewSchedule) -> bool:
    """Un planning Marad = **embarquement** s'il porte un navire.

    Les plannings Marad incluent aussi des périodes à terre (congés,
    indisponibilités : ``Vessel=null``, ex. ``status='Congés'``) qui ne sont PAS
    des embarquements et ne doivent compter ni dans les jours embarqués, ni dans
    la bordée, ni sur le certificat. On les exclut par l'absence de navire.
    """
    return bool(s.vessel_id or s.marad_vessel_name) and s.start_date is not None


# ``_marad_days_in_year`` a été retiré le 2026-07-30 : il additionnait des
# COMPTES de jours, ce qui rendait le double comptage inévitable dès que les deux
# registres décrivaient la même période. ``embarked_days_by_member`` construit
# désormais une union d'ensembles de jours et n'a plus besoin de ce helper.


async def embarked_days_by_member(
    db: AsyncSession, year: int, *, now: datetime | None = None
) -> dict[int, int]:
    """Total de jours embarqués par marin sur l'année, **sans double comptage**.

    Deux registres d'embarquement coexistent et **décrivent parfois la même
    période** :

    - ``MaradCrewSchedule`` — les relèves décidées par l'Armement, importées de
      Marad en lecture seule. C'est la source de vérité des embarquements ;
    - ``CrewAssignment`` — créé par la saisie d'une opération d'escale
      ``embarquement`` (``services/escale_crew.couple_crew_assignment``).

    ⚠️ **Correctif 2026-07-30** : cette fonction *additionnait* les jours des deux
    registres. Sa version précédente supposait ``CrewAssignment`` vide (« les
    marins proviennent exclusivement de Marad, aucune saisie manuelle ») — ce qui
    est faux, la saisie d'escale en crée. Dès qu'une opération d'escale était
    enregistrée pour un embarquement déjà connu de Marad, **les jours de ce marin
    étaient comptés deux fois**.

    L'indicateur est donc désormais construit sur une **union d'ensembles de jours
    calendaires** (même approche que ``refresh_schengen_for_members``) : un jour
    couvert par les deux registres compte **une fois**. C'est le chiffre dont
    dépend la planification des relèves (jours en mer, périodes embarquées / à
    terre), qui se fait aujourd'hui hors du logiciel.

    Bornes **inclusives** des deux côtés, comme ``assignment_days_in_year`` :
    un embarquement du 1er au 10 compte 10 jours. Une affectation encore en cours
    (``disembark_at``/``end_date`` absent) est comptée jusqu'à ``now``.
    """
    now = now or datetime.now(UTC)
    year_start, year_end = date(year, 1, 1), date(year, 12, 31)
    days_by_member: dict[int, set[date]] = defaultdict(set)

    assigns = (
        (await db.execute(select(CrewAssignment).where(CrewAssignment.embark_at.is_not(None))))
        .scalars()
        .all()
    )
    for a in assigns:
        _add_presence_days(
            days_by_member[a.crew_member_id],
            _as_utc(a.embark_at),
            _as_utc(a.disembark_at) or now,
            year_start,
            year_end,
        )

    scheds = (
        (
            await db.execute(
                select(MaradCrewSchedule).where(MaradCrewSchedule.crew_member_id.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    for s in scheds:
        if not schedule_is_embarkation(s):
            continue
        start = datetime(s.start_date.year, s.start_date.month, s.start_date.day, tzinfo=UTC)
        end = (
            datetime(s.end_date.year, s.end_date.month, s.end_date.day, tzinfo=UTC)
            if s.end_date
            else now
        )
        _add_presence_days(days_by_member[s.crew_member_id], start, end, year_start, year_end)

    # Un marin sans aucun jour retenu (embarquement hors année) ne doit pas
    # apparaître avec 0 — l'appelant distingue « absent » de « zéro jour ».
    return {mid: len(days) for mid, days in days_by_member.items() if days}


async def current_embarkations(
    db: AsyncSession, *, on: date | None = None
) -> list[MaradCrewSchedule]:
    """Plannings Marad **en cours** (embarquements dont la fenêtre contient ``on``).

    Sert à la bordée actuelle par navire et à l'indicateur « En activité ».
    """
    on = on or date.today()
    rows = (
        (
            await db.execute(
                select(MaradCrewSchedule).where(MaradCrewSchedule.crew_member_id.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    return [
        s
        for s in rows
        if schedule_is_embarkation(s)
        and s.start_date <= on
        and (s.end_date is None or on <= s.end_date)
    ]


async def crew_for_leg(
    db: AsyncSession, leg: Leg, vessel_id: int | None = None
) -> list[tuple[MaradCrewSchedule, CrewMember]]:
    """Équipage embarqué sur un leg (pour le certificat Anemos).

    Réconciliation : d'abord par ``leg_id`` (un « voyage » Marad = un leg) ;
    à défaut de correspondance, repli sur ``vessel_id`` + chevauchement de la
    fenêtre de dates avec [ETD, ETA] du leg. Exclut les périodes à terre.
    """
    stmt = (
        select(MaradCrewSchedule, CrewMember)
        .join(CrewMember, CrewMember.id == MaradCrewSchedule.crew_member_id)
        .where(MaradCrewSchedule.leg_id == leg.id)
        .order_by(CrewMember.full_name)
    )
    rows = [(s, m) for s, m in (await db.execute(stmt)).all() if schedule_is_embarkation(s)]
    if rows:
        return rows
    vid = vessel_id if vessel_id is not None else leg.vessel_id
    if vid is None:
        return []
    lo = leg.atd or leg.etd
    hi = leg.ata or leg.eta
    lo_d = lo.date() if lo else None
    hi_d = hi.date() if hi else None
    stmt = (
        select(MaradCrewSchedule, CrewMember)
        .join(CrewMember, CrewMember.id == MaradCrewSchedule.crew_member_id)
        .where(MaradCrewSchedule.vessel_id == vid)
        .order_by(CrewMember.full_name)
    )
    out: list[tuple[MaradCrewSchedule, CrewMember]] = []
    for s, m in (await db.execute(stmt)).all():
        if not schedule_is_embarkation(s):
            continue
        # Chevauchement [start,end] ∩ [ETD,ETA] (bornes ouvertes tolérées).
        if hi_d is not None and s.start_date > hi_d:
            continue
        if lo_d is not None and s.end_date is not None and s.end_date < lo_d:
            continue
        out.append((s, m))
    return out
