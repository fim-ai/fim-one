"""Connection pool manager for database connector drivers.

Maintains a per-connector cache of :class:`DatabaseDriver` instances
with LRU eviction when the pool exceeds ``max_pool_size``.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

from .base import DatabaseDriver

logger = logging.getLogger(__name__)

DEFAULT_MAX_POOL_SIZE = 50


class ConnectionPoolManager:
    """Singleton manager that caches ``DatabaseDriver`` instances by connector ID.

    Drivers are lazily created and LRU-evicted when the cache exceeds
    ``max_pool_size``.
    """

    _instance: ConnectionPoolManager | None = None
    _lock: asyncio.Lock | None = None

    def __new__(cls) -> ConnectionPoolManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._drivers = OrderedDict()
            cls._instance._max_pool_size = DEFAULT_MAX_POOL_SIZE
        return cls._instance

    @classmethod
    def get_instance(cls) -> ConnectionPoolManager:
        """Return the singleton instance."""
        return cls()

    async def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def get_driver(
        self, connector_id: str, config: dict[str, Any]
    ) -> DatabaseDriver:
        """Get or create a driver for the given connector.

        Parameters
        ----------
        connector_id:
            Unique connector identifier used as cache key.
        config:
            Database connection config dict containing ``driver``, ``host``,
            ``port``, ``database``, ``username``, ``password``, etc.

        Returns
        -------
        DatabaseDriver
            A connected driver instance ready for queries.
        """
        lock = await self._get_lock()
        async with lock:
            if connector_id in self._drivers:
                # Move to end for LRU
                self._drivers.move_to_end(connector_id)
                return self._drivers[connector_id]

            # Evict oldest if at capacity
            while len(self._drivers) >= self._max_pool_size:
                evict_id, evict_driver = self._drivers.popitem(last=False)
                logger.info("Evicting driver for connector %s (pool full)", evict_id)
                try:
                    await evict_driver.disconnect()
                except Exception:
                    logger.debug("Error disconnecting evicted driver", exc_info=True)

            # Create new driver
            driver = self._create_driver(config)
            await driver.connect()
            self._drivers[connector_id] = driver
            return driver

    async def close_driver(self, connector_id: str) -> None:
        """Close and remove a specific driver from the pool."""
        lock = await self._get_lock()
        async with lock:
            driver = self._drivers.pop(connector_id, None)
            if driver:
                try:
                    await driver.disconnect()
                except Exception:
                    logger.debug("Error closing driver %s", connector_id, exc_info=True)

    async def close_all(self) -> None:
        """Close all drivers. Call during application shutdown."""
        lock = await self._get_lock()
        async with lock:
            for cid, driver in self._drivers.items():
                try:
                    await driver.disconnect()
                except Exception:
                    logger.debug("Error closing driver %s", cid, exc_info=True)
            self._drivers.clear()

    @staticmethod
    def _create_driver(config: dict[str, Any]) -> DatabaseDriver:
        """Instantiate the correct driver based on config['driver']."""
        from .drivers import DRIVER_REGISTRY

        driver_type = config.get("driver", "postgresql")
        driver_cls = DRIVER_REGISTRY.get(driver_type)
        if driver_cls is None:
            raise ValueError(
                f"Unsupported database driver: {driver_type!r}. "
                f"Available: {list(DRIVER_REGISTRY.keys())}"
            )
        return driver_cls(config)
