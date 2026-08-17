"""Registre de remise des originaux du connaissement — §5.1.

Cf. `docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md` §5.1.

## Pourquoi ce registre existe

Sans lui, le transporteur ne peut établir **ni à qui, ni quand, ni comment** il a
remis les originaux d'un connaissement. C'est précisément le dispositif dont
l'absence exclut la *misdelivery* de la couverture P&I.

## La règle qui structure tout ce module

**Les trois canaux n'ont pas la même valeur probante, et on ne les confond jamais.**

| Canal | Ce que ça prouve | Force |
|---|---|---|
| ``download`` | le document a été **consulté** | faible — un préchargement de lien ou un antivirus de messagerie suffit |
| ``client_confirmed`` | le client **déclare** avoir reçu | forte — c'est sa propre déclaration |
| ``ops_confirmed`` | NEWTOWT **atteste** d'une remise hors plateforme | intermédiaire — c'est un **repli**, tracé comme tel |

Conséquence directe : ``has_client_acknowledgement`` ignore délibérément les
téléchargements. Présenter un téléchargement comme une réception serait un
raisonnement faux au moment où il compte le plus — face à un assureur.

Le registre est **append-only** : on n'écrase pas un événement de remise, on en
ajoute un. Un registre qui se réécrit ne prouve rien.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.packing_list import BlDeliveryReceipt, PackingListBatch
from app.services.activity import record as activity_record

#: Moyens de remise proposés au repli Opérations. Liste **ouverte** (le champ reste
#: libre) : figer un vocabulaire empêcherait de consigner un cas non prévu, et un
#: registre incomplet vaut moins qu'un registre au vocabulaire imparfait.
SUGGESTED_MEANS = (
    "mail",
    "téléphone",
    "courrier",
    "coursier",
    "remise en main propre",
    "transitaire",
)

#: Nombre d'originaux — constant, cf. §5.1 (« Toujours 3 », aucun paramétrage).
NUMBER_OF_ORIGINALS = 3


class DeliveryReceiptError(Exception):
    """Erreur de saisie du registre de remise — jamais silencieuse."""


class MeansRequired(DeliveryReceiptError, ValueError):
    """Repli Opérations sans moyen de remise : l'attestation n'établirait rien."""


def is_deliverable(batch: PackingListBatch) -> bool:
    """Le document est-il un **original remisible** ?

    Seul un connaissement signé peut être remis : avant signature il n'existe aucun
    original, seulement un projet. Consigner le téléchargement d'un projet dans un
    registre de **remise** polluerait celui-ci d'événements qui ne concernent pas la
    remise des originaux.
    """
    from app.services.bl_workflow import FROZEN_STATES

    return batch.bl_state in FROZEN_STATES


async def record_download(
    db: AsyncSession,
    *,
    batch: PackingListBatch,
    client,
    ip: str | None = None,
) -> BlDeliveryReceipt | None:
    """Consigne un **accès** à un original. Renvoie ``None`` si rien à consigner.

    ⚠️ Ce n'est **pas** une réception. Cet événement est produit par un `GET`, donc
    potentiellement par un préchargement de lien, un antivirus de messagerie ou un
    scan — sans que personne n'ait lu quoi que ce soit. Il est consigné parce qu'un
    registre d'accès a de la valeur, mais il ne doit **jamais** être présenté comme
    une confirmation de réception (cf. ``has_client_acknowledgement``).

    Un téléchargement de **projet** n'est pas consigné du tout : il ne concerne pas
    la remise des originaux (cf. ``is_deliverable``).
    """
    if not is_deliverable(batch):
        return None
    receipt = BlDeliveryReceipt(
        batch_id=batch.id,
        channel=BlDeliveryReceipt.CHANNEL_DOWNLOAD,
        confirmed_at=datetime.now(UTC),
        confirmed_by_client_id=client.id,
        confirmed_by_name=getattr(client, "company_name", None) or getattr(client, "email", None),
        ip_address=ip,
    )
    db.add(receipt)
    await db.flush()
    return receipt


async def confirm_by_client(
    db: AsyncSession,
    *,
    batch: PackingListBatch,
    client,
    notes: str | None = None,
    ip: str | None = None,
) -> BlDeliveryReceipt:
    """Le client **déclare** avoir reçu les originaux. La preuve la plus forte.

    Refusé tant qu'aucun original n'existe : confirmer la réception d'un projet
    n'aurait aucun sens, et la déclaration serait inexploitable.
    """
    if not is_deliverable(batch):
        raise DeliveryReceiptError(
            "aucun original à confirmer : le connaissement n'est pas encore signé."
        )
    name = getattr(client, "company_name", None) or getattr(client, "email", None) or "client"
    receipt = BlDeliveryReceipt(
        batch_id=batch.id,
        channel=BlDeliveryReceipt.CHANNEL_CLIENT,
        confirmed_at=datetime.now(UTC),
        confirmed_by_client_id=client.id,
        confirmed_by_name=name,
        notes=(notes or "").strip() or None,
        ip_address=ip,
    )
    db.add(receipt)
    await db.flush()
    await activity_record(
        db,
        action="bl_delivery_confirmed_by_client",
        user_id=None,
        user_name=name,
        user_role="client",
        module="cargo",
        entity_type="packing_batch",
        entity_id=batch.id,
        entity_label=f"BL {batch.bl_number or batch.batch_number}",
        detail=f"réception des {NUMBER_OF_ORIGINALS} originaux confirmée par le client",
        ip_address=ip,
    )
    return receipt


async def confirm_by_ops(
    db: AsyncSession,
    *,
    batch: PackingListBatch,
    user,
    confirmed_at: datetime,
    means: str | None,
    notes: str | None = None,
    attachment_path: str | None = None,
    ip: str | None = None,
) -> BlDeliveryReceipt:
    """Repli Opérations : attester d'une remise **hors plateforme**.

    Le **moyen** est obligatoire — une attestation qui ne dit pas *comment* la remise
    a eu lieu n'établit rien face à un assureur. Levé avant toute écriture.

    L'événement est tracé **comme un repli** : ``channel='ops_confirmed'`` et
    ``confirmed_by_user_id`` renseigné (jamais ``confirmed_by_client_id``), de sorte
    qu'il ne puisse pas être relu comme une déclaration du client.
    """
    if not is_deliverable(batch):
        raise DeliveryReceiptError(
            "aucun original à attester : le connaissement n'est pas encore signé."
        )
    clean_means = " ".join((means or "").split())
    if not clean_means:
        raise MeansRequired(
            "indiquer le moyen de remise (mail, téléphone, courrier, coursier…) — "
            "une attestation sans moyen n'établit rien."
        )
    name = user.full_name or user.username
    receipt = BlDeliveryReceipt(
        batch_id=batch.id,
        channel=BlDeliveryReceipt.CHANNEL_OPS,
        confirmed_at=confirmed_at,
        means=clean_means,
        confirmed_by_user_id=user.id,
        confirmed_by_name=name,
        notes=(notes or "").strip() or None,
        attachment_path=attachment_path,
        ip_address=ip,
    )
    db.add(receipt)
    await db.flush()
    await activity_record(
        db,
        action="bl_delivery_confirmed_by_ops",
        user_id=user.id,
        user_name=name,
        user_role=getattr(user, "role", None),
        module="cargo",
        entity_type="packing_batch",
        entity_id=batch.id,
        entity_label=f"BL {batch.bl_number or batch.batch_number}",
        detail=(
            f"remise attestée PAR NEWTOWT (repli hors plateforme) le "
            f"{confirmed_at:%Y-%m-%d %H:%M} par {clean_means}"
            + (f" — pièce jointe : {attachment_path}" if attachment_path else "")
        ),
        ip_address=ip,
    )
    return receipt


async def receipts_for_batch(
    db: AsyncSession, *, batch_id: int, channels: tuple[str, ...] | None = None
) -> list[BlDeliveryReceipt]:
    """Événements de remise d'un lot, du plus récent au plus ancien."""
    stmt = (
        select(BlDeliveryReceipt)
        .where(BlDeliveryReceipt.batch_id == batch_id)
        .order_by(BlDeliveryReceipt.confirmed_at.desc(), BlDeliveryReceipt.id.desc())
    )
    if channels is not None:
        stmt = stmt.where(BlDeliveryReceipt.channel.in_(channels))
    return list((await db.execute(stmt)).scalars().all())


async def has_client_acknowledgement(db: AsyncSession, *, batch_id: int) -> bool:
    """La remise est-elle **attestée** ? Un téléchargement ne compte PAS.

    🔴 C'est la fonction où l'erreur coûterait le plus cher. Compter les
    téléchargements ferait passer pour « reçu » un document qu'un préchargement de
    lien a simplement effleuré — et cette affirmation serait produite au moment où
    elle compte le plus, face à un assureur en réclamation *misdelivery*.

    Seuls ``client_confirmed`` (déclaration du client) et ``ops_confirmed``
    (attestation de repli, avec son moyen) comptent.
    """
    rows = await receipts_for_batch(
        db,
        batch_id=batch_id,
        channels=(BlDeliveryReceipt.CHANNEL_CLIENT, BlDeliveryReceipt.CHANNEL_OPS),
    )
    return bool(rows)


async def delivery_status(db: AsyncSession, *, batch_id: int) -> dict:
    """Synthèse lisible pour un écran, sans jamais surestimer la preuve."""
    rows = await receipts_for_batch(db, batch_id=batch_id)
    downloads = [r for r in rows if r.channel == BlDeliveryReceipt.CHANNEL_DOWNLOAD]
    acks = [r for r in rows if r.channel != BlDeliveryReceipt.CHANNEL_DOWNLOAD]
    return {
        "acknowledged": bool(acks),
        # Le dernier événement ATTESTÉ, pas le dernier événement tout court.
        "acknowledged_at": acks[0].confirmed_at if acks else None,
        "acknowledged_channel": acks[0].channel if acks else None,
        "download_count": len(downloads),
        "first_download_at": downloads[-1].confirmed_at if downloads else None,
        "last_download_at": downloads[0].confirmed_at if downloads else None,
        "receipts": rows,
    }
