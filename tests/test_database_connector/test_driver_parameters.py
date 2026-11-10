"""Each driver hands bound values to its database library, not to the SQL text."""

from __future__ import annotations

import decimal
import sys
import types
from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from fim_one.core.tool.connector.database.drivers.mysql import MySQLDriver
from fim_one.core.tool.connector.database.drivers.postgresql import _fit_parameters


def _types(*names: str) -> list[Any]:
    return [types.SimpleNamespace(name=name) for name in names]


class TestPostgreSQLFitsValuesToTheParameterTypes:
    def test_text_that_reads_as_the_wanted_type_is_converted(self) -> None:
        assert _fit_parameters(
            _types("int4", "int8", "float8", "numeric", "bool", "date", "timestamptz"),
            ["42", " -7 ", "1.5", "2.50", "true", "2026-11-10", "2026-11-10T08:00:00+00:00"],
        ) == [
            42,
            -7,
            1.5,
            decimal.Decimal("2.50"),
            True,
            date(2026, 11, 10),
            datetime.fromisoformat("2026-11-10T08:00:00+00:00"),
        ]

    def test_numbers_and_booleans_for_a_text_parameter_are_sent_as_text(self) -> None:
        assert _fit_parameters(
            _types("text", "varchar", "text", "unknown"), [42, 1.5, True, "x"]
        ) == ["42", "1.5", "true", "x"]

    def test_a_whole_float_fits_an_integer_parameter(self) -> None:
        assert _fit_parameters(_types("int4"), [3.0]) == [3]

    def test_null_stays_null(self) -> None:
        assert _fit_parameters(_types("int4", "text"), [None, None]) == [None, None]

    @pytest.mark.parametrize("text", ["1 OR 1=1", "1_000", "٣", "0x10", "", "1e3"])
    def test_text_that_is_not_an_integer_is_left_for_the_database_to_refuse(
        self, text: str
    ) -> None:
        assert _fit_parameters(_types("int4"), [text]) == [text]

    def test_a_count_mismatch_is_an_error(self) -> None:
        with pytest.raises(RuntimeError, match="takes 1 parameter"):
            _fit_parameters(_types("int4"), [])


class _Acquire:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *exc: object) -> None:
        return None


class TestMySQLPassesValuesToTheCursor:
    async def _run(self, sql: str, parameters: list[Any] | None) -> list[Any]:
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.fetchmany = AsyncMock(return_value=[])
        cursor.description = None
        conn = MagicMock()
        conn.cursor = MagicMock(return_value=_Acquire(cursor))
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_Acquire(conn))
        driver = MySQLDriver({})
        driver._pool = pool
        await driver.execute_query(sql, parameters=parameters)
        return [call.args for call in cursor.execute.await_args_list]

    async def test_values_travel_beside_the_sql(self) -> None:
        calls = await self._run("SELECT 1 FROM t WHERE a = %s", ["x' OR 1=1 --"])
        assert ("SELECT 1 FROM t WHERE a = %s", ("x' OR 1=1 --",)) in calls

    async def test_sql_with_no_values_is_not_percent_formatted(self) -> None:
        calls = await self._run("SELECT 1 FROM t WHERE a LIKE 'x%'", None)
        assert ("SELECT 1 FROM t WHERE a LIKE 'x%'", None) in calls


class TestDM8PassesValuesToTheCursor:
    async def test_values_travel_beside_the_sql(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fim_one.core.tool.connector.database.drivers.dm8 import DM8Driver

        cursor = MagicMock()
        cursor.description = [("ID",)]
        cursor.fetchmany.return_value = [(1,)]
        conn = MagicMock()
        conn.cursor.return_value = cursor
        module = types.ModuleType("dmPython")
        module.connect = MagicMock(return_value=conn)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "dmPython", module)

        driver = DM8Driver({"username": "u"})
        await driver.connect()
        await driver.execute_query("SELECT ID FROM T WHERE A = ?", parameters=["x' OR 1=1 --"])
        cursor.execute.assert_called_once_with(
            "SELECT ID FROM T WHERE A = ?", ("x' OR 1=1 --",)
        )
