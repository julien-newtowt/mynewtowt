"""Rapprochement compte plateforme ↔ client commercial — **suggestion seule**.

⚠️ Sécurité (C-1) : le champ ``ClientAccount.commercial_client_id`` donne accès à
la **grille tarifaire négociée** du client commercial (prix confidentiels). Il ne
doit donc **jamais** être dérivé d'une adresse e-mail auto-déclarée à l'inscription
— sinon un tiers s'inscrivant avec le domaine (voire l'e-mail exact) d'un client
lit sa grille. Le rattachement est un **acte explicite d'un opérateur** ``commercial:M``
via les routes ``/commercial/clients/{id}/accounts/link`` (audité).

Ce module ne fait plus que **proposer** une correspondance (match e-mail exact) pour
pré-remplir l'écran de rattachement staff. Il n'écrit jamais ``commercial_client_id``.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client_account import ClientAccount
from app.models.commercial import Client

logger = logging.getLogger(__name__)


async def suggest_client_for_email(db: AsyncSession, email: str | None) -> Client | None:
    """Client commercial dont ``contact_email`` **égale exactement** ``email``.

    Suggestion à confirmer par un opérateur — **jamais** appliquée automatiquement.
    Le rapprochement par *domaine* a été retiré (C-1) : trop laxiste pour un accès
    à des prix négociés (un e-mail pro partagé par plusieurs sociétés, un domaine
    deviné). Seul un match e-mail exact est proposé, et il reste indicatif.
    """
    clean = (email or "").strip().lower()
    if not clean or "@" not in clean:
        return None
    return (
        await db.execute(select(Client).where(func.lower(Client.contact_email) == clean).limit(1))
    ).scalar_one_or_none()


async def suggest_unlinked_matches(
    db: AsyncSession,
) -> list[tuple[ClientAccount, Client]]:
    """Comptes plateforme non liés ayant une correspondance e-mail exacte (suggestions).

    Ne modifie rien : retourne des couples (compte, client suggéré) pour affichage
    à l'opérateur, qui décide de relier ou non.
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
    suggestions: list[tuple[ClientAccount, Client]] = []
    for acc in accounts:
        client = await suggest_client_for_email(db, acc.email)
        if client is not None:
            suggestions.append((acc, client))
    return suggestions
