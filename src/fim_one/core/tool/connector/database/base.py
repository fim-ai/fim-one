"""Abstract base class and data structures for database drivers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal


@dataclass
class TableInfo:
    """Metadata for a single database table."""

    table_name: str
    display_name: str | None = None
    description: str | None = None
    column_count: int = 0


@dataclass
class ColumnInfo:
    """Metadata for a single table column."""

    column_name: str
    data_type: str
    is_nullable: bool = True
    is_primary_key: bool = False
    display_name: str | None = None
    description: str | None = None


@dataclass
class QueryResult:
    """Result of executing a SQL query."""

    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    execution_time_ms: float = 0


class DatabaseDriver(ABC):
    """Abstract base for all database drivers.

    Each driver implementation wraps a specific async database library
    (asyncpg, aiomysql, etc.) and exposes a unified interface for
    connection management, schema introspection, and query execution.
    """

    # How this driver writes a bound parameter (``$1``, ``%s`` or ``?``).
    # ``None``: the driver cannot take parameters, and SQL with
    # ``{{variable}}`` references is refused for it.
    placeholder_style: ClassVar[Literal["numeric", "format", "qmark"] | None] = None
    # Which comment and quoting rules the database reads SQL by.
    sql_dialect: ClassVar[Literal["postgresql", "mysql", "generic"]] = "generic"

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    @abstractmethod
    async def connect(self) -> None:
        """Establish a connection (or pool) to the database."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the connection (or pool)."""

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        """Test connectivity and return ``(success, version_or_error)``."""

    @abstractmethod
    async def list_tables(self, schema: str | None = None) -> list[TableInfo]:
        """List all user tables in the given schema."""

    @abstractmethod
    async def describe_table(
        self, table_name: str, schema: str | None = None
    ) -> list[ColumnInfo]:
        """Return column metadata for a single table."""

    @abstractmethod
    async def execute_query(
        self,
        sql: str,
        *,
        timeout_s: int = 30,
        max_rows: int = 1000,
        parameters: Sequence[Any] | None = None,
    ) -> QueryResult:
        """Execute a query and return results.

        *parameters* are the values of the placeholders in *sql*, written in
        the driver's :attr:`placeholder_style`. They are sent to the database
        as values, never as SQL text.
        """
