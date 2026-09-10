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
                # Les deux entrées inexploitables (sans id, sans nom) sont
                # comptées à part : ni créées, ni ignorées faute de deal.
                assert r1["invalid"] == 2
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


def test_sync_clients_fills_the_contact_block_from_the_crm(monkeypatch) -> None:
    """COM-12 — la fiche client remonte de Pipedrive : contact, téléphone, pays.

    Deux garanties, opposées et complémentaires :

    * ce que le CRM **sait**, il le pose (le bloc « Contact » de la fiche
      restait vide alors que Pipedrive porte l'information) ;
    * ce que le CRM **tait**, il ne l'efface pas — un champ absent de l'API
      n'est pas une valeur vide, et écraser une saisie manuelle par ce silence
      serait une perte de donnée silencieuse.
    """
    monkeypatch.setattr(pipedrive, "enabled", lambda: True)

    async def _fake_orgs(*, max_items=1000):
        return [
            {
                "id": 201,
                "name": "Café Brasil Imports",
                "address": "Rua do Porto 8, Santos",
                "address_country": "Brazil",
                "won_deals_count": 1,
            },
            {"id": 202, "name": "Cacao Direct", "address_country": "CI", "open_deals_count": 1},
        ]

    async def _fake_deals(*, max_items=10000):
        return []

    async def _fake_persons(*, max_items=20000):
        return [
            # Personne sans moyen de contact : ne doit pas masquer la suivante.
            {"name": "Standard", "org_id": 201, "email": [], "phone": []},
            {
                "name": "Ana Souza",
                "org_id": {"value": 201},
                "email": [
                    {"value": "second@brasil.test", "primary": False},
                    {"value": "ana@brasil.test", "primary": True},
                ],
                "phone": [{"value": "+55 13 99999-0000", "primary": True}],
            },
            {"name": "Sans org", "org_id": None, "email": [{"value": "x@y.test"}]},
        ]

    monkeypatch.setattr(pipedrive, "list_organizations", _fake_orgs)
    monkeypatch.setattr(pipedrive, "list_deals", _fake_deals)
    monkeypatch.setattr(pipedrive, "list_persons", _fake_persons)

    async def _run():
        eng = create_async_engine("sqlite+aiosqlite://")
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                # Client existant avec un téléphone saisi à la main et aucun
                # correspondant côté CRM (l'org 202 n'a pas de personne).
                s.add(
                    Client(
                        name="Cacao Direct",
                        client_type="shipper",
                        contact_phone="+225 07 00 00 00",
                        pipedrive_org_id=202,
                    )
                )
                await s.flush()

                await pipedrive_sync.sync_clients(s)
                clients = {
                    c.pipedrive_org_id: c for c in (await s.execute(select(Client))).scalars().all()
                }

                created = clients[201]
                assert created.contact_name == "Ana Souza"
                assert created.contact_email == "ana@brasil.test"  # entrée « primary »
                assert created.contact_phone == "+55 13 99999-0000"
                assert created.country == "BR"  # « Brazil » → ISO 2
                assert created.pipedrive_synced_at is not None

                kept = clients[202]
                assert kept.contact_phone == "+225 07 00 00 00", "le CRM muet n'efface rien"
                assert kept.country == "CI"  # déjà un code ISO 2 côté CRM
        finally:
            await eng.dispose()

    asyncio.run(_run())


def test_country_code_ignores_an_unresolvable_label() -> None:
    """Un pays non reconnu ne pose rien — un mauvais code afficherait un faux drapeau."""
    assert pipedrive_sync._country_code({"address_country": "Ruritania"}) is None
    assert pipedrive_sync._country_code({}) is None
    assert pipedrive_sync._country_code({"address_country": "france"}) == "FR"


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
                    "recovered": 0,
                    "skipped": 0,
                    "invalid": 0,
                    "with_deal": 0,
                    "total": 0,
                    "truncated": False,
                    "lookup_capped": False,
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


# ────────── Exhaustivité : le deal fait le client, pas le listing ──────────
#
# Défaut constaté le 2026-09-10. Le CRM portait 51 organisations avec deal ;
# la base en comptait 16. La synchronisation annonçait
# « updated=16, skipped=984 » — soit `total = 1000`, exactement le plafond de
# `list_organizations`. Le listing s'arrêtait là **sans le dire**, et les
# organisations au-delà n'étaient jamais examinées : 35 clients manquaient.
#
# L'identifiant d'organisation de chaque deal était pourtant déjà en mémoire
# (`org_ids_with_deal`) — il n'était consulté qu'à l'intérieur de la boucle sur
# le listing. La correction s'en sert comme point de départ d'une seconde passe.


async def _in_session(fn):
    """Exécute ``fn(session)`` sur une base SQLite jetable."""
    eng = create_async_engine("sqlite+aiosqlite://")
    try:
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as s:
            return await fn(s)
    finally:
        await eng.dispose()


def _patch_crm(monkeypatch, *, orgs, deals, by_id=None, persons=()):
    """Branche un CRM Pipedrive factice (listing, deals, lecture par id)."""
    monkeypatch.setattr(pipedrive, "enabled", lambda: True)
    calls: list[int] = []

    async def _fake_orgs(*, max_items=None):
        return list(orgs)

    async def _fake_deals(*, max_items=10000):
        return list(deals)

    async def _fake_persons(*, max_items=20000):
        return list(persons)

    async def _fake_get(org_id):
        calls.append(org_id)
        return (by_id or {}).get(org_id)

    monkeypatch.setattr(pipedrive, "list_organizations", _fake_orgs)
    monkeypatch.setattr(pipedrive, "list_deals", _fake_deals)
    monkeypatch.setattr(pipedrive, "list_persons", _fake_persons)
    monkeypatch.setattr(pipedrive, "get_organization", _fake_get)
    return calls


def test_an_organisation_with_a_deal_outside_the_listing_is_still_imported(monkeypatch) -> None:
    """Le défaut signalé, en un test : le listing ne remonte pas tout le CRM.

    L'organisation 999 porte un deal mais n'apparaît pas dans le listing (elle
    était au-delà du millième rang). Elle doit devenir cliente quand même.
    """
    lookups = _patch_crm(
        monkeypatch,
        orgs=[{"id": 101, "name": "Café Brasil Imports"}],
        deals=[{"org_id": {"value": 101}}, {"org_id": {"value": 999}}],
        by_id={999: {"id": 999, "name": "Malongo", "address_country": "France"}},
    )

    async def _body(s):
        return await pipedrive_sync.sync_clients(s), {
            c.pipedrive_org_id: c for c in (await s.execute(select(Client))).scalars().all()
        }

    result, clients = asyncio.run(_in_session(_body))

    assert set(clients) == {101, 999}, "l'organisation hors listing manquait"
    assert clients[999].name == "Malongo"
    assert clients[999].country == "FR"
    assert result["created"] == 2
    assert result["recovered"] == 1, "elle doit être comptée comme rattrapée"
    assert result["with_deal"] == 2, "le dénominateur attendu est le nombre de deals"
    assert lookups == [999], "seule l'organisation absente du listing est relue"


def test_a_listed_organisation_without_deal_is_never_refetched(monkeypatch) -> None:
    """La passe de rattrapage ne défait pas le filtre métier.

    Une organisation examinée puis écartée faute de deal reste écartée : elle
    ne doit pas revenir par la porte de derrière, sinon la liste clients se
    remplirait de prospects.
    """
    lookups = _patch_crm(
        monkeypatch,
        orgs=[
            {"id": 101, "name": "Client avec deal", "open_deals_count": 1},
            {"id": 104, "name": "Prospect sans deal"},
        ],
        deals=[{"org_id": 101}],
        by_id={104: {"id": 104, "name": "Prospect sans deal"}},
    )

    async def _body(s):
        return await pipedrive_sync.sync_clients(s), {
            c.pipedrive_org_id for c in (await s.execute(select(Client))).scalars().all()
        }

    result, ids = asyncio.run(_in_session(_body))

    assert ids == {101}
    assert result["skipped"] == 1
    assert result["recovered"] == 0
    assert lookups == [], "une organisation déjà examinée ne se relit pas"


def test_a_deal_on_an_unreadable_organisation_is_counted_not_hidden(monkeypatch) -> None:
    """Organisation supprimée, fusionnée, ou erreur réseau : c'est un incident.

    Le compter permet à l'écran de le dire. L'avaler reproduirait exactement le
    défaut qu'on corrige — une synchronisation qui se déclare complète en
    laissant des clients de côté.
    """
    _patch_crm(
        monkeypatch,
        orgs=[{"id": 101, "name": "Client listé", "open_deals_count": 1}],
        deals=[{"org_id": 101}, {"org_id": 999}],
        by_id={},  # 999 illisible
    )

    async def _body(s):
        return await pipedrive_sync.sync_clients(s), len(
            (await s.execute(select(Client))).scalars().all()
        )

    result, count = asyncio.run(_in_session(_body))

    assert count == 1, "le reste de la synchronisation aboutit malgré l'incident"
    assert result["errors"] == 1
    assert result["recovered"] == 0


def test_an_organisation_reached_by_both_passes_is_created_once(monkeypatch) -> None:
    """Le flush n'a pas encore eu lieu quand la seconde passe démarre.

    Sans indexation immédiate du client créé, une organisation atteinte deux
    fois produirait un doublon — et un doublon de client, c'est une grille
    tarifaire dupliquée.
    """
    org = {"id": 555, "name": "Sonar", "open_deals_count": 1}
    # Le listing la remonte **et** elle porte un deal : les deux passes la
    # voient. `get_organization` la rendrait à nouveau si elle était appelée.
    _patch_crm(monkeypatch, orgs=[org], deals=[{"org_id": 555}], by_id={555: org})

    async def _body(s):
        result = await pipedrive_sync.sync_clients(s)
        rows = (await s.execute(select(Client))).scalars().all()
        return result, rows

    result, rows = asyncio.run(_in_session(_body))

    assert len(rows) == 1
    assert result["created"] == 1


def test_hitting_the_listing_bound_is_reported(monkeypatch) -> None:
    """Une borne atteinte se dit. C'est le silence qui a coûté 35 clients."""
    monkeypatch.setattr(pipedrive, "ORG_LIST_MAX_ITEMS", 2)
    _patch_crm(
        monkeypatch,
        orgs=[
            {"id": 1, "name": "A", "open_deals_count": 1},
            {"id": 2, "name": "B", "open_deals_count": 1},
        ],
        deals=[],
    )

    result = asyncio.run(_in_session(pipedrive_sync.sync_clients))
    assert result["truncated"] is True

    # Et l'inverse : un listing qui tient dans la borne ne crie pas au loup.
    _patch_crm(monkeypatch, orgs=[{"id": 1, "name": "A", "open_deals_count": 1}], deals=[])
    assert asyncio.run(_in_session(pipedrive_sync.sync_clients))["truncated"] is False


def test_get_organization_reads_the_api_envelope(monkeypatch) -> None:
    """``{"success": true, "data": {...}}`` → l'organisation ; sinon ``None``."""

    async def _ok(method, path, **kw):
        assert path == "/organizations/42"
        return {"success": True, "data": {"id": 42, "name": "Volcafe"}}

    monkeypatch.setattr(pipedrive, "_request", _ok)
    assert asyncio.run(pipedrive.get_organization(42))["name"] == "Volcafe"

    async def _fail(method, path, **kw):
        return {"success": False}

    monkeypatch.setattr(pipedrive, "_request", _fail)
    assert asyncio.run(pipedrive.get_organization(42)) is None
    # Identifiant absent : aucun appel réseau.
    assert asyncio.run(pipedrive.get_organization(0)) is None


def test_an_organisation_with_a_deal_but_no_name_is_counted_not_dropped(monkeypatch) -> None:
    """Un deal dont l'organisation n'a pas de nom exploitable reste visible.

    Elle ne peut pas devenir cliente (le nom est la clé métier de la fiche),
    mais la disparition muette est précisément le défaut corrigé ici.
    """
    _patch_crm(
        monkeypatch,
        orgs=[],
        deals=[{"org_id": 777}],
        by_id={777: {"id": 777, "name": "   "}},
    )

    async def _body(s):
        return await pipedrive_sync.sync_clients(s), len(
            (await s.execute(select(Client))).scalars().all()
        )

    result, count = asyncio.run(_in_session(_body))

    assert count == 0
    assert result["invalid"] == 1
    assert result["recovered"] == 0
    assert result["errors"] == 0, "ce n'est pas une panne, c'est une donnée inexploitable"
