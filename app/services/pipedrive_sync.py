"""Synchronisation Pipedrive → clients commerciaux.

Remonte les **organisations** Pipedrive dans la table ``commercial_clients``
(une organisation = un client). Rapprochement par ``pipedrive_org_id`` :
- org déjà liée → mise à jour du nom / adresse (les champs saisis à la main
  comme le contact ne sont pas écrasés) ;
- org inconnue → création d'un client (type par défaut ``freight_forwarder``).

Déclenché par le bouton « Synchroniser Pipedrive » sur /commercial/clients.
No-op propre si ``PIPEDRIVE_API_TOKEN`` n'est pas configuré.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.commercial import Client
from app.models.user import User
from app.utils import pipedrive

logger = logging.getLogger(__name__)

# Compteurs de deals présents sur l'objet organisation Pipedrive (fallback si
# la liste des deals est indisponible). Une org est « cliente » dès qu'elle a
# au moins un deal (ouvert ou clos), sur n'importe quel pipeline.
_DEAL_COUNT_KEYS = (
    "open_deals_count",
    "closed_deals_count",
    "related_open_deals_count",
    "related_closed_deals_count",
    "won_deals_count",
    "lost_deals_count",
)

# Classification du type de client à partir de l'« activité » de l'organisation
# Pipedrive : une activité commençant par « IFF » => transitaire (freight
# forwarder), sinon chargeur (shipper / client direct).
_FF_ACTIVITY_PREFIX = "IFF"
# Clé du champ personnalisé Pipedrive portant l'« activité » de l'org. Si non
# renseignée, on repère par balayage une valeur de champ commençant par IFF.
_ACTIVITY_FIELD_KEY = (os.getenv("PIPEDRIVE_ORG_ACTIVITY_KEY") or "").strip() or None


# Résolution nom de pays → code ISO 2, limitée aux pays réellement présents au
# portefeuille NEWTOWT (France, Europe de l'Ouest, Amériques, Afrique de l'Ouest,
# Asie du Sud-Est). Deux graphies par pays : celle de l'API en anglais et celle
# qu'un commercial saisit en français. Une entrée absente ne pose simplement
# aucun pays — c'est le comportement d'avant cette synchronisation.
_COUNTRY_BY_NAME: dict[str, str] = {
    "france": "FR",
    "belgium": "BE",
    "belgique": "BE",
    "switzerland": "CH",
    "suisse": "CH",
    "luxembourg": "LU",
    "united kingdom": "GB",
    "royaume-uni": "GB",
    "portugal": "PT",
    "spain": "ES",
    "espagne": "ES",
    "italy": "IT",
    "italie": "IT",
    "germany": "DE",
    "allemagne": "DE",
    "netherlands": "NL",
    "pays-bas": "NL",
    "united states": "US",
    "united states of america": "US",
    "états-unis": "US",
    "etats-unis": "US",
    "canada": "CA",
    "brazil": "BR",
    "brésil": "BR",
    "bresil": "BR",
    "colombia": "CO",
    "colombie": "CO",
    "peru": "PE",
    "pérou": "PE",
    "perou": "PE",
    "dominican republic": "DO",
    "république dominicaine": "DO",
    "martinique": "MQ",
    "guadeloupe": "GP",
    "morocco": "MA",
    "maroc": "MA",
    "senegal": "SN",
    "sénégal": "SN",
    "ivory coast": "CI",
    "côte d'ivoire": "CI",
    "cote d'ivoire": "CI",
    "ghana": "GH",
    "vietnam": "VN",
    "viet nam": "VN",
}


def _org_owner_id(org: dict) -> int | None:
    """Identifiant du propriétaire Pipedrive d'une organisation.

    ``owner_id`` est tantôt un entier, tantôt un objet développé
    (``{"id": 42, "email": …}``) selon l'endpoint et la version d'API.
    """
    raw = org.get("owner_id")
    if isinstance(raw, dict):
        raw = raw.get("id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _org_owner_email(org: dict) -> str | None:
    """E-mail du propriétaire Pipedrive, quand l'API le développe."""
    raw = org.get("owner_id")
    if isinstance(raw, dict):
        email = (raw.get("email") or "").strip().lower()
        return email or None
    return None


def _org_has_deal(org: dict) -> bool:
    """True si l'organisation Pipedrive porte au moins un deal (via compteurs)."""
    return any((org.get(k) or 0) for k in _DEAL_COUNT_KEYS)


def _deal_org_id(deal: dict) -> int | None:
    """Extrait l'``org_id`` d'un deal Pipedrive (int ou objet ``{value}``)."""
    raw = deal.get("org_id")
    if isinstance(raw, dict):
        raw = raw.get("value")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


# Champs standard (non « activité ») exclus du balayage de secours afin de ne
# pas classer un transitaire à tort sur la base de son nom/adresse.
_STANDARD_STR_KEYS = frozenset(
    {
        "name",
        "address",
        "address_country",
        "address_locality",
        "address_admin_area_level_1",
        "address_admin_area_level_2",
        "address_route",
        "address_subpremise",
        "address_postal_code",
        "owner_name",
        "cc_email",
    }
)


def _org_activity(org: dict) -> str:
    """Récupère l'« activité » de l'organisation Pipedrive.

    Priorité au champ personnalisé configuré (``PIPEDRIVE_ORG_ACTIVITY_KEY``) ;
    à défaut, on repère une valeur de champ (hors champs standard nom/adresse)
    commençant par « IFF » — le seul motif qui distingue un transitaire.
    """
    if _ACTIVITY_FIELD_KEY:
        return str(org.get(_ACTIVITY_FIELD_KEY) or "").strip()
    for key, value in org.items():
        if key in _STANDARD_STR_KEYS:
            continue
        if isinstance(value, str) and value.strip().upper().startswith(_FF_ACTIVITY_PREFIX):
            return value.strip()
    return ""


def _client_type_for(org: dict) -> str:
    """``freight_forwarder`` si l'activité commence par IFF, sinon ``shipper``."""
    if _org_activity(org).upper().startswith(_FF_ACTIVITY_PREFIX):
        return "freight_forwarder"
    return "shipper"


def is_configured() -> bool:
    return pipedrive.enabled()


async def push_deal_for(db: AsyncSession, entity) -> int | None:
    """COM-06 — pousse un Deal Pipedrive pour une **offre** ou une **commande**.

    ``entity`` porte ``client_id``, ``reference``, ``total_eur`` et
    ``pipedrive_deal_id`` (``RateOffer`` et ``Order`` partagent ces champs).

    - **No-op** si Pipedrive n'est pas configuré ;
    - **idempotent** : ne recrée pas de deal si ``pipedrive_deal_id`` est déjà
      renseigné (retourne l'existant) ;
    - **best-effort** : les erreurs réseau sont avalées par le client HTTP
      (retour ``None``), l'entité reste inchangée.

    L'organisation est résolue (find-or-create) depuis le client, le deal est
    créé sur le pipeline ``settings.pipedrive_pipeline_name`` (1er étage), avec
    le montant ``total_eur``. Retourne l'id du deal (existant ou créé) ou ``None``.
    """
    existing = getattr(entity, "pipedrive_deal_id", None)
    if existing or not pipedrive.enabled():
        return existing
    client = await db.get(Client, entity.client_id)
    if client is None or not (client.name or "").strip():
        return None
    org = await pipedrive.find_or_create_organization(client.name)
    org_id = org.get("id") if org else None
    pipeline_id = await pipedrive.find_pipeline_id(settings.pipedrive_pipeline_name)
    stage_id = await pipedrive.first_stage_id(pipeline_id) if pipeline_id else None
    value = float(entity.total_eur) if getattr(entity, "total_eur", None) is not None else None
    deal = await pipedrive.create_deal(
        title=f"{entity.reference} · {client.name}",
        org_id=org_id,
        value=value,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
    )
    deal_id = (deal or {}).get("id")
    if deal_id:
        entity.pipedrive_deal_id = deal_id
        await db.flush()
        return deal_id
    return None


def _country_code(org: dict) -> str | None:
    """Code pays ISO 3166-1 alpha-2 d'une organisation Pipedrive.

    ``address_country`` est un **libellé** (« France », « Brazil »), pas un
    code : on ne le retient que s'il fait déjà deux lettres, sinon on tente la
    résolution par nom. Sans correspondance sûre, on ne pose rien — un mauvais
    code pays fait afficher le mauvais drapeau et fausse les filtres.
    """
    raw = (org.get("address_country") or "").strip()
    if not raw:
        return None
    if len(raw) == 2 and raw.isalpha():
        return raw.upper()
    return _COUNTRY_BY_NAME.get(raw.casefold())


def _person_org_id(person: dict) -> int | None:
    """``org_id`` d'une personne Pipedrive (int ou objet développé)."""
    raw = person.get("org_id")
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get("id")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _contacts_by_org(persons: list[dict]) -> dict[int, dict]:
    """Contact retenu par organisation : nom, e-mail, téléphone.

    Une organisation peut porter plusieurs personnes. On garde la **première
    qui apporte au moins un moyen de contact** — un contact sans e-mail ni
    téléphone ne remplirait la fiche que d'un nom, et masquerait un contact
    complet arrivé plus tard dans la liste.
    """
    out: dict[int, dict] = {}
    for person in persons:
        org_id = _person_org_id(person)
        if org_id is None:
            continue
        email = pipedrive.primary_value(person.get("email"))
        phone = pipedrive.primary_value(person.get("phone"))
        name = (person.get("name") or "").strip() or None
        if not (email or phone):
            continue
        if org_id in out:
            continue
        out[org_id] = {"name": name, "email": email, "phone": phone}
    return out


def _apply_crm_field(client: Client, field: str, value: str | None) -> None:
    """Pose une valeur venue du CRM sans jamais effacer une saisie existante.

    Pipedrive est la source de la fiche client, mais une valeur **absente** du
    CRM n'est pas une valeur vide : écraser un e-mail saisi à la main par le
    silence de l'API serait une perte de donnée silencieuse.
    """
    if value:
        setattr(client, field, value)


async def sync_clients(db: AsyncSession) -> dict:
    """Upsert des organisations Pipedrive **ayant un deal** dans ``commercial_clients``.

    Seules les organisations avec au moins un deal (ouvert ou clos) sont
    remontées — les autres sont ignorées (``skipped``) pour ne pas polluer la
    liste clients.

    Renvoie ``{configured, created, updated, skipped, total, errors}``.
    """
    if not pipedrive.enabled():
        return {
            "configured": False,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "total": 0,
            "errors": 0,
        }

    orgs = await pipedrive.list_organizations()
    by_pd = {
        c.pipedrive_org_id: c
        for c in (await db.execute(select(Client))).scalars().all()
        if c.pipedrive_org_id is not None
    }
    # Comptes staff indexés par e-mail, pour rapprocher le propriétaire Pipedrive
    # d'un commercial attitré. Seuls les comptes actifs sont éligibles : attribuer
    # un client à un compte désactivé reviendrait à ne notifier personne.
    staff_by_email = {
        (u.email or "").strip().lower(): u.id
        for u in (await db.execute(select(User).where(User.is_active.is_(True)))).scalars().all()
        if u.email
    }

    # Source de vérité « a un deal sur n'importe quel pipeline » : on liste TOUS
    # les deals (tous pipelines, ouverts/gagnés/perdus) et on en déduit les org
    # qui en portent au moins un. C'est plus fiable que les compteurs parfois
    # absents de la liste des organisations (cause des clients manquants).
    deals = await pipedrive.list_deals()
    org_ids_with_deal: set[int] = {oid for d in deals if (oid := _deal_org_id(d)) is not None}

    # Contacts du CRM, indexés par organisation : ils alimentent le bloc
    # « Fiche client » (contact, e-mail, téléphone) qui restait vide.
    contacts = _contacts_by_org(await pipedrive.list_persons())
    synced_at = datetime.now(UTC)

    created = 0
    updated = 0
    skipped = 0
    errors = 0
    for org in orgs:
        try:
            pd_id = org.get("id")
            name = (org.get("name") or "").strip()
            if not pd_id or not name:
                continue
            # Règle métier : on remonte une organisation dès qu'elle a un deal
            # sur n'importe quel pipeline (liste des deals OU compteurs de
            # secours si la liste est indisponible).
            has_deal = int(pd_id) in org_ids_with_deal or _org_has_deal(org)
            if not has_deal:
                skipped += 1
                continue
            address = (org.get("address") or "").strip() or None
            # Pays : code ISO 2 quand Pipedrive le donne développé, sinon rien
            # (on ne devine pas un pays à partir d'une adresse libre).
            country = _country_code(org)
            contact = contacts.get(int(pd_id)) or {}
            client_type = _client_type_for(org)
            owner_id = _org_owner_id(org)
            # Commercial attitré : le propriétaire Pipedrive est rapproché d'un
            # compte staff par e-mail. Le rapprochement peut échouer (owner non
            # développé par l'API, commercial sans compte) — l'attribution reste
            # alors à faire à la main depuis la fiche client.
            assigned_id = staff_by_email.get(_org_owner_email(org) or "")
            existing = by_pd.get(int(pd_id))
            if existing is None:
                db.add(
                    Client(
                        name=name[:200],
                        client_type=client_type,
                        address=address,
                        country=country,
                        contact_name=(contact.get("name") or None),
                        contact_email=(contact.get("email") or None),
                        contact_phone=(contact.get("phone") or None),
                        pipedrive_org_id=int(pd_id),
                        pipedrive_owner_id=owner_id,
                        pipedrive_synced_at=synced_at,
                        assigned_user_id=assigned_id,
                        is_active=True,
                    )
                )
                created += 1
            else:
                # Mise à jour douce : nom + adresse + type (dérivé de l'activité
                # Pipedrive). On préserve les coordonnées saisies manuellement.
                existing.name = name[:200]
                existing.client_type = client_type
                _apply_crm_field(existing, "address", address)
                _apply_crm_field(existing, "country", country)
                _apply_crm_field(existing, "contact_name", contact.get("name"))
                _apply_crm_field(existing, "contact_email", contact.get("email"))
                _apply_crm_field(existing, "contact_phone", contact.get("phone"))
                existing.pipedrive_owner_id = owner_id
                existing.pipedrive_synced_at = synced_at
                # ⚠️ L'attribution manuelle fait foi : l'import ne renseigne le
                # commercial attitré que s'il est **vide**. Écraser un choix
                # d'organisation interne par une donnée CRM serait une perte
                # silencieuse — et Pipedrive n'a pas autorité sur qui suit le client.
                if existing.assigned_user_id is None and assigned_id is not None:
                    existing.assigned_user_id = assigned_id
                updated += 1
        except (ValueError, TypeError) as e:  # données Pipedrive inattendues
            errors += 1
            logger.warning("pipedrive sync: org ignorée (%s): %s", org.get("id"), e)

    await db.flush()

    # C-1 : plus de rapprochement automatique compte ↔ client commercial (il donne
    # accès à la grille négociée). On se contente de **compter** les correspondances
    # e-mail exactes à proposer à l'opérateur sur la fiche client.
    suggested = 0
    try:
        from app.services.client_linking import suggest_unlinked_matches

        suggested = len(await suggest_unlinked_matches(db))
    except Exception:
        logger.warning("post-sync account match suggestion failed", exc_info=True)

    result = {
        "configured": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "suggested": suggested,
        "total": len(orgs),
        "errors": errors,
    }
    logger.info("Pipedrive sync clients: %s", result)
    return result
