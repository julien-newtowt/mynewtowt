"""Journal d'escale — timeline unifiée (reprise UX Phase 2).

Couvre : construction de la timeline (``build_journal``), rapprochement
des deux registres SOF (``sof_reconciliation``), la route
``GET /escale/legs/{leg_id}/journal`` et les liens croisés cockpit ↔
journal dans les templates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.claim import Claim
from app.models.escale import ESCALE_ACTION_TO_SOF, EscaleOperation
from app.models.leg import Leg
from app.models.port import Port
from app.models.sof_event import CargoDocument, SofEvent
from app.models.ticket import Ticket
from app.models.vessel import Vessel
from app.services.escale_journal import build_journal, sof_reconciliation


class _FullReq:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/escale")
    state = SimpleNamespace(notif_count=0, newtowt_agent_enabled=True)


async def _setup_leg(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    from datetime import timedelta

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


# ───────────────────────── build_journal ─────────────────────────


@pytest.mark.asyncio
async def test_build_journal_orders_and_kinds(db):
    """Timeline triée, un kind attendu par source, badges/liens corrects."""
    leg = await _setup_leg(db)

    t0 = datetime(2026, 4, 5, 8, tzinfo=UTC)
    t1 = datetime(2026, 4, 6, 9, tzinfo=UTC)
    t2 = datetime(2026, 4, 7, 10, tzinfo=UTC)
    t3 = datetime(2026, 4, 7, 18, tzinfo=UTC)
    t4 = datetime(2026, 4, 8, 11, tzinfo=UTC)

    leg.ata = t0

    db.add(
        SofEvent(
            leg_id=leg.id,
            event_type="PILOT_ON",
            occurred_at=t1,
            signed_at=t1,
            signed_by_name="Cdt Martin",
        )
    )
    db.add(
        EscaleOperation(
            leg_id=leg.id,
            operation_type="commercial",
            action="chargement",
            direction="EXPORT",
            actual_start=t2,
            actual_end=t3,
        )
    )
    db.add(
        CargoDocument(
            leg_id=leg.id,
            kind="NOR",
            issued_at=t4,
        )
    )
    db.add(
        Ticket(
            reference="TCK-J001",
            leg_id=leg.id,
            category="avarie",
            priority="P1",
            title="Grue en panne",
            description="Terminal 7",
            status="open",
        )
    )
    claim = Claim(
        reference="CLM-J001",
        claim_type="cargo",
        leg_id=leg.id,
        title="Colis endommagé",
        description="Carton écrasé",
        occurred_at=t4,
    )
    db.add(claim)
    await db.flush()

    entries = await build_journal(db, leg)

    sort_ats = [e.sort_at for e in entries]
    assert sort_ats == sorted(sort_ats)

    kinds_present = {e.kind for e in entries}
    for expected_kind in ("statut", "sof", "operation", "document", "ticket", "sinistre"):
        assert expected_kind in kinds_present, f"kind manquant : {expected_kind}"

    sof_entry = next(e for e in entries if e.kind == "sof")
    assert sof_entry.badge == "signé"

    claim_entry = next(e for e in entries if e.kind == "sinistre")
    assert claim_entry.link == f"/claims/{claim.id}"


@pytest.mark.asyncio
async def test_build_journal_skips_missing_timestamps(db):
    """Pas d'horodatage réel/statut posé → aucune entrée inventée."""
    leg = await _setup_leg(db)

    # Opération jamais démarrée/terminée : ni actual_start ni actual_end.
    db.add(
        EscaleOperation(
            leg_id=leg.id,
            operation_type="technique",
            action="soutage",
        )
    )
    await db.flush()

    entries = await build_journal(db, leg)
    kinds_present = {e.kind for e in entries}

    assert "operation" not in kinds_present
    # leg.ata / leg.atd jamais posés dans _setup_leg.
    assert "statut" not in kinds_present


# ───────────────────────── sof_reconciliation ─────────────────────────


def test_sof_reconciliation():
    """Rapprochement bord/terre limité aux 7 actions synchronisables."""
    mapped_action, mapped_type = next(iter(ESCALE_ACTION_TO_SOF.items()))

    unsynced_op = EscaleOperation(leg_id=1, operation_type="technique", action="soutage")
    synced_op = EscaleOperation(leg_id=1, operation_type="technique", action=mapped_action)

    # Rien côté bord : le type attendu manque au bord.
    result = sof_reconciliation([unsynced_op, synced_op], [])
    assert result["missing_on_board"] == [mapped_type]
    assert result["missing_on_shore"] == []
    assert result["ops_total"] == 2
    assert result["ops_synced"] == 1
    assert result["sof_total"] == 0
    assert result["sof_signed"] == 0

    # Inverse : un événement bord d'un type mappé sans opération côté terre.
    sof_event = SofEvent(
        leg_id=1, event_type=mapped_type, occurred_at=datetime(2026, 4, 1, tzinfo=UTC)
    )
    result2 = sof_reconciliation([], [sof_event])
    assert result2["missing_on_shore"] == [mapped_type]
    assert result2["missing_on_board"] == []

    # Un type NON mappé présent au bord n'apparaît dans aucun des deux registres.
    unmapped_event = SofEvent(
        leg_id=1, event_type="OTHER", occurred_at=datetime(2026, 4, 1, tzinfo=UTC)
    )
    result3 = sof_reconciliation([], [sof_event, unmapped_event])
    assert mapped_type in result3["missing_on_shore"]
    assert "OTHER" not in result3["missing_on_shore"]
    assert "OTHER" not in result3["missing_on_board"]


# ───────────────────────── Route ─────────────────────────


@pytest.mark.asyncio
async def test_journal_route_renders(db, staff_user):
    """200, bon template, contexte days/reconciliation, filtre par kind."""
    from app.routers.escale_router import escale_journal_page

    leg = await _setup_leg(db)
    db.add(
        SofEvent(
            leg_id=leg.id,
            event_type="PILOT_ON",
            occurred_at=datetime(2026, 4, 6, 9, tzinfo=UTC),
            signed_at=datetime(2026, 4, 6, 9, tzinfo=UTC),
            signed_by_name="Cdt Martin",
        )
    )
    db.add(
        CargoDocument(
            leg_id=leg.id,
            kind="NOR",
            issued_at=datetime(2026, 4, 6, 12, tzinfo=UTC),
        )
    )
    await db.flush()

    resp = await escale_journal_page(
        leg_id=leg.id, request=_FullReq(), kind=None, db=db, user=staff_user
    )
    assert resp.status_code == 200
    assert resp.template.name == "staff/escale/journal.html"
    ctx = resp.context
    assert ctx["days"]
    assert ctx["reconciliation"] is not None
    assert ctx["entry_total"] >= 2
    all_kinds = {e.kind for _day, day_entries in ctx["days"] for e in day_entries}
    assert "sof" in all_kinds
    assert "document" in all_kinds

    resp_sof = await escale_journal_page(
        leg_id=leg.id, request=_FullReq(), kind="sof", db=db, user=staff_user
    )
    ctx_sof = resp_sof.context
    assert ctx_sof["days"]
    for _day, day_entries in ctx_sof["days"]:
        for e in day_entries:
            assert e.kind == "sof"


@pytest.mark.asyncio
async def test_journal_route_404_unknown_leg(db, staff_user):
    """Leg inexistant → 404 explicite."""
    from app.routers.escale_router import escale_journal_page

    with pytest.raises(HTTPException) as exc_info:
        await escale_journal_page(leg_id=999, request=_FullReq(), kind=None, db=db, user=staff_user)
    assert exc_info.value.status_code == 404


# ───────────────────────── Liens croisés templates ─────────────────────────


def test_cockpit_card_links_journal():
    """Le cockpit pointe vers le journal, et le journal affiche le rapprochement."""
    from app.templating import templates

    cockpit_src = templates.env.loader.get_source(templates.env, "staff/escale/index.html")[0]
    assert "/journal" in cockpit_src
    assert "Journal d'escale" in cockpit_src

    journal_src = templates.env.loader.get_source(templates.env, "staff/escale/journal.html")[0]
    assert "Rapprochement des SOF" in journal_src
