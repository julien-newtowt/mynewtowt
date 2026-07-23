"""Unitaires — scheduler interne du trombinoscope (`services.trombinoscope_scheduler`).

Couvre uniquement l'enregistrement/l'arrêt du job APScheduler (idempotence,
toggle de configuration). Le déclenchement réel de `_generate_job` (accès
DB, rendu PDF, archivage, notification) est couvert indirectement par
tests/integration/test_report_archive.py et
tests/integration/test_trombinoscope_notification.py, qui exercent la même
logique via le chemin manuel — une exécution bout-en-bout du job planifié
nécessite une vraie session DB, non disponible ici.
"""

from __future__ import annotations

import pytest

from app.services import trombinoscope_scheduler as sched


@pytest.fixture(autouse=True)
async def _reset_scheduler():
    """Fixture volontairement `async` (pas juste `def` + yield) : `AsyncIOScheduler`
    capture la boucle asyncio active à `.start()`. pytest-asyncio crée une boucle
    par test — un fixture *synchrone* voit sa fermeture s'exécuter après que
    pytest-asyncio a déjà fermé cette boucle, et `shutdown()` échoue alors avec
    `RuntimeError: Event loop is closed`. En gardant le fixture async, son
    teardown s'exécute dans la même boucle que le corps du test."""
    sched.shutdown()
    yield
    sched.shutdown()


@pytest.mark.asyncio
async def test_start_registers_the_monthly_job(monkeypatch):
    monkeypatch.setattr(sched.settings, "trombinoscope_scheduler_enabled", True)
    sched.start()
    assert sched._scheduler is not None
    job = sched._scheduler.get_job("trombinoscope_monthly_generate")
    assert job is not None


@pytest.mark.asyncio
async def test_start_is_idempotent(monkeypatch):
    monkeypatch.setattr(sched.settings, "trombinoscope_scheduler_enabled", True)
    sched.start()
    first = sched._scheduler
    sched.start()  # deuxième appel — ne doit pas recréer d'instance
    assert sched._scheduler is first


@pytest.mark.asyncio
async def test_start_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(sched.settings, "trombinoscope_scheduler_enabled", False)
    sched.start()
    assert sched._scheduler is None


@pytest.mark.asyncio
async def test_shutdown_allows_restart(monkeypatch):
    monkeypatch.setattr(sched.settings, "trombinoscope_scheduler_enabled", True)
    sched.start()
    sched.shutdown()
    assert sched._scheduler is None
    sched.start()
    assert sched._scheduler is not None
