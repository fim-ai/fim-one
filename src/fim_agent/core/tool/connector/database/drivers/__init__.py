"""Database driver registry.

Maps driver name strings to their implementing classes.
Drivers are imported lazily when first accessed to avoid
hard dependencies on optional packages (asyncpg, aiomysql).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fim_agent.core.tool.connector.database.base import DatabaseDriver

if TYPE_CHECKING:
    pass

# Populated below — each driver is imported inline so that missing
# optional packages only cause errors when that specific driver is used.
DRIVER_REGISTRY: dict[str, type[DatabaseDriver]] = {}


def _register_drivers() -> None:
    """Import and register all available drivers."""
    try:
        from .postgresql import PostgreSQLDriver

        DRIVER_REGISTRY["postgresql"] = PostgreSQLDriver
    except ImportError:
        pass

    try:
        from .mysql import MySQLDriver

        DRIVER_REGISTRY["mysql"] = MySQLDriver
    except ImportError:
        pass


_register_drivers()
