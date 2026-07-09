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

from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — enregistre tous les modèles sur Base.metadata
from app.database import Base
from app.models.user import User


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
