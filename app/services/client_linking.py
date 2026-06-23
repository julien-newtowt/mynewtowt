"""Rapprochement automatique compte plateforme ↔ client commercial (par e-mail).

Lorsqu'un prospect est créé depuis mytowt (inscription `ClientAccount`) ou
qu'une organisation Pipedrive est remontée en `Client`, on relie
automatiquement le compte au client commercial partageant la même adresse —
ou, à défaut, le même domaine e-mail professionnel (rapprochement unique,
domaines grand public exclus). Plusieurs comptes plateforme peuvent ainsi
pointer vers un même client commercial.

Le rapprochement n'écrase jamais un lien existant : il ne fait que renseigner
`commercial_client_id` quand il est vide. L'override manuel reste possible via
les routes link/unlink de la fiche client.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client_account import ClientAccount
from app.models.commercial import Client

logger = logging.getLogger(__name__)

# Domaines e-mail grand public : non significatifs pour un rapprochement société.
_GENERIC_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.fr",
        "hotmail.com",
        "hotmail.fr",
        "outlook.com",
        "outlook.fr",
        "live.com",
        "live.fr",
        "icloud.com",
        "me.com",
        "orange.fr",
        "free.fr",
        "wanadoo.fr",
        "sfr.fr",
        "laposte.net",
        "gmx.com",
        "proton.me",
        "protonmail.com",
    }
)


def _domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    dom = email.rsplit("@", 1)[1].strip().lower()
    return dom or None


async def find_client_for_email(db: AsyncSession, email: str | None) -> Client | None:
    """Client commercial correspondant à un e-mail (exact, puis domaine unique).

    1. Match exact sur ``Client.contact_email`` (insensible à la casse).
    2. Sinon, si le domaine n'est pas grand public et qu'un **seul** client
       partage ce domaine, on le retient. Ambiguïté → pas de rapprochement.
    """
    clean = (email or "").strip().lower()
    if not clean or "@" not in clean:
        return None

    exact = (
        await db.execute(select(Client).where(func.lower(Client.contact_email) == clean).limit(1))
    ).scalar_one_or_none()
    if exact is not None:
        return exact

    dom = _domain(clean)
    if not dom or dom in _GENERIC_DOMAINS:
        return None
    candidates = list(
        (await db.execute(select(Client).where(func.lower(Client.contact_email).like(f"%@{dom}"))))
        .scalars()
        .all()
    )
    return candidates[0] if len(candidates) == 1 else None


async def auto_link_account(db: AsyncSession, account: ClientAccount) -> Client | None:
    """Relie ``account`` à un client commercial par e-mail si non déjà lié.

    Renvoie le client lié (ou déjà lié), sinon None. Best-effort : ne lève pas.
    """
    if account is None:
        return None
    if account.commercial_client_id is not None:
        return await db.get(Client, account.commercial_client_id)
    try:
        client = await find_client_for_email(db, account.email)
    except Exception:
        logger.warning("auto_link_account failed for %r", getattr(account, "email", "?"))
        return None
    if client is not None:
        account.commercial_client_id = client.id
        await db.flush()
        logger.info("compte %s relié au client commercial #%s", account.email, client.id)
    return client


async def link_unlinked_accounts(db: AsyncSession) -> int:
    """Rapproche tous les comptes plateforme non liés (passe post-sync Pipedrive).

    Renvoie le nombre de comptes nouvellement reliés.
    """
    accounts = list(
        (
            await db.execute(
                select(ClientAccount).where(ClientAccount.commercial_client_id.is_(None))
            )
        )
        .scalars()
        .all()
    )
    linked = 0
    for acc in accounts:
        client = await find_client_for_email(db, acc.email)
        if client is not None:
            acc.commercial_client_id = client.id
            linked += 1
    if linked:
        await db.flush()
    return linked
