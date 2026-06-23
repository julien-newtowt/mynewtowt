"""ONB-02 — documents cargo guidés (formulaires structurés par type)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.crew import CrewAssignment, CrewMember
from app.models.leg import Leg
from app.models.port import Port
from app.models.sof_event import CargoDocument
from app.models.vessel import Vessel


class _FormReq:
    def __init__(self, form=None, query=None):
        self._form = dict(form or {})
        self.query_params = dict(query or {})
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="127.0.0.1")

    async def form(self):
        return self._form


async def _setup_leg(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    leg = Leg(
        id=1,
        leg_code="1CFRBR6",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=base,
        eta_ref=base + timedelta(days=20),
        etd=base,
        eta=base + timedelta(days=20),
    )
    db.add(leg)
    await db.flush()
    return leg


# ───────────────────────────── Service (schéma) ─────────────────────────────


def test_all_13_types_present():
    from app.services.cargo_documents import CARGO_DOC_TYPES

    expected = {
        "NOR",
        "NOR_RT",
        "HOLDS_CERT",
        "KEY_MEETING",
        "PRE_MEETING",
        "MATES_RECEIPT",
        "LOP_FP",
        "LOP_DELAYS",
        "LOP_DOCUMENT",
        "LOP_QTY",
        "LOP_DEADFREIGHT",
        "LOP_OTHER",
    }
    assert expected <= set(CARGO_DOC_TYPES)


def test_legal_boilerplate_prefilled():
    from app.services.cargo_documents import LOP_RESERVE, MATES_CONDITION, field_defaults

    lop = field_defaults("LOP_QTY")
    assert lop["reserve"] == LOP_RESERVE
    assert "TO WHOM IT MAY CONCERN" in lop["to"]
    mates = field_defaults("MATES_RECEIPT")
    assert mates["condition"] == MATES_CONDITION


def test_coerce_doc_form_is_allowlisted():
    from app.services.cargo_documents import coerce_doc_form

    data = coerce_doc_form(
        "NOR",
        {"to_charterer": "ACME", "cargo_desc": "Vin", "evil": "DROP", "kind": "NOR"},
    )
    assert data["to_charterer"] == "ACME"
    assert "evil" not in data  # anti mass-assignment
    assert "kind" not in data


def test_doc_rows_and_recipient():
    from app.services.cargo_documents import doc_rows, recipient_of

    data = {"to_charterer": "ACME", "cargo_desc": "Vin"}
    rows = dict(doc_rows("NOR", data))
    assert rows["Destinataire (affréteur)"] == "ACME"
    assert recipient_of("NOR", data) == "ACME"


# ───────────────────────────── Routes ─────────────────────────────


@pytest.mark.asyncio
async def test_create_guided_doc_stores_data_json(db, staff_user):
    from app.routers.captain_router import create_cargo_document

    await _setup_leg(db)
    resp = await create_cargo_document(
        1,
        _FormReq(
            {
                "kind": "NOR",
                "reference": "NOR-001",
                "to_charterer": "ACME Charter",
                "port": "Fécamp",
                "cargo_desc": "Vin en vrac",
                "master_name": "Cmdt Test",
            }
        ),
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    doc = (await db.execute(CargoDocument.__table__.select())).fetchone()
    assert doc.kind == "NOR"
    assert doc.party_name == "ACME Charter"  # recipient → party_name
    data = json.loads(doc.data_json)
    assert data["cargo_desc"] == "Vin en vrac"
    assert data["master_name"] == "Cmdt Test"


@pytest.mark.asyncio
async def test_update_guided_doc(db, staff_user):
    from app.routers.captain_router import create_cargo_document, update_cargo_document

    await _setup_leg(db)
    await create_cargo_document(
        1,
        _FormReq({"kind": "LOP_QTY", "to": "Terminal", "subject": "Manquant"}),
        db=db,
        user=staff_user,
    )
    doc = (await db.execute(CargoDocument.__table__.select())).fetchone()
    resp = await update_cargo_document(
        1,
        doc.id,
        _FormReq({"to": "Terminal", "subject": "Quantité contestée", "details": "10 t manquantes"}),
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    refreshed = await db.get(CargoDocument, doc.id)
    data = json.loads(refreshed.data_json)
    assert data["subject"] == "Quantité contestée"
    assert data["details"] == "10 t manquantes"


@pytest.mark.asyncio
async def test_embarked_crew_names(db):
    from app.routers.captain_router import _embarked_crew_names

    await _setup_leg(db)
    m = CrewMember(full_name="Jean Marin", role="capitaine")
    db.add(m)
    await db.flush()
    db.add(
        CrewAssignment(
            crew_member_id=m.id, leg_id=1, vessel_id=1, embark_at=datetime(2026, 4, 1, tzinfo=UTC)
        )
    )
    await db.flush()
    names = await _embarked_crew_names(db, 1)
    assert "Jean Marin" in names
    # navire sans équipage embarqué → vide
    assert await _embarked_crew_names(db, 999) == []


@pytest.mark.asyncio
async def test_cargo_doc_pdf_renders(db, staff_user):
    pytest.importorskip("weasyprint")
    from app.routers.captain_router import captain_cargo_doc_pdf, create_cargo_document

    await _setup_leg(db)
    await create_cargo_document(
        1,
        _FormReq({"kind": "MATES_RECEIPT", "shipper": "Cave X", "cargo_desc": "Vin"}),
        db=db,
        user=staff_user,
    )
    doc = (await db.execute(CargoDocument.__table__.select())).fetchone()
    resp = await captain_cargo_doc_pdf(1, doc.id, db=db, user=staff_user)
    assert resp.media_type == "application/pdf"
    assert len(resp.body) > 500


@pytest.mark.asyncio
async def test_form_context_includes_crew_and_defaults(db, staff_user):
    from app.routers.captain_router import _cargo_doc_form_ctx

    leg = await _setup_leg(db)
    m = CrewMember(full_name="Jean Marin", role="capitaine")
    db.add(m)
    await db.flush()
    db.add(
        CrewAssignment(
            crew_member_id=m.id, leg_id=1, vessel_id=1, embark_at=datetime(2026, 4, 1, tzinfo=UTC)
        )
    )
    await db.flush()
    ctx = await _cargo_doc_form_ctx(db, _FormReq(query={"kind": "LOP_FP"}), staff_user, leg)
    assert ctx["kind"] == "LOP_FP"
    assert "Jean Marin" in ctx["crew_names"]
    assert ctx["values"]["reserve"]  # mention légale pré-remplie
