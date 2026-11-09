"""Grouping the call log by what was called.

A call record's ``resource_kind`` is ``connector``, ``database`` or ``mcp``.
Readers that list calls per resource group by *whether the row is an MCP
call* and by the resource's id, not by the kind itself: one database
connector can have rows of two kinds (records written before the kind
existed read ``connector``; the migration relabels those whose connector
still exists, the rest keep the old value), and it is still one resource.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, literal_column

from fim_one.db.models.connector_call_log import ConnectorCallLog

__all__ = ["any_database", "is_mcp", "kind_of"]

# Literals, not bound parameters: PostgreSQL only accepts an expression in
# the select list as "the one grouped by" when the two are textually equal,
# and each use of a bound parameter gets a placeholder of its own.
_MCP: Any = literal_column("'mcp'")
_DATABASE: Any = literal_column("'database'")
_ONE: Any = literal_column("1")
_ZERO: Any = literal_column("0")


def is_mcp() -> Any:
    """Group key: 1 for an MCP call, 0 for a connector or database call."""
    return case((ConnectorCallLog.resource_kind == _MCP, _ONE), else_=_ZERO)


def any_database() -> Any:
    """Aggregate: 1 when any row of the group is a database call."""
    return func.max(case((ConnectorCallLog.resource_kind == _DATABASE, _ONE), else_=_ZERO))


def kind_of(mcp_flag: Any, database_flag: Any) -> str:
    """The kind of a group from its :func:`is_mcp` and :func:`any_database`."""
    if mcp_flag:
        return "mcp"
    return "database" if database_flag else "connector"
