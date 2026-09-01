"""Cockpit escale — reprise UX Phase 1 (docs/design/03-reprise-ux-legacy.md).

Couvre : marqueurs du template refondu (split Import/Export, sous-nav,
liens croisés), contexte cockpit (KPI, synthèses Documents & SOF et
Tickets, retards) et réponses de mutation HTMX (204 + escaleRefresh).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.escale import DockerShift, EscaleOperation
from app.models.leg import Leg
from app.models.port import Port
from app.models.ticket import Ticket
from app.models.vessel import Vessel


class _Req:
    headers: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")


class _HxReq:
    headers = {"hx-request": "true"}
    client = SimpleNamespace(host="127.0.0.1")


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


# ───────────────────────── Template refondu ─────────────────────────


def test_escale_template_cockpit_markers():
    """Le cockpit conserve/introduit ses marqueurs structurants."""
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/escale/index.html")[0]
    # Zone rafraîchie sans rechargement (HTMX hx-select sur la page).
    assert 'id="escale-sections"' in src
    assert "escaleRefresh" in src
    assert 'hx-select="#escale-sections"' in src
    # Sous-navigation collante + split directionnel (pattern legacy repris).
    assert "escale-subnav" in src
    assert "dir-col-import" in src
    assert "dir-col-export" in src
    # Indicateurs cockpit.
    assert "PAF · réglementaire" in src
    assert "badge-overdue" in src
    # Liens croisés (fin des impasses inter-modules).
    assert "/tickets?leg_id=" in src
    assert "/captain?leg_id=" in src
    assert "/crew/members/" in src


# ───────────────────────── Contexte cockpit ─────────────────────────


@pytest.mark.asyncio
async def test_escale_index_cockpit_context(db, staff_user):
    """KPI d'escale, synthèse Documents & SOF, tickets et retards exposés."""
    from app.routers.escale_router import escale_index

    leg = await _setup_leg(db)
    past = datetime.now(UTC) - timedelta(hours=6)
    # Une opération en retard (début prévu échu, jamais démarrée)…
    db.add(
        EscaleOperation(
            leg_id=leg.id,
            operation_type="technique",
            action="soutage",
            direction="BOTH",
            planned_start=past,
        )
    )
    # … et une en cours.
    db.add(
        EscaleOperation(
            leg_id=leg.id,
            operation_type="commercial",
            action="chargement",
            direction="EXPORT",
            planned_start=past,
            actual_start=past + timedelta(hours=1),
            status="in_progress",
        )
    )
    db.add(
        DockerShift(
            leg_id=leg.id,
            direction="EXPORT",
            company="SATA",
            nb_dockers=8,
            palettes_target=80,
            palettes_done=64,
        )
    )
    db.add(
        Ticket(
            reference="TCK-0001",
            leg_id=leg.id,
            category="avarie",
            priority="P1",
            title="Grue en panne",
            description="Terminal 7",
            status="open",
        )
    )
    # Un ticket hors leg — ne doit pas compter.
    db.add(
        Ticket(
            reference="TCK-0002",
            category="douane",
            priority="P3",
            title="Hors escale",
            description="—",
            status="open",
        )
    )
    await db.flush()

    resp = await escale_index(_FullReq(), leg_id=leg.id, db=db, user=staff_user)
    assert resp.status_code == 200
    ctx = resp.context
    assert ctx["escale_kpis"]["ops_total"] == 2
    assert ctx["escale_kpis"]["ops_in_progress"] == 1
    assert ctx["escale_kpis"]["ops_late"] >= 1
    assert ctx["escale_kpis"]["palettes_done"] == 64
    assert ctx["escale_kpis"]["palettes_target"] == 80
    assert ctx["late_op_ids"]
    assert ctx["docs_sof"] is not None
    assert ctx["docs_sof"]["sof_total"] >= 0
    assert ctx["tickets_summary"]["open"] == 1
    assert ctx["tickets_summary"]["open_p1"] == 1
    assert ctx["tickets_summary"]["total"] == 1  # le ticket hors leg est exclu


@pytest.mark.asyncio
async def test_cockpit_late_op_ids_rules(db):
    """Retard = fenêtre planifiée échue sans réel ; jamais sur une op terminée."""
    from app.routers.escale_router import _cockpit_late_op_ids

    now = datetime.now(UTC)
    late = EscaleOperation(id=1, leg_id=1, operation_type="technique", action="soutage")
    late.planned_start = now - timedelta(hours=2)
    done = EscaleOperation(id=2, leg_id=1, operation_type="technique", action="soutage")
    done.planned_end = now - timedelta(hours=2)
    done.actual_end = now - timedelta(hours=1)
    done.status = "completed"
    future = EscaleOperation(id=3, leg_id=1, operation_type="technique", action="soutage")
    future.planned_start = now + timedelta(hours=2)

    ids = _cockpit_late_op_ids([late, done, future], now)
    assert ids == {1}


# ───────────────────── Mutations HTMX (204 + refresh) ─────────────────────


@pytest.mark.asyncio
async def test_start_operation_htmx_returns_204_with_refresh(db, staff_user):
    """Sous HTMX : 204 + HX-Trigger toast/escaleRefresh (pas de rechargement)."""
    from app.routers.escale_router import start_operation

    leg = await _setup_leg(db)
    op = EscaleOperation(leg_id=leg.id, operation_type="technique", action="soutage")
    db.add(op)
    await db.flush()

    resp = await start_operation(op.id, _HxReq(), db=db, user=staff_user)
    assert resp.status_code == 204
    trigger = resp.headers["HX-Trigger"]
    assert "escaleRefresh" in trigger
    assert "toast" in trigger
    assert op.actual_start is not None


@pytest.mark.asyncio
async def test_start_operation_without_htmx_redirects(db, staff_user):
    """Sans JS : le repli 303 classique reste intact."""
    from app.routers.escale_router import start_operation

    leg = await _setup_leg(db)
    op = EscaleOperation(leg_id=leg.id, operation_type="technique", action="soutage")
    db.add(op)
    await db.flush()

    resp = await start_operation(op.id, _Req(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/escale?leg_id={leg.id}"
