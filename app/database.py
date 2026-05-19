"""Async SQLAlchemy engine + session factory.

`get_db()` is the FastAPI dependency that yields a session, commits on
success, rolls back on exception, and closes deterministically.
"""
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """Common base for all ORM models."""


def _build_engine() -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        echo=settings.debug,
        future=True,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        pool_recycle=1800,
    )


engine: AsyncEngine = _build_engine()

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, Any]:
    """FastAPI dependency. Auto-commit on success, auto-rollback on failure."""
    session: AsyncSession = SessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db() -> None:
    """Create tables if missing — used only in development.

    In staging/production, schema is managed by Alembic exclusively.
    """
    # Import models so they register with Base.metadata
    from app import models  # noqa: F401

    if settings.app_env == "development":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
