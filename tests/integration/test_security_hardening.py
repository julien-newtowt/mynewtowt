"""Durcissements issus de la revue de sécurité de la reprise P0.

- Neutralisation de l'injection de formules CSV (exports planning/finance/kpi).
- Pré-filtre anti-OOM sur la taille des uploads (Content-Length).
- Niveau de permission « S » (Suppress) sur les routes de suppression.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

# ───────────────────── Injection de formules CSV ─────────────────────


def test_csv_sanitize_neutralizes_formula_prefixes():
    from app.utils.csv_safe import sanitize_cell

    for danger in ("=cmd|'/c calc'!A1", "+1+1", "@SUM(A1)", "-2+3", "\tx", "\rx"):
        out = sanitize_cell(danger)
        assert out.startswith("'"), danger


def test_csv_sanitize_preserves_numbers_and_dates():
    """Un nombre négatif (Decimal/float) NE doit pas être corrompu en texte."""
    from app.utils.csv_safe import sanitize_cell

    assert sanitize_cell(Decimal("-150.00")) == Decimal("-150.00")
    assert sanitize_cell(-150.0) == -150.0
    assert sanitize_cell(42) == 42
    assert sanitize_cell("2026-04-01T00:00:00") == "2026-04-01T00:00:00"  # ISO date OK
    assert sanitize_cell("FRFEC") == "FRFEC"  # locode bénin inchangé


def test_csv_sanitize_row_mixed():
    from app.utils.csv_safe import sanitize_row

    row = sanitize_row(["=HYPERLINK(1)", Decimal("-9.5"), "ok", 3])
    assert row[0] == "'=HYPERLINK(1)"
    assert row[1] == Decimal("-9.5")  # négatif préservé
    assert row[2] == "ok"
    assert row[3] == 3


@pytest.mark.asyncio
async def test_planning_csv_export_escapes_malicious_vessel_name(db, staff_user):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from app.models.leg import Leg
    from app.models.port import Port
    from app.models.vessel import Vessel
    from app.routers.planning_router import planning_export_csv

    class _Req:
        headers: dict[str, str] = {}
        cookies: dict[str, str] = {}
        client = SimpleNamespace(host="127.0.0.1")

    db.add(Vessel(id=1, code="ANE", name="=cmd|'/c calc'!A1"))  # nom hostile
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 3, 1, tzinfo=UTC)
    db.add(
        Leg(
            id=1,
            leg_code="1CFRBR6",
            vessel_id=1,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=base,
            eta_ref=base + timedelta(days=20),
            etd=base,
            eta=base + timedelta(days=20),
            status="planned",
        )
    )
    await db.flush()

    resp = await planning_export_csv(_Req(), vessel_id=None, year=2026, db=db, user=staff_user)
    text = resp.body.decode()
    assert "'=cmd" in text  # le nom hostile est neutralisé (apostrophe en tête)
    assert ",=cmd" not in text  # jamais de cellule formule brute


# ───────────────────── Pré-filtre taille d'upload ─────────────────────


def test_content_length_guard():
    from app.services.safe_files import content_length_exceeds_max

    assert content_length_exceeds_max(str(500 * 1024 * 1024)) is True  # 500 Mo → rejeté
    assert content_length_exceeds_max(str(1024)) is False  # 1 Ko → OK
    assert content_length_exceeds_max(None) is False  # absent → on lit (puis validate_size)
    assert content_length_exceeds_max("not-a-number") is False


# ───────────────────── Permissions de suppression (« S ») ─────────────────────


def test_destructive_deletes_require_suppress_level():
    """Les suppressions d'entités/fichiers exigent le niveau « S » (least privilege)."""
    import inspect

    from app.routers.captain_router import delete_leg_attachment, delete_sof_event
    from app.routers.commercial_router import order_assignment_delete, order_delete_attachment
    from app.routers.crew_router import crew_assignment_ticket_delete
    from app.routers.stowage_router import delete_item

    for fn in (
        delete_sof_event,
        delete_leg_attachment,
        order_assignment_delete,
        order_delete_attachment,
        delete_item,
        crew_assignment_ticket_delete,
    ):
        src = inspect.getsource(fn)
        assert '"S"' in src, f"{fn.__name__} devrait exiger le niveau S"
