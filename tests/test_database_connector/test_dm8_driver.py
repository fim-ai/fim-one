"""Tests for the DM8 (达梦) database driver.

``dmPython`` is a vendor wheel that is not present in CI. The tests
therefore stub out the import with a fake module (``sys.modules``
injection) and verify the driver shape, SQL it emits, and the error
message when the real package is missing.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from fim_one.core.tool.connector.database.base import (
    ColumnInfo,
    QueryResult,
    TableInfo,
)


def _make_fake_dmpython(cursor: MagicMock, conn: MagicMock) -> types.ModuleType:
    """Build a fake ``dmPython`` module whose ``connect`` returns ``conn``."""
    mod = types.ModuleType("dmPython")
    conn.cursor.return_value = cursor
    # ``ModuleType`` has no declared attributes; use setattr so mypy accepts
    # the dynamic patch without an attr-defined error.
    mod.connect = MagicMock(return_value=conn)  # type: ignore[attr-defined]
    return mod


@pytest.fixture()
def fake_cursor() -> MagicMock:
    cursor = MagicMock()
    cursor.description = None
    cursor.fetchall.return_value = []
    cursor.fetchmany.return_value = []
    cursor.execute = MagicMock(return_value=None)
    cursor.close = MagicMock(return_value=None)
    return cursor


@pytest.fixture()
def fake_conn(fake_cursor: MagicMock) -> MagicMock:
    conn = MagicMock()
    conn.cursor.return_value = fake_cursor
    conn.close = MagicMock(return_value=None)
    return conn


@pytest.fixture()
def patch_dmpython(
    monkeypatch: pytest.MonkeyPatch, fake_cursor: MagicMock, fake_conn: MagicMock
) -> types.ModuleType:
    """Install a fake ``dmPython`` module into ``sys.modules`` for the test."""
    mod = _make_fake_dmpython(fake_cursor, fake_conn)
    monkeypatch.setitem(sys.modules, "dmPython", mod)
    return mod


@pytest.fixture()
def driver_config() -> dict[str, Any]:
    return {
        "driver": "dm8",
        "host": "10.0.0.42",
        "port": 5236,
        "username": "SYSDBA",
        "password": "SYSDBA",
        "database": "DAMENG",
    }


class TestDM8Registry:
    """Registry sanity: ``dm8`` must be discoverable."""

    def test_dm8_in_registry(self) -> None:
        from fim_one.core.tool.connector.database.drivers import DRIVER_REGISTRY

        assert "dm8" in DRIVER_REGISTRY

    def test_pool_routes_dm8(self, driver_config: dict[str, Any]) -> None:
        from fim_one.core.tool.connector.database.drivers.dm8 import DM8Driver
        from fim_one.core.tool.connector.database.pool import ConnectionPoolManager

        driver = ConnectionPoolManager._create_driver(driver_config)
        assert isinstance(driver, DM8Driver)


class TestDM8LazyImport:
    """Driver must fail with a friendly message when dmPython is missing."""

    @pytest.mark.asyncio
    async def test_missing_dmpython_raises_install_hint(
        self, monkeypatch: pytest.MonkeyPatch, driver_config: dict[str, Any]
    ) -> None:
        from fim_one.core.tool.connector.database.drivers.dm8 import DM8Driver

        # Make sure dmPython is NOT importable for this test.
        monkeypatch.delitem(sys.modules, "dmPython", raising=False)

        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "dmPython":
                raise ImportError("No module named 'dmPython'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        driver = DM8Driver(driver_config)
        with pytest.raises(RuntimeError, match=r"dmPython"):
            await driver.connect()


class TestDM8ConnectAndIntrospect:
    """Exercise connect / list_tables / describe_table against a fake driver."""

    @pytest.mark.asyncio
    async def test_connect_invokes_dmpython_connect(
        self,
        patch_dmpython: types.ModuleType,
        driver_config: dict[str, Any],
        fake_conn: MagicMock,
    ) -> None:
        from fim_one.core.tool.connector.database.drivers.dm8 import DM8Driver

        driver = DM8Driver(driver_config)
        await driver.connect()

        connect_mock: MagicMock = patch_dmpython.connect
        connect_mock.assert_called_once()
        call_kwargs = connect_mock.call_args.kwargs
        assert call_kwargs["user"] == "SYSDBA"
        assert call_kwargs["password"] == "SYSDBA"
        assert call_kwargs["server"] == "10.0.0.42"
        assert call_kwargs["port"] == 5236
        assert driver._conn is fake_conn

    @pytest.mark.asyncio
    async def test_list_tables_parses_catalog(
        self,
        patch_dmpython: types.ModuleType,
        driver_config: dict[str, Any],
        fake_cursor: MagicMock,
    ) -> None:
        from fim_one.core.tool.connector.database.drivers.dm8 import DM8Driver

        # Cursor returns (table_name, col_count) tuples.
        fake_cursor.fetchall.return_value = [
            ("USERS", 5),
            ("ORDERS", 12),
        ]

        driver = DM8Driver(driver_config)
        await driver.connect()
        tables = await driver.list_tables()

        assert tables == [
            TableInfo(table_name="USERS", column_count=5),
            TableInfo(table_name="ORDERS", column_count=12),
        ]

        # Verify it targets ALL_TABLES with the uppercased owner.
        executed_sql = fake_cursor.execute.call_args.args[0]
        executed_params = fake_cursor.execute.call_args.args[1]
        assert "ALL_TABLES" in executed_sql
        assert executed_params == ("SYSDBA",)

    @pytest.mark.asyncio
    async def test_describe_table_marks_primary_key(
        self,
        patch_dmpython: types.ModuleType,
        driver_config: dict[str, Any],
        fake_cursor: MagicMock,
    ) -> None:
        from fim_one.core.tool.connector.database.drivers.dm8 import DM8Driver

        # Each row: (column_name, data_type, nullable_flag, is_pk)
        fake_cursor.fetchall.return_value = [
            ("ID", "INT", "N", 1),
            ("NAME", "VARCHAR", "Y", 0),
        ]

        driver = DM8Driver(driver_config)
        await driver.connect()
        cols = await driver.describe_table("users")

        assert cols == [
            ColumnInfo(
                column_name="ID",
                data_type="INT",
                is_nullable=False,
                is_primary_key=True,
            ),
            ColumnInfo(
                column_name="NAME",
                data_type="VARCHAR",
                is_nullable=True,
                is_primary_key=False,
            ),
        ]

        # Parameters: (owner, TABLE_NAME_UPPER)
        executed_params = fake_cursor.execute.call_args.args[1]
        assert executed_params == ("SYSDBA", "USERS")


class TestDM8ExecuteQuery:
    """End-to-end query path: columns, truncation, serialization."""

    @pytest.mark.asyncio
    async def test_query_returns_columns_and_rows(
        self,
        patch_dmpython: types.ModuleType,
        driver_config: dict[str, Any],
        fake_cursor: MagicMock,
    ) -> None:
        from fim_one.core.tool.connector.database.drivers.dm8 import DM8Driver

        fake_cursor.description = [("ID",), ("NAME",)]
        fake_cursor.fetchmany.return_value = [(1, "alice"), (2, "bob")]

        driver = DM8Driver(driver_config)
        await driver.connect()
        result = await driver.execute_query("SELECT id, name FROM users", max_rows=10)

        assert isinstance(result, QueryResult)
        assert result.columns == ["ID", "NAME"]
        assert result.rows == [[1, "alice"], [2, "bob"]]
        assert result.row_count == 2
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_query_truncates_when_over_max_rows(
        self,
        patch_dmpython: types.ModuleType,
        driver_config: dict[str, Any],
        fake_cursor: MagicMock,
    ) -> None:
        from fim_one.core.tool.connector.database.drivers.dm8 import DM8Driver

        fake_cursor.description = [("ID",)]
        # Return max_rows + 1 = 3 rows for max_rows=2 → truncated.
        fake_cursor.fetchmany.return_value = [(1,), (2,), (3,)]

        driver = DM8Driver(driver_config)
        await driver.connect()
        result = await driver.execute_query("SELECT id FROM t", max_rows=2)

        assert result.row_count == 2
        assert result.truncated is True
        assert result.rows == [[1], [2]]

    @pytest.mark.asyncio
    async def test_query_wraps_driver_errors(
        self,
        patch_dmpython: types.ModuleType,
        driver_config: dict[str, Any],
        fake_cursor: MagicMock,
    ) -> None:
        from fim_one.core.tool.connector.database.drivers.dm8 import DM8Driver

        fake_cursor.execute.side_effect = RuntimeError("ORA-00942: table does not exist")

        driver = DM8Driver(driver_config)
        await driver.connect()

        with pytest.raises(RuntimeError, match=r"DM8 error"):
            await driver.execute_query("SELECT * FROM missing", max_rows=10)
