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
        {"id": 101, "name": "Acme Forwarding", "address": "12 Dock Rd, Le Havre"},
        {"id": 102, "name": "Café Brasil Imports", "address": None},
        {"id": None, "name": "ignored (no id)"},
        {"id": 103, "name": ""},  # ignoré (pas de nom)
    ]
    monkeypatch.setattr(pipedrive, "enabled", lambda: True)

    async def _fake_list(*, max_items=1000):
        return orgs

    monkeypatch.setattr(pipedrive, "list_organizations", _fake_list)

    async def _run():
        eng = create_async_engine("sqlite+aiosqlite://")
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                # Un client déjà lié à l'org 101 (contact saisi à la main).
                s.add(Client(
                    name="Acme (ancien nom)", client_type="shipper",
                    contact_email="ops@acme.test", pipedrive_org_id=101,
                ))
                await s.flush()

                r1 = await pipedrive_sync.sync_clients(s)
                assert r1["configured"] is True
                assert r1["created"] == 1   # org 102
                assert r1["updated"] == 1   # org 101
                assert r1["total"] == 4

                clients = {c.pipedrive_org_id: c for c in (await s.execute(select(Client))).scalars().all()}
                assert set(clients) == {101, 102}
                # 101 : nom mis à jour, contact manuel préservé, type inchangé
                assert clients[101].name == "Acme Forwarding"
                assert clients[101].contact_email == "ops@acme.test"
                assert clients[101].client_type == "shipper"
                # 102 : créé avec type par défaut
                assert clients[102].client_type == "freight_forwarder"

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
                assert r == {"configured": False, "created": 0, "updated": 0, "total": 0, "errors": 0}
        finally:
            await eng.dispose()

    asyncio.run(_run())
