"""Abstract base class and data structures for database drivers."""

from __future__ import annotations

import asyncio
import functools
import ipaddress
import ssl
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from fim_one.core.security.db_target import authorize_database_target


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


class _NamedTlsContext(ssl.SSLContext):
    """A TLS context that presents a fixed server name.

    The driver connects to an already-checked IP address; this keeps the
    handshake's server name (SNI) equal to the host the connector was
    configured with.
    """

    _server_name: str | None = None

    def wrap_bio(self, *args: Any, **kwargs: Any) -> ssl.SSLObject:
        if self._server_name is not None:
            # (incoming, outgoing, server_side, server_hostname, session)
            if len(args) >= 4:
                args = (*args[:3], self._server_name, *args[4:])
            else:
                kwargs["server_hostname"] = self._server_name
        return super().wrap_bio(*args, **kwargs)


def tls_context_for(server_name: str | None) -> ssl.SSLContext:
    """An encrypting, non-verifying client context (asyncpg's ``require``)."""
    context = _NamedTlsContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context._server_name = server_name
    return context


def _checked_connect(
    connect: Callable[[Any], Awaitable[None]],
) -> Callable[[Any], Awaitable[None]]:
    @functools.wraps(connect)
    async def checked(self: Any) -> None:
        await self._authorize_target()
        await connect(self)

    setattr(checked, "_target_checked", True)
    return checked


class DatabaseDriver(ABC):
    """Abstract base for all database drivers.

    Each driver implementation wraps a specific async database library
    (asyncpg, aiomysql, etc.) and exposes a unified interface for
    connection management, schema introspection, and query execution.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Put the network check in front of every driver's ``connect``.

        A driver cannot opt out: whatever class defines ``connect`` gets it
        wrapped here, so a new driver is covered without doing anything.
        """
        super().__init_subclass__(**kwargs)
        own = cls.__dict__.get("connect")
        if own is not None and not getattr(own, "_target_checked", False):
            setattr(cls, "connect", _checked_connect(own))

    async def _authorize_target(self) -> None:
        """Check the configured host and pin the address the driver dials.

        Runs on every ``connect()``: the host the connector was configured
        with is resolved and checked again each time (see
        :mod:`fim_one.core.security.db_target`), and the driver's config is
        replaced by a private copy whose ``host`` is the checked address, so
        the pool, and any side connection the driver opens later, go to that
        address and never resolve the name a second time.
        """
        declared = getattr(self, "_declared_host", None)
        if declared is None:
            declared = self._config.get("host", "localhost")
            self._declared_host = declared
        config = dict(self._config)
        config["host"] = declared
        if not config.get("port"):
            config["port"] = self.default_port
        config["host"] = await asyncio.to_thread(authorize_database_target, config)
        self._config = config

    def _tls_server_name(self) -> str | None:
        """The host name to present in a TLS handshake, if one was configured."""
        declared = str(getattr(self, "_declared_host", "") or "").strip("[]")
        try:
            ipaddress.ip_address(declared)
        except ValueError:
            return declared or None
        return None

    # The port ``connect`` dials when the config names none.
    default_port: ClassVar[int] = 5432

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
        self, sql: str, *, timeout_s: int = 30, max_rows: int = 1000
    ) -> QueryResult:
        """Execute a read query and return results."""
