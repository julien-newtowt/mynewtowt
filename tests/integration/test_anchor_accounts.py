"""Client stratégique (ex- « compte-ancre » P11) — tests d'intégration.

Les quatre attributs de P11 subsistent **en base** (aucune donnée détruite),
mais un seul est encore écrit et affiché : ``is_anchor``, rebaptisé *client
stratégique* (COM-12, ADR-015). Les trois autres — engagement de volume annuel,
rang de priorité capacité, statut de co-branding — n'étaient consommés par
aucune règle de l'application : ni allocation de cale, ni facturation, ni tri.
Trois champs saisis que rien n'applique finissent par être crus.
"""

from __future__ import annotations

import pytest

from app.models.commercial import Client


class _Req:
    headers: dict[str, str] = {}

    class client:
        host = "127.0.0.1"


async def _client(db, **kw):
    c = Client(name=kw.pop("name", "Client X"), client_type=kw.pop("client_type", "shipper"), **kw)
    db.add(c)
    await db.flush()
    await db.refresh(c)
    return c


@pytest.mark.asyncio
async def test_anchor_fields_default_to_standard(db):
    """Un client neuf n'est pas un compte-ancre (défauts serveur appliqués)."""
    c = await _client(db)
    assert c.is_anchor is False
    assert c.capacity_priority == 0
    assert c.co_branding_status == "none"
    assert c.annual_volume_commitment is None
    # propriétés d'affichage
    assert c.capacity_priority_display == "Standard"
    assert c.co_branding_label == "Aucun"


@pytest.mark.asyncio
async def test_strategic_flag_is_set_and_unset(db, staff_user):
    """COM-12 — il ne reste qu'une case à cocher : *client stratégique*.

    Les trois attributs qui l'accompagnaient (engagement de volume, rang de
    priorité capacité, co-branding) n'étaient consommés par **aucune** règle :
    ni allocation de cale, ni facturation, ni tri ne les lisaient. Ils ne sont
    plus exposés ni écrits — les colonnes restent en base (cf. ADR-015).
    """
    from app.routers.commercial_router import client_anchor_update

    c = await _client(db)
    resp = await client_anchor_update(c.id, _Req(), is_anchor=True, db=db, user=staff_user)
    assert resp.status_code == 303
    await db.refresh(c)
    assert c.is_anchor is True

    await client_anchor_update(c.id, _Req(), is_anchor=False, db=db, user=staff_user)
    await db.refresh(c)
    assert c.is_anchor is False


@pytest.mark.asyncio
async def test_anchor_update_no_longer_writes_the_dead_attributes(db, staff_user):
    """Un client déjà porteur d'anciennes valeurs les conserve telles quelles.

    La bascule ne détruit rien : elle cesse d'écrire et d'afficher. Une valeur
    saisie avant COM-12 reste lisible en base pour qui l'interroge.
    """
    from app.routers.commercial_router import client_anchor_update

    c = await _client(db, annual_volume_commitment=2400, capacity_priority=2)
    await client_anchor_update(c.id, _Req(), is_anchor=True, db=db, user=staff_user)
    await db.refresh(c)
    assert c.annual_volume_commitment == 2400
    assert c.capacity_priority == 2
