"""Workflow du Bill of Lading — transitions d'état, gel, révisions, journalisation.

Cf. `docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md`.

Le cycle demandé :

    (aucun) → draft → client_validated → master_signed → final

**Le point de gel est la signature du commandant, pas l'émission.** Avant
signature, un connaissement n'engage personne — l'expéditeur doit pouvoir corriger
sa packing list. Après, il engage le transporteur : plus aucune édition, seulement
une **révision numérotée** qui annule explicitement la précédente.

Conséquence directe, la **règle de régression** : toute modification du contenu au
stade ``client_validated`` **annule la validation** et ramène à ``draft``. Une
validation porte sur un contenu précis, pas sur un dossier ouvert — présenter une
validation client obtenue sur un contenu qui a changé depuis serait faux.

Toutes les transitions sont tracées **deux fois** : dans ``activity_logs`` (journal
append-only consulté depuis `/admin/activity-logs`, celui qu'un P&I club réclame) et
dans ``PackingListAudit`` (piste champ par champ, propre au dossier). Les deux
pistes ne servent pas le même lecteur.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.commercial import Order
from app.models.packing_list import PackingList, PackingListBatch
from app.services.activity import record as activity_record
from app.services.packing_list import record_audit

# ── États et transitions autorisées ──────────────────────────────────────────
DRAFT = "draft"
CLIENT_VALIDATED = "client_validated"
MASTER_SIGNED = "master_signed"
FINAL = "final"

BL_STATES: tuple[str, ...] = (DRAFT, CLIENT_VALIDATED, MASTER_SIGNED, FINAL)

# `None` = aucun BL généré. La table est explicite plutôt que déduite d'un ordre :
# une machine à états écrite noir sur blanc se relit, un `index()` ne se relit pas.
ALLOWED_TRANSITIONS: dict[str | None, tuple[str, ...]] = {
    None: (DRAFT,),
    DRAFT: (CLIENT_VALIDATED,),
    CLIENT_VALIDATED: (MASTER_SIGNED, DRAFT),  # DRAFT = retour par régression
    MASTER_SIGNED: (FINAL,),
    FINAL: (),
}

# À partir de cet état, le contenu est gelé : plus d'édition, seulement révision.
FROZEN_STATES: frozenset[str] = frozenset({MASTER_SIGNED, FINAL})

# Champs qui définissent juridiquement le document. Toute altération de l'un
# d'eux après signature doit être détectable par le hash.
SIGNED_FIELDS: tuple[str, ...] = (
    "bl_number",
    "batch_number",
    "shipper_name",
    "consignee_name",
    "notify_name",
    "description_of_goods",
    "marks_and_numbers",
    "hs_code",
    "pallet_count",
    "pallet_format",
    "weight_kg",
    "cases_quantity",
)


class BlWorkflowError(Exception):
    """Erreur de workflow BL — jamais silencieuse."""


class InvalidTransition(BlWorkflowError):
    """Transition d'état non autorisée par la machine à états."""


class BlFrozen(BlWorkflowError):
    """Le document est signé : il ne se corrige plus que par révision."""


class ValidatorConflict(BlWorkflowError):
    """Validation à la fois client et staff — interdit."""


def is_frozen(batch: PackingListBatch) -> bool:
    """Le contenu du lot est-il gelé par une signature ?

    ⚠️ À appeler **en plus** de ``packing_list.can_modify`` (qui ne regarde que le
    verrou de la packing list) : le gel du BL est porté par le **lot**, pas par la
    packing list, et les deux verrous sont indépendants.
    """
    return batch.bl_state in FROZEN_STATES


def assert_transition(current: str | None, target: str) -> None:
    """Lève ``InvalidTransition`` si le passage n'est pas prévu."""
    if target not in BL_STATES:
        raise InvalidTransition(f"état inconnu : {target!r}")
    allowed = ALLOWED_TRANSITIONS.get(current, ())
    if target not in allowed:
        raise InvalidTransition(
            f"transition refusée : {current or 'aucun'} → {target}. "
            f"Autorisé depuis {current or 'aucun'} : {', '.join(allowed) or 'rien'}."
        )


def signature_payload(batch: PackingListBatch) -> str:
    """Sérialisation canonique et stable des champs signés.

    Ordre **fixe** (celui de ``SIGNED_FIELDS``) et séparateurs explicites : deux
    contenus différents ne doivent jamais produire la même chaîne. Un `repr` de
    dict, dont l'ordre pourrait changer, ne conviendrait pas.
    """
    parts = []
    for field in SIGNED_FIELDS:
        value = getattr(batch, field, None)
        parts.append(f"{field}={'' if value is None else value}")
    return "|".join(parts)


def compute_signature_hash(batch: PackingListBatch) -> str:
    """SHA-256 du contenu signé — détecte toute altération postérieure."""
    return hashlib.sha256(signature_payload(batch).encode("utf-8")).hexdigest()


def signature_is_intact(batch: PackingListBatch) -> bool | None:
    """Le contenu correspond-il encore à ce qui a été signé ?

    ``None`` si le lot n'est pas signé — **pas** ``True`` : « non signé » et
    « signé et intact » sont deux choses différentes, et les confondre reviendrait
    à affirmer une intégrité qu'on ne peut pas vérifier.
    """
    if not batch.bl_signature_hash:
        return None
    return compute_signature_hash(batch) == batch.bl_signature_hash


async def batches_for_leg(
    db: AsyncSession, *, leg_id: int, states: tuple[str | None, ...] | None = None
) -> list[PackingListBatch]:
    """Lots portant un BL sur un leg donné, filtrés par état.

    La jointure reprend **exactement** la règle COM-11 de
    ``packing_list._count_issued_bls_for_leg`` : leg épinglé sur la packing list
    prioritaire, repli sur ``order``/``booking`` pour les lignes héritées. Toute
    autre convention de rattachement ferait apparaître ou disparaître des
    connaissements de l'écran du commandant selon la requête, ce qui est
    inacceptable pour un registre.
    """
    stmt = (
        select(PackingListBatch)
        .join(PackingList, PackingListBatch.packing_list_id == PackingList.id)
        .outerjoin(Order, PackingList.order_id == Order.id)
        .outerjoin(Booking, PackingList.booking_id == Booking.id)
        .where(PackingListBatch.bl_number.is_not(None))
        .where(func.coalesce(PackingList.leg_id, Order.leg_id, Booking.leg_id) == leg_id)
        .order_by(PackingListBatch.bl_number)
    )
    if states is not None:
        stmt = stmt.where(PackingListBatch.bl_state.in_(states))
    return list((await db.execute(stmt)).scalars().all())


async def _trace(
    db: AsyncSession,
    batch: PackingListBatch,
    *,
    action: str,
    detail: str,
    actor_name: str,
    actor_id: int | None,
    actor_role: str | None,
    ip: str | None,
) -> None:
    """Trace une transition dans les DEUX pistes.

    `activity_logs` sert l'audit transverse (qui a fait quoi dans l'application) ;
    `PackingListAudit` sert le dossier (que s'est-il passé sur CE lot). Un lecteur
    externe — assureur, P&I club — part du premier ; les Opérations partent du
    second.
    """
    await activity_record(
        db,
        action=action,
        user_id=actor_id,
        user_name=actor_name,
        user_role=actor_role,
        module="cargo",
        entity_type="packing_batch",
        entity_id=batch.id,
        entity_label=f"BL {batch.bl_number or f'lot {batch.batch_number}'}",
        detail=detail,
        ip_address=ip,
    )
    await record_audit(
        db,
        packing_list_id=batch.packing_list_id,
        batch_id=batch.id,
        actor="staff",
        actor_name=actor_name,
        field="bl_state",
        old_value=None,
        new_value=f"{action} — {detail}",
    )


async def generate_draft(
    db: AsyncSession,
    *,
    pl: PackingList,
    batch: PackingListBatch,
    leg,
    user,
    ip: str | None = None,
) -> str:
    """Génère le draft et attribue le numéro de BL. Idempotent sur l'état.

    Le numéro est attribué **ici** et ne bouge plus : il identifie le dossier, pas
    une version. Une révision ultérieure porte le même numéro suffixé.
    """
    from app.services.packing_list import assign_bl_number

    if batch.bl_state is not None:
        # Déjà généré : on ne repasse pas par `draft` sans le dire.
        raise InvalidTransition(
            f"un BL existe déjà pour ce lot (état {batch.bl_state}) — "
            "utiliser une révision pour le corriger."
        )
    assert_transition(batch.bl_state, DRAFT)

    number = await assign_bl_number(db, pl, batch, leg)
    # Le nom vient de la variable locale, pas de la colonne : celle-ci est nullable
    # et une trace d'audit sans acteur nommé ne vaut rien.
    issuer_name: str = user.full_name or user.username
    batch.bl_state = DRAFT
    batch.bl_draft_at = datetime.now(UTC)
    batch.bl_issued_by_id = user.id
    batch.bl_issued_by_name = issuer_name
    await db.flush()

    await _trace(
        db,
        batch,
        action="bl_draft_generated",
        detail=f"draft généré, numéro {number}",
        actor_name=issuer_name,
        actor_id=user.id,
        actor_role=getattr(user, "role", None),
        ip=ip,
    )
    return number


async def validate_by_client(
    db: AsyncSession,
    *,
    batch: PackingListBatch,
    client=None,
    on_behalf_user=None,
    ip: str | None = None,
) -> None:
    """Validation du draft par le client, ou par le staff **pour son compte**.

    Exactement l'un des deux acteurs. Le repli staff existe parce que
    ``Booking.client_account_id`` est nullable (réservation saisie côté staff pour
    un client non inscrit) — mais il est tracé **comme tel** : jamais de validation
    silencieuse présentée comme venant du client.
    """
    if (client is None) == (on_behalf_user is None):
        raise ValidatorConflict(
            "exactement un validateur attendu : le client OU le staff pour son compte"
        )
    assert_transition(batch.bl_state, CLIENT_VALIDATED)

    batch.bl_state = CLIENT_VALIDATED
    batch.bl_client_validated_at = datetime.now(UTC)
    name: str
    if client is not None:
        batch.bl_client_validated_by_id = client.id
        batch.bl_validated_on_behalf_by_id = None
        # Repli final explicite : les deux champs peuvent être vides, et une
        # validation tracée sans acteur nommé serait inexploitable en audit.
        name = getattr(client, "company_name", None) or getattr(client, "email", None) or "client"
        detail = f"validé par le client ({name})"
    else:
        batch.bl_validated_on_behalf_by_id = on_behalf_user.id
        batch.bl_client_validated_by_id = None
        name = on_behalf_user.full_name or on_behalf_user.username
        detail = f"validé par NEWTOWT POUR LE COMPTE du client ({name})"
    batch.bl_client_validated_by = name
    await db.flush()

    await _trace(
        db,
        batch,
        action="bl_client_validated",
        detail=detail,
        actor_name=name,
        actor_id=on_behalf_user.id if on_behalf_user is not None else None,
        actor_role="client" if client is not None else getattr(on_behalf_user, "role", None),
        ip=ip,
    )


async def sign_by_master(
    db: AsyncSession, *, batch: PackingListBatch, user, ip: str | None = None
) -> str:
    """Signature du commandant — **point de gel**. Renvoie le hash calculé."""
    assert_transition(batch.bl_state, MASTER_SIGNED)

    signer_name: str = user.full_name or user.username
    batch.bl_state = MASTER_SIGNED
    batch.bl_signed_at = datetime.now(UTC)
    batch.bl_signed_by_id = user.id
    batch.bl_signed_by_name = signer_name
    signature = compute_signature_hash(batch)
    batch.bl_signature_hash = signature
    await db.flush()

    await _trace(
        db,
        batch,
        action="bl_master_signed",
        detail=f"signé par le commandant, empreinte {signature[:12]}…",
        actor_name=signer_name,
        actor_id=user.id,
        actor_role=getattr(user, "role", None),
        ip=ip,
    )
    return signature


@dataclass(frozen=True)
class BulkSignResult:
    """Compte rendu **exact** d'une signature groupée.

    Deux listes plutôt qu'un compteur : le commandant doit savoir **lesquels** il
    a signés et **lesquels** ne l'ont pas été, avec la raison. Un simple « 11
    signés » sur 12 sélectionnés laisserait croire à une réussite complète.
    """

    signed: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...]  # (numéro de BL, raison)

    @property
    def is_complete(self) -> bool:
        return not self.skipped


async def sign_many(
    db: AsyncSession, *, batches: list[PackingListBatch], user, ip: str | None = None
) -> BulkSignResult:
    """Signe plusieurs connaissements d'un geste (§5.2 — « tout signer »).

    Un lot non signable (revenu à ``draft`` entre l'affichage de l'écran et la
    validation du formulaire, ou déjà signé) est **écarté avec sa raison**, sans
    faire échouer les autres : le commandant qui signe douze connaissements ne
    doit pas en perdre onze parce que le douzième a régressé. Mais l'écran le dit.
    """
    signed: list[str] = []
    skipped: list[tuple[str, str]] = []
    for batch in batches:
        label = batch.bl_number or f"lot {batch.batch_number}"
        try:
            await sign_by_master(db, batch=batch, user=user, ip=ip)
        except InvalidTransition as exc:
            skipped.append((label, str(exc)))
        else:
            signed.append(label)
    return BulkSignResult(signed=tuple(signed), skipped=tuple(skipped))


async def issue_final(
    db: AsyncSession, *, batch: PackingListBatch, user, ip: str | None = None
) -> None:
    """Émission du BL final au client."""
    assert_transition(batch.bl_state, FINAL)

    # Garde-fou : ne jamais émettre un final dont le contenu a bougé depuis la
    # signature. Sans ce contrôle, le hash existerait sans rien protéger.
    if signature_is_intact(batch) is False:
        raise BlFrozen(
            "le contenu a été altéré depuis la signature — émission refusée, "
            "une révision numérotée est nécessaire."
        )
    batch.bl_state = FINAL
    await db.flush()

    await _trace(
        db,
        batch,
        action="bl_issued_final",
        detail="BL final émis au client",
        actor_name=user.full_name or user.username,
        actor_id=user.id,
        actor_role=getattr(user, "role", None),
        ip=ip,
    )


async def invalidate_validation_on_edit(
    db: AsyncSession, *, batch: PackingListBatch, actor_name: str, ip: str | None = None
) -> bool:
    """Règle de régression : une modification annule la validation client.

    Renvoie ``True`` si l'état a effectivement été ramené à ``draft``.

    Lève ``BlFrozen`` si le lot est signé — à ce stade la correction ne passe plus
    par l'édition. C'est le garde-fou à appeler depuis **tout** chemin d'écriture
    sur un lot, staff comme portail expéditeur.
    """
    if is_frozen(batch):
        raise BlFrozen(
            f"BL {batch.bl_number or batch.batch_number} signé : "
            "corriger par révision numérotée, pas par édition."
        )
    if batch.bl_state != CLIENT_VALIDATED:
        return False

    batch.bl_state = DRAFT
    batch.bl_client_validated_at = None
    batch.bl_client_validated_by_id = None
    batch.bl_validated_on_behalf_by_id = None
    previous = batch.bl_client_validated_by
    batch.bl_client_validated_by = None
    await db.flush()

    await _trace(
        db,
        batch,
        action="bl_validation_invalidated",
        detail=(
            "contenu modifié après validation — retour à draft, "
            f"validation de « {previous} » annulée"
        ),
        actor_name=actor_name,
        actor_id=None,
        actor_role=None,
        ip=ip,
    )
    return True
