"""G14 — périmètre MRV UE piloté par Port.mrv_scope (admin), pas une liste
de préfixes pays codée en dur (architecture §7.3)."""

from __future__ import annotations

import pytest


def test_port_model_has_mrv_scope():
    from app.models.port import Port

    assert "mrv_scope" in Port.__table__.columns


@pytest.mark.asyncio
async def test_new_port_defaults_mrv_scope_false(db):
    from app.models.port import Port

    port = Port(locode="BRSSZ", name="Santos", country="BR")
    db.add(port)
    await db.flush()
    assert port.mrv_scope is False


@pytest.mark.asyncio
async def test_admin_toggle_mrv_scope_flips(db, staff_user):
    from app.models.port import Port
    from app.routers.modules_router import admin_port_toggle_mrv_scope

    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    await db.flush()

    resp = await admin_port_toggle_mrv_scope(1, db=db, user=staff_user)
    assert resp.status_code == 303
    assert (await db.get(Port, 1)).mrv_scope is True

    await admin_port_toggle_mrv_scope(1, db=db, user=staff_user)
    assert (await db.get(Port, 1)).mrv_scope is False


def test_admin_ports_template_has_mrv_scope_toggle():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/admin/ports.html")[0]
    assert "/toggle-mrv-scope" in src
