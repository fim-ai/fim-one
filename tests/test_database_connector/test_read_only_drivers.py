"""Which drivers put a read-only connector's query in a read-only transaction."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fim_one.core.tool.connector.database.drivers import DRIVER_REGISTRY
from fim_one.core.tool.connector.database.drivers.mysql import MySQLDriver


class _Acquire:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *exc: object) -> None:
        return None


def test_which_drivers_enforce_read_only_in_the_database() -> None:
    assert {name: cls.enforces_read_only for name, cls in DRIVER_REGISTRY.items()} == {
        "postgresql": True,
        "kingbasees": True,
        "highgo": True,
        "mysql": True,
        # No read-only transaction: the keyword check is all there is.
        "dm8": False,
    }


class TestMySQL:
    async def _statements(self, *, read_only: bool, fails: bool = False) -> list[str]:
        cursor = MagicMock()
        ran: list[str] = []

        async def _execute(sql: str, args: Any = None) -> None:
            ran.append(sql)
            if fails and sql.startswith("DELETE"):
                raise RuntimeError("(1792, 'Cannot execute statement in a READ ONLY transaction.')")

        cursor.execute = _execute
        cursor.fetchmany = AsyncMock(return_value=[])
        cursor.description = None
        cursor.close = AsyncMock()
        conn = MagicMock(closed=False)
        conn.cursor = AsyncMock(return_value=cursor)
        driver = MySQLDriver({})
        driver._pool = MagicMock()
        driver._session = lambda: _Acquire(conn)  # type: ignore[method-assign]
        try:
            sql = "DELETE FROM t" if fails else "SELECT 1"
            await driver.execute_query(sql, read_only=read_only)
        except RuntimeError:
            assert fails
        return ran

    async def test_a_read_only_query_runs_inside_a_read_only_transaction(self) -> None:
        ran = await self._statements(read_only=True)
        assert ran[1:] == ["START TRANSACTION READ ONLY", "SELECT 1", "ROLLBACK"]

    async def test_the_transaction_is_ended_when_the_query_fails(self) -> None:
        ran = await self._statements(read_only=True, fails=True)
        assert ran[1:] == ["START TRANSACTION READ ONLY", "DELETE FROM t", "ROLLBACK"]

    async def test_a_connector_that_allows_writes_opens_no_transaction(self) -> None:
        ran = await self._statements(read_only=False)
        assert "START TRANSACTION READ ONLY" not in ran and "ROLLBACK" not in ran
