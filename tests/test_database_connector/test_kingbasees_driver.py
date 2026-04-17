"""Tests for the KingbaseES driver.

KingbaseES (人大金仓) is PostgreSQL protocol compatible, so its driver is
a thin subclass of :class:`PostgreSQLDriver`. These tests lock in that
inheritance contract and verify the registry routes ``kingbasees`` to
the PG driver.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.tool.connector.database.drivers import DRIVER_REGISTRY
from fim_one.core.tool.connector.database.drivers.kingbasees import KingbaseESDriver
from fim_one.core.tool.connector.database.drivers.postgresql import PostgreSQLDriver
from fim_one.core.tool.connector.database.pool import ConnectionPoolManager


class TestKingbaseESRegistry:
    """Registry wiring: ``kingbasees`` must resolve to a PG-compatible driver."""

    def test_registered(self) -> None:
        assert "kingbasees" in DRIVER_REGISTRY

    def test_driver_is_pg_subclass(self) -> None:
        """KingbaseES MUST subclass PostgreSQLDriver — the whole point of the
        stub is that we reuse asyncpg without duplicating code."""
        assert issubclass(DRIVER_REGISTRY["kingbasees"], PostgreSQLDriver)

    def test_pool_routes_kingbasees_to_pg_driver(self) -> None:
        """The pool factory must return a KingbaseESDriver instance for a
        ``driver="kingbasees"`` config (not a raw PG driver, not None)."""
        config: dict[str, Any] = {
            "driver": "kingbasees",
            "host": "localhost",
            "port": 54321,
            "username": "system",
            "password": "pw",
            "database": "test",
        }
        driver = ConnectionPoolManager._create_driver(config)
        assert isinstance(driver, KingbaseESDriver)
        assert isinstance(driver, PostgreSQLDriver)


class TestKingbaseESConnect:
    """Connection lifecycle should delegate to ``asyncpg.create_pool``."""

    @pytest.mark.asyncio
    async def test_connect_uses_asyncpg(self) -> None:
        """Instantiating + connecting should invoke asyncpg.create_pool with
        the KingbaseES default port (54321) when the caller passes it."""
        config: dict[str, Any] = {
            "driver": "kingbasees",
            "host": "10.0.0.1",
            "port": 54321,
            "username": "system",
            "password": "secret",
            "database": "kb_test",
        }
        driver = KingbaseESDriver(config)

        mock_pool = MagicMock()
        with patch(
            "fim_one.core.tool.connector.database.drivers.postgresql.asyncpg.create_pool",
            new=AsyncMock(return_value=mock_pool),
        ) as create_pool:
            await driver.connect()

            create_pool.assert_awaited_once()
            await_args = create_pool.await_args
            assert await_args is not None
            kwargs = await_args.kwargs
            assert kwargs["host"] == "10.0.0.1"
            assert kwargs["port"] == 54321
            assert kwargs["user"] == "system"
            assert kwargs["password"] == "secret"
            assert kwargs["database"] == "kb_test"

    @pytest.mark.asyncio
    async def test_test_connection_returns_version(self) -> None:
        """test_connection should run SELECT version() via the inherited
        PG code path and surface the banner verbatim."""
        config: dict[str, Any] = {
            "driver": "kingbasees",
            "host": "localhost",
            "port": 54321,
            "username": "system",
            "password": "",
            "database": "test",
        }
        driver = KingbaseESDriver(config)

        # Mock the asyncpg pool + connection path.
        mock_conn = MagicMock()
        mock_conn.fetchrow = AsyncMock(return_value=("KingbaseES V9.0.0",))

        # ``pool.acquire()`` returns an async context manager whose
        # __aenter__ yields the connection.
        mock_acquire_ctx = MagicMock()
        mock_acquire_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=mock_acquire_ctx)
        driver._pool = mock_pool

        success, version = await driver.test_connection()
        assert success is True
        assert "KingbaseES" in version
        mock_conn.fetchrow.assert_awaited_once_with("SELECT version()")
