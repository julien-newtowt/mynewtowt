"""Fixtures d'intégration — session SQLite asynchrone avec FK activées.

L'enforcement des clés étrangères est **désactivé par défaut sous SQLite** ;
on l'active explicitement (`PRAGMA foreign_keys=ON`) pour que les gardes de
suppression du module RH soient réellement vérifiées (sinon une suppression
en violation de FK passerait silencieusement en test mais casserait en
Postgres). Un `StaticPool` garde une connexion unique → la base in-memory
persiste sur tout le test ; `dispose()` ferme proprement le thread aiosqlite
en fin de test (sans quoi la boucle asyncio se bloque à la fermeture).

NB : le `dispose()` laisse la socketpair interne de la boucle asyncio être
collectée plus tard par le GC → un ``ResourceWarning`` « unclosed socket »
remonté en ``PytestUnraisableExceptionWarning``. C'est un artefact connu des
suites async (aiosqlite + boucle pytest-asyncio), neutralisé par un filtre
ciblé dans ``pyproject.toml`` (et uniquement celui-là).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — enregistre tous les modèles sur Base.metadata
from app.database import Base
from app.models.leg import Leg
from app.models.port import Port
from app.models.user import User
from app.models.vessel import Vessel


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _record):  # pragma: no cover - hook bas niveau
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session = async_sessionmaker(engine, expire_on_commit=False)()
    session.add(
        User(
            id=1,
            username="admin",
            email="admin@example.test",
            hashed_password="x",
            role="administrateur",
        )
    )
    await session.flush()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


@pytest_asyncio.fixture
def staff_user():
    """Utilisateur factice passé aux coroutines (bypass require_permission)."""
    return SimpleNamespace(id=1, full_name="Admin Test", username="admin", role="administrateur")


class FakeRequest:
    """Requête minimale pour appeler les coroutines de route hors ASGI."""

    def __init__(self, form: dict | None = None):
        self._form = dict(form or {})
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="127.0.0.1")
        # Accessoires lus par les context processors Jinja (i18n / layout staff)
        # au rendu d'un TemplateResponse : sans eux, toute route qui rend un
        # gabarit staff lève ``AttributeError`` (``request.state`` absent). Les
        # doter ici rend ``FakeRequest`` utilisable pour les écrans SSR.
        self.state = SimpleNamespace()
        self.cookies: dict[str, str] = {}
        self.query_params: dict[str, str] = {}
        self.url = SimpleNamespace(path="/")

    async def form(self):
        return self._form


async def _setup_leg(db):
    """Vessel + 2 ports + un leg de référence (voyage FR→BR, navire ANE).

    Relocalisé depuis l'ex-``tests/integration/test_mrv_reprise.py`` (legacy
    MRV supprimé) — partagé par de nombreuses suites qui n'ont aucun rapport
    avec le MRV, d'où son emplacement ici plutôt que dans un fichier de test
    spécifique.
    """
    db.add(Vessel(id=1, code="ANE", name="Anemos", imo_number="9876543", flag="FR"))
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


# ─────────────────────────── LOT 14 — bascule capture v2 ─────────────────────
@pytest.fixture(autouse=True)
def _reset_capture_v2_cache():
    """Vide le cache module de la garde de bascule entre chaque test.

    ``capture_v2_enabled`` met en cache (par navire) l'état du flag ; le cache
    est global au process → sans reset, un état posé par un test fuiterait sur
    le suivant (la base SQLite est neuve à chaque test, pas le cache Python)."""
    from app.services.feature_flags import reset_capture_v2_cache

    reset_capture_v2_cache()
    yield
    reset_capture_v2_cache()


async def disable_capture_v2(db, *vessel_codes):
    """Pose ``mrv_v2_capture`` en opt-out PAR NAVIRE (double-run inversé).

    Rend l'ancien formulaire noon actif (capture v2 OFF) pour ces navires —
    utilisé par les tests du flux noon legacy. Sans code → coupe globalement.
    """
    from app.models.feature_flag import FeatureFlag
    from app.services.feature_flags import reset_capture_v2_cache

    db.add(
        FeatureFlag(
            key="mrv_v2_capture",
            enabled=True,
            audience={"vessels_off": list(vessel_codes)},
            description="test — opt-out double-run",
        )
    )
    await db.flush()
    reset_capture_v2_cache()
