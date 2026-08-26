"""Historisation des offres commerciales — journal append-only et vérifiable.

Pourquoi une table dédiée plutôt que ``activity_logs`` : le journal d'activité
enregistre *qu'une* action a eu lieu, avec un détail en texte libre. Il ne
conserve ni l'ancienne ni la nouvelle valeur des champs, et il est purgeable par
ancienneté. Une offre commerciale, elle, engage un prix : en cas de contestation
il faut pouvoir dire **ce que valait l'offre à chaque instant**, et prouver que
l'historique n'a pas été retouché.

D'où trois garanties, dans cet ordre d'importance :

1. **Complétude** — chaque révision porte le diff champ par champ *et* l'état
   complet de l'offre. Le diff se lit, l'instantané se rejoue.
2. **Inaltérabilité détectable** — chaque révision est hachée avec le hash de la
   précédente. Modifier ou retirer une ligne casse la chaîne des suivantes.
   Ce n'est pas une protection contre l'écriture (une base reste modifiable par
   qui a les droits) : c'est une protection contre la falsification *silencieuse*.
3. **Append-only en pratique** — ce module n'expose aucune fonction de
   modification ou de suppression, et la table est hors périmètre de purge.

Le chaînage porte sur une sérialisation **canonique** (clés triées, séparateurs
fixes) : sans cela, deux sérialisations équivalentes du même état donneraient des
hash différents et la vérification échouerait à tort.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial import RateOffer, RateOfferRevision

# Champs de l'offre suivis dans l'historique. Volontairement explicite plutôt
# que dérivé du modèle : ajouter une colonne ne doit pas l'exposer à l'historique
# par accident (ni un champ technique le polluer).
AUDITED_OFFER_FIELDS: tuple[str, ...] = (
    "reference",
    "client_id",
    "grid_id",
    "leg_id",
    "title",
    "status",
    "estimated_palettes",
    "proposed_rate_eur",
    "total_eur",
    "valid_until",
    "notes",
)

# Actions possibles — un verbe métier, pas un nom de route.
REVISION_ACTIONS = (
    "created",
    "updated",
    "sent",
    "validated",
    "cancelled",
    "expired",
    "converted",
)


def _plain(value: Any) -> Any:
    """Valeur sérialisable et **stable** dans le temps pour le hachage.

    ``Decimal`` en chaîne (jamais en float : la conversion perdrait des
    centimes et rendrait le hash dépendant de l'arrondi binaire), dates en ISO.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def snapshot_offer(offer: RateOffer) -> dict[str, Any]:
    """État de l'offre restreint aux champs suivis, sérialisable."""
    return {field: _plain(getattr(offer, field, None)) for field in AUDITED_OFFER_FIELDS}


def _canonical(payload: dict[str, Any]) -> str:
    """Sérialisation canonique : clés triées, séparateurs fixes, UTF-8 conservé."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(
    *,
    previous_hash: str | None,
    sequence: int,
    action: str,
    snapshot: dict[str, Any],
) -> str:
    """Hash d'une révision, chaîné sur la précédente.

    Le rang et l'action entrent dans le calcul : sans eux, deux révisions
    ramenant l'offre au même état seraient interchangeables dans la chaîne.
    """
    material = _canonical(
        {
            "previous": previous_hash or "",
            "sequence": sequence,
            "action": action,
            "snapshot": snapshot,
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def diff_snapshots(before: dict[str, Any] | None, after: dict[str, Any]) -> list[dict]:
    """Diff champ par champ entre deux instantanés (vide à la création)."""
    if before is None:
        return []
    return [
        {"field": field, "old": before.get(field), "new": after.get(field)}
        for field in AUDITED_OFFER_FIELDS
        if before.get(field) != after.get(field)
    ]


async def latest_revision(db: AsyncSession, offer_id: int) -> RateOfferRevision | None:
    """Dernière révision enregistrée pour une offre (None si aucune)."""
    return (
        await db.execute(
            select(RateOfferRevision)
            .where(RateOfferRevision.offer_id == offer_id)
            .order_by(RateOfferRevision.sequence.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def list_revisions(db: AsyncSession, offer_id: int) -> list[RateOfferRevision]:
    """Historique complet d'une offre, du plus ancien au plus récent."""
    return list(
        (
            await db.execute(
                select(RateOfferRevision)
                .where(RateOfferRevision.offer_id == offer_id)
                .order_by(RateOfferRevision.sequence.asc())
            )
        )
        .scalars()
        .all()
    )


async def record_revision(
    db: AsyncSession,
    offer: RateOffer,
    *,
    action: str,
    actor_id: int | None = None,
    actor_name: str | None = None,
    actor_role: str | None = None,
    comment: str | None = None,
    before: dict[str, Any] | None = None,
) -> RateOfferRevision | None:
    """Enregistre une révision de l'offre. Retourne ``None`` si rien n'a changé.

    ``before`` est l'instantané pris **avant** modification (cf.
    :func:`snapshot_offer`). Sur une action ``updated`` sans différence réelle,
    aucune révision n'est écrite : un historique qui consigne des non-événements
    devient vite illisible, et c'est précisément ce qui le fait ignorer.
    Les changements d'état (envoi, validation, annulation…) sont toujours tracés,
    même si les champs suivis n'ont pas bougé.
    """
    if action not in REVISION_ACTIONS:
        raise ValueError(f"action de révision inconnue : {action}")

    after = snapshot_offer(offer)
    changes = diff_snapshots(before, after)
    if action == "updated" and not changes:
        return None

    previous = await latest_revision(db, offer.id)
    sequence = (previous.sequence + 1) if previous is not None else 1
    content_hash = compute_hash(
        previous_hash=previous.content_hash if previous is not None else None,
        sequence=sequence,
        action=action,
        snapshot=after,
    )

    revision = RateOfferRevision(
        offer_id=offer.id,
        sequence=sequence,
        action=action,
        actor_id=actor_id,
        actor_name=actor_name,
        actor_role=actor_role,
        changes_json=json.dumps(changes, ensure_ascii=False) if changes else None,
        snapshot_json=_canonical(after),
        previous_hash=previous.content_hash if previous is not None else None,
        content_hash=content_hash,
        comment=(comment or "").strip() or None,
    )
    db.add(revision)
    await db.flush()
    return revision


async def verify_chain(db: AsyncSession, offer_id: int) -> tuple[bool, str | None]:
    """Vérifie l'intégrité de la chaîne d'une offre.

    Renvoie ``(True, None)`` si la chaîne est intacte, sinon ``(False, motif)``
    décrivant la première anomalie rencontrée : rang manquant, chaînage rompu ou
    hash ne correspondant pas à l'instantané consigné.
    """
    revisions = await list_revisions(db, offer_id)
    expected_previous: str | None = None
    for index, revision in enumerate(revisions, start=1):
        if revision.sequence != index:
            return False, f"rang inattendu à la position {index} (trouvé {revision.sequence})"
        if revision.previous_hash != expected_previous:
            return False, f"chaînage rompu à la révision {revision.sequence}"
        recomputed = compute_hash(
            previous_hash=revision.previous_hash,
            sequence=revision.sequence,
            action=revision.action,
            snapshot=revision.snapshot,
        )
        if recomputed != revision.content_hash:
            return False, f"contenu altéré à la révision {revision.sequence}"
        expected_previous = revision.content_hash
    return True, None


async def count_revisions(db: AsyncSession, offer_id: int) -> int:
    """Nombre de révisions d'une offre."""
    return int(
        (
            await db.scalar(
                select(func.count(RateOfferRevision.id)).where(
                    RateOfferRevision.offer_id == offer_id
                )
            )
        )
        or 0
    )
