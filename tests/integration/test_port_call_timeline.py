"""ESC-08 (tranche) — timeline du flux opérationnel d'escale (5 étapes).

`port_call_steps` dérive l'état (done / current / pending) de chaque étape ;
la première étape non terminée, dans l'ordre, est ``current``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace


def _leg(ata=None, atd=None, locked=None):
    return SimpleNamespace(ata=ata, atd=atd, escale_locked_at=locked)


def _op(end=None, start=None):
    return SimpleNamespace(actual_start=start, actual_end=end)


def test_planned_only():
    from app.services.leg_overview import port_call_steps

    steps = port_call_steps(_leg(), [])
    assert len(steps) == 5
    states = {s["key"]: s["state"] for s in steps}
    assert states["planifie"] == "done"
    assert states["arrivee"] == "current"  # 1re étape non terminée
    assert states["operations"] == "pending"
    assert states["cloture"] == "pending"


def test_arrived_with_ops_in_progress():
    from app.services.leg_overview import port_call_steps

    t = datetime(2026, 1, 1, tzinfo=UTC)
    leg = _leg(ata=t)
    ops = [_op(end=t), _op(start=t)]  # une terminée, une en cours → pas toutes finies
    states = {s["key"]: s["state"] for s in port_call_steps(leg, ops)}
    assert states["arrivee"] == "done"
    assert states["operations"] == "current"
    assert states["appareillage"] == "pending"


def test_fully_closed():
    from app.services.leg_overview import port_call_steps

    t = datetime(2026, 1, 1, tzinfo=UTC)
    leg = _leg(ata=t, atd=t, locked=t)
    states = {s["key"]: s["state"] for s in port_call_steps(leg, [_op(end=t)])}
    assert all(
        states[k] == "done"
        for k in ("planifie", "arrivee", "operations", "appareillage", "cloture")
    )


def test_escale_template_has_timeline():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/escale/index.html")[0]
    assert "Flux opérationnel" in src
    assert "port_call" in src
