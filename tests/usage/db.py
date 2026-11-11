"""A database the ledger writes to: SQLite, and PostgreSQL when one is given.

``FIM_TEST_PG_URL`` pointing at an empty throwaway database adds the
PostgreSQL case, in a schema of its own.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db.base import Base

__all__ = ["factory"]

_PG = os.environ.get("FIM_TEST_PG_URL")


def _backends() -> list[Any]:
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param(
            "postgresql",
            id="postgresql",
            marks=pytest.mark.skipif(not _PG, reason="FIM_TEST_PG_URL not set"),
        ),
    ]


@pytest.fixture(params=_backends())
async def factory(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions on an empty database, which ``fim_one.db.create_session`` hands out too."""
    admin = None
    schema = f"usage_{uuid.uuid4().hex[:12]}"
    if request.param == "sqlite":
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}", connect_args={"timeout": 30}
        )
    else:
        assert _PG is not None
        url = make_url(_PG).set(drivername="postgresql+asyncpg")
        admin = create_async_engine(url)
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            url, pool_size=20, connect_args={"server_settings": {"search_path": schema}}
        )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)

        @asynccontextmanager
        async def _create_session() -> AsyncIterator[AsyncSession]:
            async with maker() as session:
                yield session

        with patch("fim_one.db.create_session", _create_session):
            yield maker
    finally:
        await engine.dispose()
        if admin is not None:
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()
