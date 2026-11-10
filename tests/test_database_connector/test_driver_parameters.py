"""Each driver hands bound values to its database library, not to the SQL text."""

from __future__ import annotations

import decimal
import sys
import types
from datetime import date, datetime
from typing import Any
from unittest.mock import MagicMock

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

    @pytest.mark.parametrize("value", [1.5, -0.5, 1e-9])
    def test_a_number_with_a_fraction_is_refused_for_an_integer_parameter(
        self, value: float
    ) -> None:
        """asyncpg would truncate it, and 1.5 would find the row with id 1."""
        with pytest.raises(ValueError, match="not a whole number"):
            _fit_parameters(_types("int4"), [value])

    def test_json_text_of_a_list_fits_an_array_parameter(self) -> None:
        assert _fit_parameters(_types("int4[]", "text[]"), ["[1, \"2\"]", '["a"]']) == [
            [1, 2],
            ["a"],
        ]
        assert _fit_parameters(_types("int4[]"), ["not a list"]) == ["not a list"]

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


class TestMySQLTakesNoParameters:
    async def test_values_are_bound_before_the_driver_as_literals(self) -> None:
        """aiomysql would escape them for a character set the session may not be in."""
        driver = MySQLDriver({})
        driver._pool = MagicMock()
        with pytest.raises(RuntimeError, match="takes no parameters"):
            await driver.execute_query("SELECT 1 FROM t WHERE a = %s", parameters=["x"])
        assert MySQLDriver.placeholder_style == "literal"


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
