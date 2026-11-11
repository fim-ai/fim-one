"""SQL for the calendar day a timestamp falls on, in UTC.

``DATE(created_at)`` on PostgreSQL converts a ``timestamptz`` to the
session's ``TimeZone`` first, so the same row lands on different days
depending on the server's setting. :class:`utc_date` is the UTC date on
every dialect: ``timezone('UTC', col)::date`` on PostgreSQL, ``date(col)``
elsewhere (SQLite keeps the timestamps in UTC). Pair it with
``datetime.now(UTC).date()`` for "today".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Date
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement

__all__ = ["utc_date"]


class utc_date(FunctionElement[Any]):  # noqa: N801 - reads as a SQL function
    """``utc_date(column)``: the UTC calendar day of a timestamp column."""

    type = Date()
    name = "utc_date"
    inherit_cache = True


@compiles(utc_date)
def _utc_date_default(element: utc_date, compiler: Any, **kw: Any) -> str:
    return f"date({compiler.process(element.clauses, **kw)})"


@compiles(utc_date, "postgresql")
def _utc_date_postgresql(element: utc_date, compiler: Any, **kw: Any) -> str:
    return f"CAST(timezone('UTC', {compiler.process(element.clauses, **kw)}) AS DATE)"
