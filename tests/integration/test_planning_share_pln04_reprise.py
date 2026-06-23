"""PLN-04 — fiche destinataire + langue + sélection leg-à-leg des partages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel


def test_parse_legs_ids():
    from app.services.planning import parse_legs_ids

    assert parse_legs_ids("12, 15 ;18") == "12,15,18"
    assert parse_legs_ids("3 3 2") == "2,3"  # dédup + tri
    assert parse_legs_ids("") is None
    assert parse_legs_ids("abc, -4, 0") is None  # aucun ID valide
    assert parse_legs_ids(None) is None


async def _setup(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    for i in (1, 2):
        db.add(
            Leg(
                id=i,
                leg_code=f"{i}CFRBR6",
                vessel_id=1,
                departure_port_id=1,
                arrival_port_id=2,
                etd_ref=base + timedelta(days=i),
                eta_ref=base + timedelta(days=i + 15),
                etd=base + timedelta(days=i),
                eta=base + timedelta(days=i + 15),
            )
        )
    await db.flush()


@pytest.mark.asyncio
async def test_create_share_persists_recipient_lang_legs(db):
    from app.services.planning import create_share

    await _setup(db)
    share = await create_share(
        db,
        label="Acme",
        vessel_id=None,
        only_bookable=False,
        description=None,
        expires_at=None,
        created_by_id=None,
        recipient_name="Jane Doe",
        recipient_company="Acme Ltd",
        recipient_email="jane@acme.test",
        lang="en",
        legs_ids="1",
    )
    assert share.recipient_name == "Jane Doe"
    assert share.recipient_company == "Acme Ltd"
    assert share.lang == "en"
    assert share.legs_ids == "1"


@pytest.mark.asyncio
async def test_create_share_clamps_unknown_lang(db):
    from app.services.planning import create_share

    share = await create_share(
        db,
        label=None,
        vessel_id=None,
        only_bookable=False,
        description=None,
        expires_at=None,
        created_by_id=None,
        lang="vi",  # hors FR/EN → fr
    )
    assert share.lang == "fr"


class _PubReq:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/planning/share/x", query="")
    scope: dict = {"type": "http"}

    def __init__(self):
        self.state = SimpleNamespace(
            notif_count=0, recent_notifications=[], newtowt_agent_enabled=True
        )


@pytest.mark.asyncio
async def test_public_share_uses_share_lang_and_legs(db):
    from app.routers.planning_router import public_share
    from app.services.planning import create_share

    await _setup(db)
    share = await create_share(
        db,
        label="EN share",
        vessel_id=None,
        only_bookable=False,
        description=None,
        expires_at=None,
        created_by_id=None,
        lang="en",
        legs_ids="1",  # uniquement le leg 1
    )

    # Le visiteur a un cookie de langue FR : il NE doit PAS écraser la langue
    # du partage (c'est le bug « partage EN cassé » que PLN-04 corrige).
    req = _PubReq()
    req.cookies = {"towt_lang": "fr"}
    resp = await public_share(req, share.token, db=db)
    assert resp.status_code == 200
    # La langue forcée du partage prime sur le cookie du visiteur.
    assert resp.context["lang"] == "en"
    # Sélection leg-à-leg : seul le leg 1 est exposé.
    rows = resp.context["table_rows"]
    assert len(rows) == 1 and rows[0]["leg_code"] == "1CFRBR6"


@pytest.mark.asyncio
async def test_shares_index_renders_with_history(db, staff_user):
    from app.routers.planning_router import shares_index
    from app.services.planning import create_share

    await _setup(db)
    await create_share(
        db,
        label="Acme",
        vessel_id=None,
        only_bookable=False,
        description=None,
        expires_at=None,
        created_by_id=staff_user.id,
        recipient_name="Jane",
        lang="en",
    )
    resp = await shares_index(_PubReq(), db=db, user=staff_user)
    assert resp.status_code == 200
    assert staff_user.id in resp.context["creators"]
    assert len(resp.context["shares"]) == 1
