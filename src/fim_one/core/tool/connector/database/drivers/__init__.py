"""Database driver registry.

Maps driver name strings to their implementing classes.
Drivers are imported lazily when first accessed to avoid
hard dependencies on optional packages (asyncpg, aiomysql).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fim_one.core.tool.connector.database.base import DatabaseDriver

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

    try:
        from .kingbasees import KingbaseESDriver

        DRIVER_REGISTRY["kingbasees"] = KingbaseESDriver
    except ImportError:
        pass

    try:
        from .highgo import HighGoDriver

        DRIVER_REGISTRY["highgo"] = HighGoDriver
    except ImportError:
        pass

    # DM8 driver module imports cleanly even without ``dmPython`` because
    # it loads the vendor package lazily. We register it unconditionally so
    # the registry exposes a friendly error the first time a user tries
    # to connect without the wheel installed.
    try:
        from .dm8 import DM8Driver

        DRIVER_REGISTRY["dm8"] = DM8Driver
    except ImportError:  # pragma: no cover - defensive
        pass


_register_drivers()
