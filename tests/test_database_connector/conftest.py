"""Real databases for the driver tests, when one is offered.

``FIM_TEST_PG_URL`` (PostgreSQL) and ``FIM_TEST_MYSQL_URL`` (MySQL) point at
a throwaway database; the tests that need one are skipped without it. Each
test gets tables of its own (a fresh schema on PostgreSQL, a name prefix on
MySQL) holding two orders and one secret that no query below may return.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.engine import make_url

from fim_one.core.tool.connector.database.pool import ConnectionPoolManager

SECRET = "s3cr3t-token"


@dataclass
class LiveDatabase:
    """A database connector's config, and the tables seeded for one test."""

    config: dict[str, Any]
    connector_id: str
    orders: str
    secrets: str


def _config(env: str, driver: str) -> dict[str, Any]:
    url = make_url(os.environ[env])
    return {
        "driver": driver,
        "host": url.host or "127.0.0.1",
        "port": url.port,
        "username": url.username or "",
        "password": url.password or "",
        "database": url.database or "",
    }


@pytest.fixture()
async def live_pg() -> AsyncIterator[LiveDatabase]:
    if not os.environ.get("FIM_TEST_PG_URL"):
        pytest.skip("FIM_TEST_PG_URL not set")
    import asyncpg  # type: ignore[import-untyped]

    config = _config("FIM_TEST_PG_URL", "postgresql")
    schema = f"dbtool_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(
        host=config["host"],
        port=config["port"],
        user=config["username"],
        password=config["password"],
        database=config["database"],
    )
    await admin.execute(
        f"""
        CREATE SCHEMA {schema};
        CREATE TABLE {schema}.orders (id integer PRIMARY KEY, customer text, note text);
        INSERT INTO {schema}.orders VALUES
            (1, 'alice', 'first'), (2, 'O''Brien \\ -- x', 'second');
        CREATE TABLE {schema}.secrets (id integer PRIMARY KEY, token text);
        INSERT INTO {schema}.secrets VALUES (1, '{SECRET}');
        """
    )
    live = LiveDatabase(
        config=config,
        connector_id=f"live-{schema}",
        orders=f"{schema}.orders",
        secrets=f"{schema}.secrets",
    )
    try:
        yield live
    finally:
        await ConnectionPoolManager.get_instance().close_driver(live.connector_id)
        await admin.execute(f"DROP SCHEMA {schema} CASCADE")
        await admin.close()


@pytest.fixture()
async def live_mysql() -> AsyncIterator[LiveDatabase]:
    if not os.environ.get("FIM_TEST_MYSQL_URL"):
        pytest.skip("FIM_TEST_MYSQL_URL not set")
    import aiomysql  # type: ignore[import-untyped]

    config = _config("FIM_TEST_MYSQL_URL", "mysql")
    prefix = f"dbtool_{uuid.uuid4().hex[:12]}"
    orders, secrets = f"{prefix}_orders", f"{prefix}_secrets"
    admin = await aiomysql.connect(
        host=config["host"],
        port=config["port"],
        user=config["username"],
        password=config["password"],
        db=config["database"],
        autocommit=True,
    )
    async with admin.cursor() as cur:
        await cur.execute(
            f"CREATE TABLE {orders} (id integer PRIMARY KEY, customer varchar(64), note text)"
        )
        await cur.execute(
            f"INSERT INTO {orders} VALUES (1, 'alice', 'first'), (2, %s, 'second')",
            ("O'Brien \\ -- x",),
        )
        await cur.execute(f"CREATE TABLE {secrets} (id integer PRIMARY KEY, token varchar(64))")
        await cur.execute(f"INSERT INTO {secrets} VALUES (1, %s)", (SECRET,))
    live = LiveDatabase(
        config=config, connector_id=f"live-{prefix}", orders=orders, secrets=secrets
    )
    try:
        yield live
    finally:
        await ConnectionPoolManager.get_instance().close_driver(live.connector_id)
        async with admin.cursor() as cur:
            await cur.execute(f"DROP TABLE {orders}")
            await cur.execute(f"DROP TABLE {secrets}")
        admin.close()
