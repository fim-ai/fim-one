"""Async engine and session factory for SQLAlchemy.

SQLite concurrency notes
------------------------
SQLite supports only a single writer at a time.  We use the following
mitigations so that concurrent requests (e.g. SSE streaming + artifact
downloads) do **not** block each other:

1. **WAL journal mode** — allows readers to proceed while a write is in
   progress, drastically reducing lock contention.
2. **Increased busy timeout** (30 s) — if a write lock is held, other
   writers wait up to 30 s instead of failing immediately.
3. **QueuePool** with bounded size — multiple connections allow concurrent
   DB access; each request gets its own connection so long-running SSE
   streams do not starve short-lived queries.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from .base import Base

logger = logging.getLogger(__name__)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_database_url() -> str:
    return os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data/fim_one.db")


def is_sqlite_db() -> bool:
    """Return True if the configured database is SQLite."""
    return _get_database_url().startswith("sqlite")


async def init_db() -> None:
    """Create the async engine and session factory."""
    global _engine, _session_factory

    # Import all models so Base.metadata is fully populated.
    import fim_one.web.models  # noqa: F401

    url = _get_database_url()
    logger.info("Initializing database: %s", url.split("@")[-1] if "@" in url else url)

    connect_args: dict[str, object] = {}
    kwargs: dict[str, object] = {}
    is_sqlite = url.startswith("sqlite")

    if is_sqlite:
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 30
        kwargs["pool_size"] = int(os.environ.get("SQLITE_POOL_SIZE", "10"))
        kwargs["max_overflow"] = int(os.environ.get("SQLITE_MAX_OVERFLOW", "5"))
        db_path = url.split("///", 1)[-1] if "///" in url else None
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    else:
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
        kwargs["pool_timeout"] = 30
        kwargs["pool_recycle"] = 1800

    _engine = create_async_engine(url, connect_args=connect_args, echo=False, **kwargs)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    # -- SQLite-specific PRAGMAs -------------------------------------------
    if is_sqlite:

        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn: Any, connection_record: Any) -> None:  # noqa: ARG001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    logger.info("Database initialized successfully")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an ``AsyncSession`` — intended for use with FastAPI ``Depends``."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with _session_factory() as session:
        yield session


def create_session() -> AsyncSession:
    """Create an ``AsyncSession`` directly — caller must close it.

    Unlike :func:`get_session` (which is an async-generator suited for FastAPI
    ``Depends``), this returns a plain session object whose lifetime is managed
    by the caller.  Use this inside SSE async generators where breaking out of
    an ``async for`` would prematurely close the generator-managed session.
    """
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _session_factory()


async def shutdown_db() -> None:
    """Dispose of the engine and release all connections."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("Database engine disposed")
        _engine = None
        _session_factory = None
