"""Isolation des grilles tarifaires négociées (constat sécurité C-1).

Le rattachement d'un compte plateforme à un client commercial ouvre l'accès à
**ses prix négociés**. Il ne doit donc jamais découler d'une adresse e-mail
auto-déclarée à l'inscription : sinon un tiers s'inscrivant avec le domaine —
voire l'e-mail exact — d'un client lit sa grille (taux, remises, options).

Ces tests verrouillent le comportement corrigé : la création de compte ne relie
rien, et le rapprochement n'est plus qu'une *suggestion* soumise à l'opérateur.
"""

from __future__ import annotations

import pytest

from app.models.commercial import Client
from app.services import client_account as account_svc
from app.services.client_linking import suggest_client_for_email, suggest_unlinked_matches


async def _client(db, *, name: str, email: str) -> Client:
    client = Client(name=name, client_type="freight_forwarder", contact_email=email)
    db.add(client)
    await db.flush()
    return client


@pytest.mark.asyncio
async def test_signup_never_links_by_email_domain(db):
    """Un inconnu du domaine d'un client ne récupère pas sa grille (C-1)."""
    await _client(db, name="BigCoffee", email="ops@bigcoffee.fr")

    account = await account_svc.create_account(
        db,
        email="intrus@bigcoffee.fr",
        password="motdepasse-long-2026",
        company_name="Peu importe",
    )

    assert account.commercial_client_id is None


@pytest.mark.asyncio
async def test_signup_never_links_even_on_exact_email_match(db):
    """Même un e-mail identique au contact ne suffit pas : il n'est pas prouvé."""
    await _client(db, name="BigCoffee", email="ops@bigcoffee.fr")

    account = await account_svc.create_account(
        db,
        email="ops@bigcoffee.fr",
        password="motdepasse-long-2026",
        company_name="BigCoffee",
    )

    assert account.commercial_client_id is None


@pytest.mark.asyncio
async def test_suggestion_is_exact_match_only_and_writes_nothing(db):
    """La suggestion ne retient que l'e-mail exact et ne relie jamais d'elle-même."""
    client = await _client(db, name="BigCoffee", email="ops@bigcoffee.fr")

    same_domain = await account_svc.create_account(
        db,
        email="autre@bigcoffee.fr",
        password="motdepasse-long-2026",
        company_name="Autre société",
    )
    exact = await account_svc.create_account(
        db,
        email="ops@bigcoffee.fr",
        password="motdepasse-long-2026",
        company_name="BigCoffee",
    )

    assert await suggest_client_for_email(db, "autre@bigcoffee.fr") is None
    suggested = await suggest_client_for_email(db, "ops@bigcoffee.fr")
    assert suggested is not None and suggested.id == client.id

    matches = await suggest_unlinked_matches(db)
    assert [a.id for a, _ in matches] == [exact.id]

    # Aucune écriture : les deux comptes restent non rattachés.
    assert same_domain.commercial_client_id is None
    assert exact.commercial_client_id is None
