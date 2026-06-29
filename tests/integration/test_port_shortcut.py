"""PLN-07 — raccourcis ports pilotés par Port.is_shortcut (admin) + repli."""

from __future__ import annotations

import pytest


def test_port_model_has_is_shortcut():
    from app.models.port import Port

    assert "is_shortcut" in Port.__table__.columns


@pytest.mark.asyncio
async def test_admin_toggle_shortcut_flips(db, staff_user):
    from app.models.port import Port
    from app.routers.modules_router import admin_port_toggle_shortcut

    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    await db.flush()

    resp = await admin_port_toggle_shortcut(1, db=db, user=staff_user)
    assert resp.status_code == 303
    assert (await db.get(Port, 1)).is_shortcut is True

    await admin_port_toggle_shortcut(1, db=db, user=staff_user)
    assert (await db.get(Port, 1)).is_shortcut is False


def test_leg_form_has_dynamic_shortcuts_with_fallback():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/planning/leg_form.html")[0]
    assert "shortcut_ports" in src  # rendu dynamique des ports marqués
    assert "selectattr('is_shortcut')" in src
    assert "FRFEC" in src  # liste de repli conservée (pas de régression)


def test_admin_ports_has_shortcut_toggle():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/admin/ports.html")[0]
    assert "/toggle-shortcut" in src
