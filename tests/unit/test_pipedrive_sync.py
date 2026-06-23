"""Tests de la synchro Pipedrive → clients (upsert par pipedrive_org_id)."""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.commercial import Client
from app.services import pipedrive_sync
from app.utils import pipedrive


def test_sync_clients_upsert(monkeypatch) -> None:
    orgs = [
        # 101 : transitaire — activité (champ custom) commençant par IFF.
        {
            "id": 101,
            "name": "Acme Forwarding",
            "address": "12 Dock Rd, Le Havre",
            "open_deals_count": 2,
            "custom_activity": "IFF - commissionnaire",
        },
        # 102 : chargeur direct (pas d'activité IFF).
        {"id": 102, "name": "Café Brasil Imports", "address": None, "won_deals_count": 1},
        {
            "id": 104,
            "name": "Prospect sans deal",
            "open_deals_count": 0,
            "closed_deals_count": 0,
        },  # ignoré : aucun deal
        {"id": None, "name": "ignored (no id)"},
        {"id": 103, "name": ""},  # ignoré (pas de nom)
    ]
    monkeypatch.setattr(pipedrive, "enabled", lambda: True)

    async def _fake_list(*, max_items=1000):
        return orgs

    async def _fake_deals(*, max_items=10000):
        return []  # détection via compteurs de deals (fallback)

    monkeypatch.setattr(pipedrive, "list_organizations", _fake_list)
    monkeypatch.setattr(pipedrive, "list_deals", _fake_deals)

    async def _run():
        eng = create_async_engine("sqlite+aiosqlite://")
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                # Un client déjà lié à l'org 101 (contact saisi à la main).
                s.add(
                    Client(
                        name="Acme (ancien nom)",
                        client_type="shipper",
                        contact_email="ops@acme.test",
                        pipedrive_org_id=101,
                    )
                )
                await s.flush()

                r1 = await pipedrive_sync.sync_clients(s)
                assert r1["configured"] is True
                assert r1["created"] == 1  # org 102 (a un deal gagné)
                assert r1["updated"] == 1  # org 101 (a des deals ouverts)
                assert r1["skipped"] == 1  # org 104 (aucun deal)
                assert r1["total"] == 5

                clients = {
                    c.pipedrive_org_id: c for c in (await s.execute(select(Client))).scalars().all()
                }
                assert set(clients) == {101, 102}  # 104 (sans deal) non importé
                # 101 : nom mis à jour, contact manuel préservé, type dérivé de
                # l'activité IFF → freight_forwarder.
                assert clients[101].name == "Acme Forwarding"
                assert clients[101].contact_email == "ops@acme.test"
                assert clients[101].client_type == "freight_forwarder"
                # 102 : pas d'activité IFF → chargeur (shipper)
                assert clients[102].client_type == "shipper"

                # 2e passage : idempotent (aucune création)
                r2 = await pipedrive_sync.sync_clients(s)
                assert r2["created"] == 0
                count = len((await s.execute(select(Client))).scalars().all())
                assert count == 2
        finally:
            await eng.dispose()

    asyncio.run(_run())


def test_sync_clients_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(pipedrive, "enabled", lambda: False)

    async def _run():
        eng = create_async_engine("sqlite+aiosqlite://")
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                r = await pipedrive_sync.sync_clients(s)
                assert r == {
                    "configured": False,
                    "created": 0,
                    "updated": 0,
                    "skipped": 0,
                    "total": 0,
                    "errors": 0,
                }
        finally:
            await eng.dispose()

    asyncio.run(_run())


# ─────────────────────────── COM-06 — push Deal sur offre/commande ───────────


def test_push_deal_for_offer(monkeypatch) -> None:
    """Une offre émise crée un Deal Pipedrive (org find-or-create + montant)."""
    from decimal import Decimal

    from app.models.commercial import RateOffer

    async def _run():
        eng = create_async_engine("sqlite+aiosqlite://")
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                client = Client(name="ACME Ltd", client_type="shipper")
                s.add(client)
                await s.flush()
                offer = RateOffer(
                    reference="OFF-1",
                    client_id=client.id,
                    title="Offre ACME",
                    status="sent",
                    total_eur=Decimal("9600"),
                )
                s.add(offer)
                await s.flush()

                monkeypatch.setattr(pipedrive, "enabled", lambda: True)
                created: dict = {}

                async def _foc(name, **kw):
                    return {"id": 55, "name": name}

                async def _pid(name):
                    return 7

                async def _stage(pid):
                    return 3

                async def _deal(title, **kw):
                    created.update({"title": title, **kw})
                    return {"id": 999}

                monkeypatch.setattr(pipedrive, "find_or_create_organization", _foc)
                monkeypatch.setattr(pipedrive, "find_pipeline_id", _pid)
                monkeypatch.setattr(pipedrive, "first_stage_id", _stage)
                monkeypatch.setattr(pipedrive, "create_deal", _deal)

                did = await pipedrive_sync.push_deal_for(s, offer)
                assert did == 999 and offer.pipedrive_deal_id == 999
                assert created["org_id"] == 55
                assert created["value"] == 9600.0
                assert created["pipeline_id"] == 7 and created["stage_id"] == 3
                assert "OFF-1" in created["title"]

                # Idempotent : un 2e appel ne recrée pas de deal.
                created.clear()
                did2 = await pipedrive_sync.push_deal_for(s, offer)
                assert did2 == 999 and not created
        finally:
            await eng.dispose()

    asyncio.run(_run())


def test_push_deal_for_noop_when_disabled(monkeypatch) -> None:
    """Pipedrive non configuré → no-op (aucun deal, entité inchangée)."""
    from app.models.commercial import RateOffer

    async def _run():
        eng = create_async_engine("sqlite+aiosqlite://")
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                client = Client(name="ACME", client_type="shipper")
                s.add(client)
                await s.flush()
                offer = RateOffer(reference="OFF-2", client_id=client.id, title="X", status="draft")
                s.add(offer)
                await s.flush()
                monkeypatch.setattr(pipedrive, "enabled", lambda: False)
                assert await pipedrive_sync.push_deal_for(s, offer) is None
                assert offer.pipedrive_deal_id is None
        finally:
            await eng.dispose()

    asyncio.run(_run())
