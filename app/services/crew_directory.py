"""Répertoire crew — trombinoscope Armement (génération PDF mensuelle/à la demande).

Regroupe les marins actifs par fonction, ou par agence de sous-traitance pour
le personnel externe (``CrewMember.agency`` renseigné) — cf.
``docs/strategy/CAHIER_DES_CHARGES_TROMBINOSCOPE.md`` §5/§11 pour le mapping
de taxonomie figé avec le service Armement.

La sync Marad écrit ``CrewMember.role`` tel quel depuis ``ranks[0]`` (souvent
un intitulé anglais brut, ex. ``"Chief Officer"``), sans passer par
``crew_compliance.normalize_role()`` (conçu pour le contrôle d'armement
réglementaire, avec ses propres clés — cf. ``marad_sync.py::_apply``). On
construit donc ici une normalisation dédiée, tolérante aux espaces/casse,
plutôt que de modifier ``ROLE_SYNONYMS``/``ROLE_LABELS`` (laissés inchangés).

Photos encodées en data URI (base64) au moment du rendu : WeasyPrint ne peut
pas authentifier une requête HTTP vers la route de service protégée
``/crew/members/{id}/photo``, donc on lit directement le fichier via
``safe_files.resolve_path``. Cache TTL court (la liste change au rythme de la
sync Marad, pas des requêtes), tolérant aux erreurs DB/fichiers manquants —
un trombinoscope ne doit jamais échouer à cause d'une seule fiche
incomplète (cf. ``app/services/fleet.py`` pour le même patron).
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crew import CrewMember
from app.services.safe_files import resolve_path

logger = logging.getLogger("crew-directory")

_CACHE_TTL_SECONDS = 300.0

# Ordre d'affichage des pages (fonctions), figé avec le service Armement le
# 2026-07-17 — reprend l'ordre du gabarit réel analysé. Clé = valeur
# canonique de ``CREW_ROLES`` (``app/routers/crew_router.py``).
_ROLE_ORDER: tuple[str, ...] = (
    "capitaine",
    "chef_mecanicien",
    "second",
    "lieutenant",
    "electricien",
    "eleve_officier",
    "bosco",
    "marin",
    "ajusteur",
    "matelot_cuisinier",
    "cook",
)

# Libellés anglais affichés sur le trombinoscope (terminologie déjà utilisée
# opérationnellement par le service Armement) — dictionnaire dédié, distinct
# de ``crew_compliance.ROLE_LABELS`` (français, réservé au contrôle
# réglementaire, ne pas modifier).
_ROLE_LABELS_EN: dict[str, str] = {
    "capitaine": "MASTER",
    "chef_mecanicien": "CHIEF ENGINEER",
    "second": "CHIEF OFFICER",
    "lieutenant": "MATE",
    "electricien": "ASSISTING ELECTRICAL ENGINEERING OFFICER",
    "eleve_officier": "CADET",
    "bosco": "BOSUN",
    "marin": "ABLE SEAMAN",
    "ajusteur": "FITTER",
    "matelot_cuisinier": "ABLE SEAMAN COOK",
    "cook": "COOK",
}

# Alias tolérants : replie une valeur ``role`` brute (français canonique
# saisi manuellement OU intitulé Marad libre, espaces/casse variables) sur
# une clé canonique ci-dessus. Une valeur non répertoriée n'est jamais
# supprimée : elle forme son propre groupe (cf. ``_display_label``).
_ROLE_ALIASES: dict[str, str] = {
    "master": "capitaine",
    "captain": "capitaine",
    "chief officer": "second",
    "chief mate": "second",
    "chief engineer": "chef_mecanicien",
    "engineer": "chef_mecanicien",
    "chef mecanicien": "chef_mecanicien",  # forme canonique après normalisation espaces/soulignés
    "mate": "lieutenant",
    "officer": "lieutenant",
    "assisting electrical engineering officer": "electricien",
    "electrical engineer": "electricien",
    "cadet": "eleve_officier",
    "eleve officier": "eleve_officier",  # idem
    "bosun": "bosco",
    "boatswain": "bosco",
    "able seaman": "marin",
    "ordinary seaman": "marin",
    "ab": "marin",
    "matelot": "marin",
    "fitter": "ajusteur",
    "able seaman cook": "matelot_cuisinier",
    "matelot cuisinier": "matelot_cuisinier",  # idem
    "cook": "cook",
    "cuisinier": "cook",
}


def normalize_role_for_directory(raw_role: str | None) -> str:
    """Replie une valeur ``role`` brute sur une clé canonique du trombinoscope.

    Insensible à la casse et aux espaces/soulignés (``"Chief Officer"``,
    ``"chief_officer"``, ``"second"`` → même clé). Une valeur inconnue est
    repliée sur elle-même plutôt que d'écarter silencieusement le marin.
    """
    if not raw_role or not raw_role.strip():
        return "marin"
    cleaned = " ".join(raw_role.strip().lower().replace("_", " ").split())
    return _ROLE_ALIASES.get(cleaned, cleaned)


def _display_label(role_key: str) -> str:
    if role_key in _ROLE_LABELS_EN:
        return _ROLE_LABELS_EN[role_key]
    return role_key.replace("_", " ").upper()


def _display_name(member: CrewMember) -> str:
    first = (member.first_name or "").strip()
    last = (member.last_name or "").strip()
    if first or last:
        return f"{first} {last}".strip().upper()
    return (member.full_name or "").strip().upper()


def _photo_data_uri(member: CrewMember) -> str | None:
    if not member.photo_path:
        return None
    try:
        content = resolve_path(member.photo_path).read_bytes()
    except Exception:  # pragma: no cover — fichier manquant/illisible : pas de photo
        return None
    mime = member.photo_mime or "image/jpeg"
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime};base64,{encoded}"


@dataclass(frozen=True)
class CrewDirectoryEntry:
    """Une fiche marin dans le trombinoscope."""

    display_name: str
    function_label: str  # affiché en sous-titre uniquement sur les pages agence
    photo_data_uri: str | None


@dataclass(frozen=True)
class CrewDirectoryGroup:
    """Une page du trombinoscope : une fonction, ou une agence de sous-traitance."""

    title: str
    is_agency: bool
    entries: tuple[CrewDirectoryEntry, ...]


@dataclass(frozen=True)
class CrewDirectory:
    """Trombinoscope complet (marins actifs), prêt pour le rendu PDF."""

    groups: tuple[CrewDirectoryGroup, ...]

    @property
    def has_content(self) -> bool:
        return any(g.entries for g in self.groups)

    @property
    def member_count(self) -> int:
        return sum(len(g.entries) for g in self.groups)


def _sort_key(member: CrewMember) -> tuple[str, str]:
    return (member.last_name or member.full_name or "", member.first_name or "")


def _build_group(title: str, *, is_agency: bool, members: list[CrewMember]) -> CrewDirectoryGroup:
    members = sorted(members, key=_sort_key)
    return CrewDirectoryGroup(
        title=title,
        is_agency=is_agency,
        entries=tuple(
            CrewDirectoryEntry(
                display_name=_display_name(m),
                function_label=_display_label(normalize_role_for_directory(m.role)),
                photo_data_uri=_photo_data_uri(m),
            )
            for m in members
        ),
    )


_directory_cache: CrewDirectory | None = None
_directory_loaded_at: float = 0.0


def invalidate_cache() -> None:
    """Force le recalcul au prochain ``build_directory()`` (upload photo,
    bascule actif/inactif, édition manuelle, tests)."""
    global _directory_cache, _directory_loaded_at
    _directory_cache = None
    _directory_loaded_at = 0.0


async def build_directory(db: AsyncSession) -> CrewDirectory:
    """Construit le trombinoscope à partir des marins actifs.

    Cache 5 min, tolérant aux erreurs DB (jamais d'échec dur — un
    trombinoscope vide vaut mieux qu'une génération qui casse).
    """
    global _directory_cache, _directory_loaded_at
    now = time.monotonic()
    if _directory_cache is not None and (now - _directory_loaded_at) < _CACHE_TTL_SECONDS:
        return _directory_cache

    by_role: dict[str, list[CrewMember]] = {}
    by_agency: dict[str, list[CrewMember]] = {}
    try:
        rows = (
            (await db.execute(select(CrewMember).where(CrewMember.is_active.is_(True))))
            .scalars()
            .all()
        )
        for m in rows:
            agency = (m.agency or "").strip()
            if agency:
                by_agency.setdefault(agency, []).append(m)
            else:
                by_role.setdefault(normalize_role_for_directory(m.role), []).append(m)
    except Exception:
        # Best-effort : un trombinoscope vide vaut mieux qu'une génération qui
        # casse, mais l'échec doit rester diagnosticable (review 2026-07-20 —
        # ce bloc avalait l'erreur sans aucune trace).
        logger.exception("build_directory : échec de la requête crew_members")

    groups: list[CrewDirectoryGroup] = []
    for role_key in _ROLE_ORDER:
        members = by_role.pop(role_key, None)
        if members:
            groups.append(_build_group(_display_label(role_key), is_agency=False, members=members))
    # Fonctions résiduelles non prévues dans _ROLE_ORDER (donnée inattendue) :
    # affichées quand même plutôt que de faire disparaître un marin actif.
    for role_key in sorted(by_role):
        groups.append(
            _build_group(_display_label(role_key), is_agency=False, members=by_role[role_key])
        )
    for agency_name in sorted(by_agency):
        groups.append(
            _build_group(agency_name.upper(), is_agency=True, members=by_agency[agency_name])
        )

    _directory_cache = CrewDirectory(groups=tuple(groups))
    _directory_loaded_at = now
    return _directory_cache
