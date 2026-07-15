"""Cycle de vie déclaratif des événements MRV (LOT 3).

Machine à états **Brouillon → Finalisé → Validé** appliquée aux événements
de ``app.models.nav_event`` :

- ``create_draft`` — crée un brouillon (idempotent par ``client_uuid`` pour la
  reprise PWA offline) ;
- ``update_draft`` — met à jour un brouillon (= autosave, ``last_saved_at``)
  avec **garde auteur-seul** (décision D11/v0.6 : seul l'auteur du brouillon
  peut le reprendre — sinon ``DraftAuthorError``) ;
- ``finalize`` — calcule ``datetime_utc`` (local + tz), gèle l'événement et
  passe le moteur de règles (scope ``event``) : un ``fail`` de sévérité
  ``bloquant`` **refuse** la finalisation (``EventFinalizationError``) ;
- ``validate`` — passage au statut ``valide`` (réservé au siège ; la
  permission est vérifiée à la route — lot 5) ;
- ``prefill_position`` — pré-remplissage position depuis ``vessel_positions``
  (flux Thalos), source ``thalos_auto`` ; une position manuelle exige une
  justification (R05).

Les brouillons sont **exclus de tout calcul** (CDC §9.1) — cf.
``inter_event_compute``.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim import VesselPosition
from app.models.leg import Leg
from app.models.nav_event import (
    EVENT_CLASS_BY_TYPE,
    EVENT_TYPES,
    AnchoringEvent,
    CutoffEvent,
    NavEvent,
    NoonEvent,
    PortCallEvent,
)
from app.models.vessel import Vessel
from app.services.validation_engine import run_rules
from app.utils.timezones import to_utc

# ════════════════════════════════════════════════════════════ Exceptions


class EventCaptureError(Exception):
    """Erreur métier du cycle de vie d'un événement."""


class DraftAuthorError(EventCaptureError):
    """Un utilisateur autre que l'auteur tente de reprendre/finaliser le brouillon."""


class EventStateError(EventCaptureError):
    """Transition d'état invalide (ex. finaliser un événement déjà finalisé)."""


class EventFinalizationError(EventCaptureError):
    """Finalisation refusée : au moins une règle bloquante a échoué.

    ``messages`` liste les motifs (règle + message) ; ``outcomes`` porte les
    ``QualityCheckResult`` bloquants persistés (audit).
    """

    def __init__(self, messages: list[str], outcomes: list | None = None) -> None:
        self.messages = messages
        self.outcomes = outcomes or []
        super().__init__(" ; ".join(messages) or "Finalisation refusée (règle bloquante).")


# ════════════════════════════════════════════════════════════ Champs payload

# Champs communs (table mère) — ``datetime_utc`` EXCLU (calculé, non modifiable).
_COMMON_FIELDS: tuple[str, ...] = (
    "datetime_local",
    "timezone",
    "lat_decimal",
    "lon_decimal",
    "position_source",
    "position_justification",
    "cargo_mrv_t",
)
_NOON_FIELDS: tuple[str, ...] = (
    "time_from_sosp_h",
    "distance_from_sosp_nm",
    "distance_to_go_nm",
    "announced_eta",
    "etb",
    "eta_7_to_10kt",
    "comments",
)
_PORTCALL_FIELDS: tuple[str, ...] = (
    "draft_fwd_m",
    "draft_aft_m",
    "trim_m",
    "vessel_condition",
    "rob_t",
    "cargo_bl_t",
    "etd_confirmed",
    "eta_announced",
    "etb",
)
_ANCHORING_FIELDS: tuple[str, ...] = (
    "sequence_no",
    "reason",
    "paired_event_id",
    "duration_h",
)


def _allowed_fields(event: NavEvent) -> tuple[str, ...]:
    fields = _COMMON_FIELDS
    if isinstance(event, NoonEvent):
        fields += _NOON_FIELDS
    elif isinstance(event, PortCallEvent):
        fields += _PORTCALL_FIELDS
    elif isinstance(event, AnchoringEvent):
        fields += _ANCHORING_FIELDS
    return fields


def _apply_payload(event: NavEvent, payload: dict | None) -> None:
    """Applique les seuls champs autorisés du sous-type (``datetime_utc`` jamais)."""
    if not payload:
        return
    allowed = _allowed_fields(event)
    for key, value in payload.items():
        if key in allowed:
            setattr(event, key, value)


def _pin_cutoff_datetime_utc(raw_utc: datetime) -> datetime:
    """Fige un horodatage brut sur l'instant réglementaire exact le plus proche
    (31/12 24:00 UTC ⇔ 01/01 00:00 UTC — CDC v0.7 §9.2). Décision produit :
    le Cut-off n'est pas une observation de terrain comme les autres types,
    c'est une règle fixe — le local/tz saisi par le Master ne sert qu'à
    indiquer QUELLE bascule d'année il vise, jamais à décaler l'instant."""
    candidates = (
        datetime(raw_utc.year, 1, 1, tzinfo=UTC),
        datetime(raw_utc.year + 1, 1, 1, tzinfo=UTC),
    )
    return min(candidates, key=lambda c: abs((c - raw_utc).total_seconds()))


def _compute_datetime_utc(event: NavEvent) -> datetime | None:
    """UTC calculé depuis ``datetime_local`` + ``timezone`` (DST-aware via zoneinfo).

    Pour un ``CutoffEvent``, le résultat est ensuite figé sur l'instant
    réglementaire exact (cf. ``_pin_cutoff_datetime_utc``)."""
    if event.datetime_local is not None and event.timezone:
        raw = to_utc(event.datetime_local, event.timezone)
        if isinstance(event, CutoffEvent):
            return _pin_cutoff_datetime_utc(raw)
        return raw
    return None


# ════════════════════════════════════════════════════════════ Création / MAJ


async def create_draft(
    db: AsyncSession,
    *,
    leg: Leg,
    vessel: Vessel | None,
    event_type: str,
    author,
    payload: dict | None = None,
    client_uuid: str | None = None,
) -> NavEvent:
    """Crée un brouillon d'événement.

    Idempotence PWA : si ``client_uuid`` est déjà connu, l'événement existant
    est renvoyé tel quel (aucun doublon — cf. ``test onboard offline``).
    """
    if event_type not in EVENT_TYPES:
        raise EventCaptureError(f"Type d'événement inconnu : {event_type!r}.")

    if client_uuid:
        existing = (
            await db.execute(select(NavEvent).where(NavEvent.client_uuid == client_uuid))
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    cls = EVENT_CLASS_BY_TYPE[event_type]
    now = datetime.now(UTC)
    event = cls(
        leg_id=leg.id,
        vessel_id=(vessel.id if vessel is not None else None),
        status="brouillon",
        author_user_id=(author.id if author is not None else None),
        client_uuid=client_uuid,
        last_saved_at=now,
    )
    _apply_payload(event, payload)
    event.datetime_utc = _compute_datetime_utc(event)
    db.add(event)
    await db.flush()
    return event


def _assert_author(event: NavEvent, author) -> None:
    """Garde auteur-seul (D11) : seul l'auteur peut reprendre son brouillon."""
    if event.author_user_id is None:
        return
    if author is None or event.author_user_id != author.id:
        raise DraftAuthorError(
            f"Seul l'auteur du brouillon (user #{event.author_user_id}) peut le reprendre."
        )


async def update_draft(db: AsyncSession, event: NavEvent, author, payload: dict | None) -> NavEvent:
    """Met à jour un brouillon (= autosave). Garde auteur-seul + statut brouillon."""
    if event.status != "brouillon":
        raise EventStateError(
            f"Événement au statut {event.status!r} : seul un brouillon est modifiable."
        )
    _assert_author(event, author)
    _apply_payload(event, payload)
    event.last_saved_at = datetime.now(UTC)
    event.datetime_utc = _compute_datetime_utc(event)
    await db.flush()
    return event


# ════════════════════════════════════════════════════════════ Finalisation


def _manual_position_ok(event: NavEvent) -> bool:
    """R05 (précurseur) : position manuelle ⇒ justification obligatoire."""
    if event.position_source != "manuel_justifie":
        return True
    if event.lat_decimal is None and event.lon_decimal is None:
        return True
    return bool(event.position_justification and event.position_justification.strip())


async def finalize(db: AsyncSession, event: NavEvent, author) -> NavEvent:
    """Fige l'événement (statut ``finalise``) après contrôle du moteur de règles.

    1. garde auteur-seul + statut brouillon ;
    2. ``datetime_utc`` = to_utc(datetime_local, timezone) — autoritatif ;
    3. ``run_rules(scope="event")`` → persiste les QualityCheckResult ;
    4. un ``fail`` bloquant (ou une position manuelle sans justification, R05)
       ⇒ ``EventFinalizationError`` ; l'événement reste brouillon.
    """
    if event.status != "brouillon":
        raise EventStateError(
            f"Événement au statut {event.status!r} : seul un brouillon peut être finalisé."
        )
    _assert_author(event, author)

    # UTC autoritatif (peut rester None si local/tz manquent → R01 bloquera).
    event.datetime_utc = _compute_datetime_utc(event)
    await db.flush()

    leg = await db.get(Leg, event.leg_id)
    vessel = await db.get(Vessel, event.vessel_id) if event.vessel_id is not None else None
    run_id = _uuid.uuid4().hex
    summary = await run_rules(db, "event", [event], vessel=vessel, leg=leg, run_id=run_id)

    blocking = [
        r for r in summary.results if r.result == "fail" and r.severity_applied == "bloquant"
    ]
    messages = [f"{r.rule_id} : {r.message}" for r in blocking]
    if not _manual_position_ok(event):
        messages.append(
            "R05 : position saisie manuellement sans justification (position_justification requis)."
        )

    if messages:
        raise EventFinalizationError(messages, outcomes=blocking)

    event.status = "finalise"
    event.finalized_at = datetime.now(UTC)
    await db.flush()
    await _refresh_emission_summary(db, event.leg_id)
    return event


async def validate(db: AsyncSession, event: NavEvent, validator) -> NavEvent:
    """Passage au statut ``valide`` (siège). La permission est vérifiée à la route (lot 5)."""
    if event.status != "finalise":
        raise EventStateError(
            f"Événement au statut {event.status!r} : seul un événement finalisé peut être validé."
        )
    event.status = "valide"
    event.validated_at = datetime.now(UTC)
    event.validated_by = validator.id if validator is not None else None
    await db.flush()
    await _refresh_emission_summary(db, event.leg_id)
    return event


async def _refresh_emission_summary(db: AsyncSession, leg_id: int | None) -> None:
    """Hook lot 9 : rematérialise ``voyage_emission_summaries`` du voyage.

    Import tardif de ``services.emission_ledger`` (le ledger importe
    ``inter_event_compute`` qui partage des modèles avec ce module — l'import
    au niveau fonction évite tout cycle). **Best-effort** : le summary est un
    cache recalculable, jamais source de vérité — son échec ne doit jamais
    bloquer la finalisation/validation d'un événement à bord (même posture
    no-op silencieuse que ``services.security_alerts`` sans SMTP).
    """
    if leg_id is None:
        return
    try:
        from app.services.emission_ledger import refresh_summary

        leg = await db.get(Leg, leg_id)
        if leg is not None:
            await refresh_summary(db, leg)
    except Exception:  # pragma: no cover — cache best-effort, jamais bloquant
        pass


# ════════════════════════════════════════════════════════════ Préremplissage position


@dataclass(frozen=True)
class PrefilledPosition:
    """Position pré-remplie depuis le flux Thalos (``vessel_positions``)."""

    lat_decimal: Decimal
    lon_decimal: Decimal
    source: str  # toujours "thalos_auto"
    recorded_at: datetime


async def prefill_position(
    db: AsyncSession, vessel: Vessel, at_dt: datetime
) -> PrefilledPosition | None:
    """Position Thalos la plus proche de ``at_dt`` pour ``vessel`` (source ``thalos_auto``).

    Réutilise le flux ``vessel_positions`` (pattern ``autofill_event_position``
    du noon). Renvoie ``None`` si aucune position (saisie manuelle justifiée
    reste alors possible, R05). Best-effort : ne modifie rien en base.
    """
    if vessel is None or vessel.id is None:
        return None

    before = (
        await db.execute(
            select(VesselPosition)
            .where(VesselPosition.vessel_id == vessel.id, VesselPosition.recorded_at <= at_dt)
            .order_by(VesselPosition.recorded_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    after = (
        await db.execute(
            select(VesselPosition)
            .where(VesselPosition.vessel_id == vessel.id, VesselPosition.recorded_at >= at_dt)
            .order_by(VesselPosition.recorded_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()

    candidates = [p for p in (before, after) if p is not None]
    if not candidates:
        return None

    def _abs_seconds(recorded_at: datetime) -> float:
        # Robuste au mélange naïf (SQLite) / aware (Postgres) : normalise en
        # UTC-naïf avant la soustraction.
        a = (
            recorded_at
            if recorded_at.tzinfo is None
            else recorded_at.astimezone(UTC).replace(tzinfo=None)
        )
        b = at_dt if at_dt.tzinfo is None else at_dt.astimezone(UTC).replace(tzinfo=None)
        return abs((a - b).total_seconds())

    nearest = min(candidates, key=lambda p: _abs_seconds(p.recorded_at))
    return PrefilledPosition(
        lat_decimal=Decimal(str(nearest.latitude)),
        lon_decimal=Decimal(str(nearest.longitude)),
        source="thalos_auto",
        recorded_at=nearest.recorded_at,
    )
