"""Comptes-ancres (P11) — tests d'intégration.

Couvre les attributs « compte-ancre » portés par le client commercial
(``is_anchor`` / ``annual_volume_commitment`` / ``capacity_priority`` /
``co_branding_status``) : valeurs par défaut, écriture via le back-office
commercial, validation, et présence des constantes dans la fiche client.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

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
async def test_client_anchor_update_persists_all_fields(db, staff_user):
    from app.routers.commercial_router import client_anchor_update

    c = await _client(db)
    resp = await client_anchor_update(
        c.id,
        _Req(),
        is_anchor=True,
        annual_volume_commitment="2400",
        capacity_priority="2",
        co_branding_status="active",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    await db.refresh(c)
    assert c.is_anchor is True
    assert c.annual_volume_commitment == 2400
    assert c.capacity_priority == 2
    assert c.co_branding_status == "active"
    assert c.capacity_priority_display == "Stratégique"
    assert c.co_branding_label == "Actif"


@pytest.mark.asyncio
async def test_client_anchor_update_can_unset(db, staff_user):
    from app.routers.commercial_router import client_anchor_update

    c = await _client(db, is_anchor=True, capacity_priority=2, co_branding_status="active")
    await client_anchor_update(
        c.id,
        _Req(),
        is_anchor=False,
        annual_volume_commitment="",  # vide → None
        capacity_priority="0",
        co_branding_status="none",
        db=db,
        user=staff_user,
    )
    await db.refresh(c)
    assert c.is_anchor is False
    assert c.annual_volume_commitment is None
    assert c.capacity_priority == 0
    assert c.co_branding_status == "none"


@pytest.mark.asyncio
async def test_client_anchor_update_rejects_bad_cobranding(db, staff_user):
    from app.routers.commercial_router import client_anchor_update

    c = await _client(db)
    with pytest.raises(HTTPException) as exc:
        await client_anchor_update(
            c.id,
            _Req(),
            is_anchor=True,
            annual_volume_commitment=None,
            capacity_priority="1",
            co_branding_status="bogus",
            db=db,
            user=staff_user,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_client_anchor_update_rejects_negative_volume(db, staff_user):
    from app.routers.commercial_router import client_anchor_update

    c = await _client(db)
    with pytest.raises(HTTPException) as exc:
        await client_anchor_update(
            c.id,
            _Req(),
            is_anchor=True,
            annual_volume_commitment="-5",
            capacity_priority="0",
            co_branding_status="none",
            db=db,
            user=staff_user,
        )
    assert exc.value.status_code == 400
