from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str) -> None:
    global _engine, _session_factory
    _engine = create_async_engine(database_url, pool_size=5, max_overflow=5, pool_pre_ping=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def create_all() -> None:
    """Быстрый способ создать таблицы для локальной разработки без Alembic.
    В проде схему накатывать через `alembic upgrade head`, а не этим вызовом."""

    assert _engine is not None, "Сначала вызови init_engine(settings.database_url)"
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    assert _session_factory is not None, "Сначала вызови init_engine(settings.database_url)"
    async with _session_factory() as session:
        yield session
