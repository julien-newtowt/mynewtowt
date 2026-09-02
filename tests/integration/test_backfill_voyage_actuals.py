"""Script de reprise des dates réelles — ``scripts.backfill_voyage_actuals``.

Rejoue un CSV ``leg_code,atd,ata`` par le chemin unique (voyage_transitions) :
séquence vérifiée, legs précédents terminés, dates futures ignorées, mode
quiet (aucune notification), idempotence au rejeu.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel

BASE = datetime(2026, 6, 16, tzinfo=UTC)


async def _setup(db):
    db.add(Vessel(id=1, code="1", name="Anemos"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    legs = [
        Leg(
            id=1,
            leg_code="1AFRBR6",
            vessel_id=1,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=BASE,
            eta_ref=BASE + timedelta(days=30),
            etd=BASE,
            eta=BASE + timedelta(days=30),
            port_stay_planned_hours=72,
        ),
        Leg(
            id=2,
            leg_code="1BBRFR6",
            vessel_id=1,
            departure_port_id=2,
            arrival_port_id=1,
            etd_ref=BASE + timedelta(days=36),
            eta_ref=BASE + timedelta(days=66),
            etd=BASE + timedelta(days=36),
            eta=BASE + timedelta(days=66),
            port_stay_planned_hours=72,
        ),
        Leg(
            id=3,
            leg_code="1CFRBR6",
            vessel_id=1,
            departure_port_id=1,
            arrival_port_id=2,
            etd_ref=BASE + timedelta(days=77),
            eta_ref=BASE + timedelta(days=108),
            etd=BASE + timedelta(days=77),
            eta=BASE + timedelta(days=108),
        ),
    ]
    db.add_all(legs)
    await db.flush()
    return legs


@pytest.mark.asyncio
async def test_backfill_replays_sequence_and_skips_future(db, tmp_path, monkeypatch):
    from scripts import backfill_voyage_actuals as script

    legs = await _setup(db)
    csv_path = tmp_path / "actuals.csv"
    csv_path.write_text(
        "leg_code,atd,ata\n"
        "1AFRBR6,2026-06-16,2026-07-19\n"
        "1BBRFR6,2026-07-22,2026-08-21\n"
        "1CFRBR6,2026-09-01,\n"
        "9ZZZZZ6,2026-01-01,\n",  # inconnu → ignoré, pas d'erreur
        encoding="utf-8",
    )

    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(script, "SessionLocal", _session)
    # Neutralise commit/rollback : la fixture gère la transaction de test.
    monkeypatch.setattr(db, "commit", _noop, raising=False)
    monkeypatch.setattr(db, "rollback", _noop, raising=False)

    today = datetime(2026, 8, 31, tzinfo=UTC)  # 1CFRBR6 part « demain » → ignoré
    rc = await script.run(csv_path, apply=True, today=today)
    assert rc == 0

    leg1, leg2, leg3 = legs
    assert leg1.atd is not None and leg1.ata is not None
    assert leg1.phase == "termine"  # terminé par le départ de 1BBRFR6
    # Arrivée réelle connue : l'ETA prévisionnelle n'est pas re-ancrée sur l'ATD.
    assert leg1.eta.replace(tzinfo=None) == (BASE + timedelta(days=30)).replace(tzinfo=None)
    assert leg2.eta.replace(tzinfo=None) == (BASE + timedelta(days=66)).replace(tzinfo=None)
    assert leg2.phase == "a_quai"  # arrivé, le suivant n'a pas encore appareillé
    assert leg3.atd is None and leg3.phase == "planifie"  # date future ignorée

    # Rejeu à l'identique : idempotent (aucun nouveau mouvement).
    from app.models.schedule_revision import ScheduleRevision

    n = len((await db.execute(ScheduleRevision.__table__.select())).fetchall())
    rc = await script.run(csv_path, apply=True, today=today)
    assert rc == 0
    assert len((await db.execute(ScheduleRevision.__table__.select())).fetchall()) == n

    # Le lendemain, le départ de 1CFRBR6 devient un fait : il termine 1BBRFR6.
    rc = await script.run(csv_path, apply=True, today=today + timedelta(days=1))
    assert rc == 0
    assert leg3.phase == "en_mer" and leg2.phase == "termine"
    active = [lg for lg in legs if lg.phase in ("en_mer", "a_quai")]
    assert active == [leg3]  # un seul leg actif par navire

    # Mode quiet : aucune notification émise par la reprise.
    from app.models.notification import Notification

    notifs = (await db.execute(Notification.__table__.select())).fetchall()
    assert [n.type for n in notifs if n.type in ("sosp", "eosp", "leg_activated")] == []


async def _noop(*_a, **_k):
    return None


def test_load_rows_rejects_incoherent_sequence(tmp_path):
    from scripts.backfill_voyage_actuals import load_rows

    bad = tmp_path / "bad.csv"
    bad.write_text("leg_code,atd,ata\n1AFRBR6,,2026-07-19\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        load_rows(bad)
    bad.write_text("leg_code,atd,ata\n1AFRBR6,2026-07-20,2026-07-19\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        load_rows(bad)
