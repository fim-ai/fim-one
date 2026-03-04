"""Async engine and session factory for SQLAlchemy.

SQLite concurrency notes
------------------------
SQLite supports only a single writer at a time.  To avoid
``sqlite3.OperationalError: database is locked`` under concurrent requests we
apply three mitigations:

1. **WAL journal mode** — allows readers to proceed while a write is in
   progress, drastically reducing lock contention.
2. **Increased busy timeout** (30 s) — the default is 5 s which is far too
   short when an LLM streaming endpoint holds a session open for tens of
   seconds.
3. **StaticPool** — a single shared connection via ``StaticPool`` so that all
   async tasks serialise through one underlying SQLite connection, eliminating
   multi-connection write contention entirely.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .base import Base

logger = logging.getLogger(__name__)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_database_url() -> str:
    return os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data/fim_agent.db")


async def init_db() -> None:
    """Create the async engine and run ``CREATE TABLE`` for all models."""
    global _engine, _session_factory

    # Import all models so Base.metadata is fully populated before create_all.
    import fim_agent.web.models  # noqa: F401

    url = _get_database_url()
    logger.info("Initializing database: %s", url.split("@")[-1] if "@" in url else url)

    connect_args: dict = {}
    kwargs: dict = {}
    is_sqlite = url.startswith("sqlite")

    if is_sqlite:
        connect_args["check_same_thread"] = False
        # Give SQLite 30 seconds to wait for a lock instead of the default 5.
        connect_args["timeout"] = 30
        # Use StaticPool so that all async tasks share the same underlying
        # SQLite connection.  This avoids multi-connection write contention
        # that causes "database is locked" even with WAL mode enabled.
        kwargs["poolclass"] = StaticPool
        # Ensure the data directory exists for SQLite file-based databases.
        db_path = url.split("///", 1)[-1] if "///" in url else None
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    _engine = create_async_engine(url, connect_args=connect_args, echo=False, **kwargs)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    # -- SQLite-specific PRAGMAs -------------------------------------------
    # Enable WAL mode so readers don't block writers and vice-versa.  Also
    # turn on normal synchronous mode (safe with WAL) for better throughput.
    # The listener is registered before any connection is opened (the engine
    # is lazy), so every connection — including the one used by create_all
    # below — will have these pragmas applied.
    if is_sqlite:

        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, connection_record):  # noqa: ARG001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if is_sqlite:
            await _migrate_user_password_nullable(conn)
            await _migrate_user_oauth_columns(conn)
            await _migrate_oauth_bindings(conn)
            await _migrate_agent_columns(conn)
            await _migrate_user_is_active(conn)
            await _migrate_user_email_required(conn)
            await _migrate_mcp_server_columns(conn)

    logger.info("Database initialized successfully")


async def _migrate_user_password_nullable(conn) -> None:
    """Make users.password_hash nullable for OAuth users who have no password.

    SQLite does not support ``ALTER COLUMN``, so we check whether the column is
    already nullable.  If not, we recreate the table with the corrected schema
    while preserving all existing data.
    """
    result = await conn.execute(text("PRAGMA table_info(users)"))
    columns = result.fetchall()

    # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk)
    for col in columns:
        if col[1] == "password_hash":
            notnull = col[3]
            if not notnull:
                return  # Already nullable, nothing to do
            break
    else:
        return  # Column doesn't exist yet (will be created by create_all)

    logger.info("Migrating users.password_hash to nullable (recreating table)")

    # Gather current column definitions from PRAGMA
    col_names = [c[1] for c in columns]
    col_list = ", ".join(col_names)

    # Build CREATE TABLE statement with the same columns but password_hash nullable
    col_defs = []
    for c in columns:
        cid, name, ctype, notnull, dflt, pk = c
        parts = [name, ctype or "TEXT"]
        if pk:
            parts.append("PRIMARY KEY")
        if notnull and name != "password_hash":
            parts.append("NOT NULL")
        if dflt is not None:
            parts.append(f"DEFAULT {dflt}")
        col_defs.append(" ".join(parts))

    create_sql = f"CREATE TABLE users_new ({', '.join(col_defs)})"

    await conn.execute(text(create_sql))
    await conn.execute(text(f"INSERT INTO users_new ({col_list}) SELECT {col_list} FROM users"))
    await conn.execute(text("DROP TABLE users"))
    await conn.execute(text("ALTER TABLE users_new RENAME TO users"))

    # Recreate indexes that were dropped with the old table
    await conn.execute(
        text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users(username)")
    )
    await conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_oauth "
            "ON users(oauth_provider, oauth_id)"
        )
    )
    logger.info("users.password_hash is now nullable")


async def _migrate_user_oauth_columns(conn) -> None:
    """Add OAuth-related columns to the users table if they don't exist.

    SQLite does not support ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``, so we
    inspect the table via ``PRAGMA table_info`` first.
    """
    result = await conn.execute(text("PRAGMA table_info(users)"))
    existing_columns = {row[1] for row in result.fetchall()}

    migrations = [
        ("oauth_provider", "ALTER TABLE users ADD COLUMN oauth_provider VARCHAR(20)"),
        ("oauth_id", "ALTER TABLE users ADD COLUMN oauth_id VARCHAR(255)"),
        ("email", "ALTER TABLE users ADD COLUMN email VARCHAR(255)"),
    ]

    for col_name, ddl in migrations:
        if col_name not in existing_columns:
            logger.info("Adding column users.%s", col_name)
            await conn.execute(text(ddl))

    # Create the unique index for the (oauth_provider, oauth_id) pair.
    await conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_oauth "
            "ON users(oauth_provider, oauth_id)"
        )
    )


async def _migrate_oauth_bindings(conn) -> None:
    """Migrate existing User.oauth_provider/oauth_id data into the bindings table.

    Runs once: if the user_oauth_bindings table is empty AND there are users
    with oauth_provider set, copy those rows into UserOAuthBinding records.
    """
    import uuid as _uuid

    result = await conn.execute(text("SELECT COUNT(*) FROM user_oauth_bindings"))
    count = result.scalar()
    if count and count > 0:
        return  # already migrated

    result = await conn.execute(
        text(
            "SELECT id, oauth_provider, oauth_id, email, display_name "
            "FROM users WHERE oauth_provider IS NOT NULL AND oauth_id IS NOT NULL"
        )
    )
    rows = result.fetchall()
    if not rows:
        return

    logger.info("Migrating %d existing OAuth users into user_oauth_bindings", len(rows))
    for row in rows:
        user_id, provider, oauth_id, email, display_name = row
        binding_id = str(_uuid.uuid4())
        await conn.execute(
            text(
                "INSERT INTO user_oauth_bindings (id, user_id, provider, oauth_id, email, display_name) "
                "VALUES (:id, :user_id, :provider, :oauth_id, :email, :display_name)"
            ),
            {
                "id": binding_id,
                "user_id": user_id,
                "provider": provider,
                "oauth_id": oauth_id,
                "email": email,
                "display_name": display_name,
            },
        )
    logger.info("OAuth bindings migration complete")


async def _migrate_agent_columns(conn) -> None:
    """Add new columns to the agents table if they don't exist.

    Covers columns added after the initial table creation: ``kb_ids``,
    ``connector_ids``, and ``grounding_config`` (all JSON, nullable).
    """
    result = await conn.execute(text("PRAGMA table_info(agents)"))
    existing_columns = {row[1] for row in result.fetchall()}

    migrations = [
        ("kb_ids", "ALTER TABLE agents ADD COLUMN kb_ids JSON"),
        ("connector_ids", "ALTER TABLE agents ADD COLUMN connector_ids JSON"),
        ("grounding_config", "ALTER TABLE agents ADD COLUMN grounding_config JSON"),
        ("icon", "ALTER TABLE agents ADD COLUMN icon VARCHAR(100)"),
    ]

    for col_name, ddl in migrations:
        if col_name not in existing_columns:
            logger.info("Adding column agents.%s", col_name)
            await conn.execute(text(ddl))


async def _migrate_user_is_active(conn) -> None:
    """Add is_active column to users table if it doesn't exist."""
    result = await conn.execute(text("PRAGMA table_info(users)"))
    existing_columns = {row[1] for row in result.fetchall()}
    if "is_active" not in existing_columns:
        logger.info("Adding column users.is_active")
        await conn.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"))


async def _migrate_mcp_server_columns(conn) -> None:
    """Add working_dir and headers columns to mcp_servers if they don't exist."""
    result = await conn.execute(text("PRAGMA table_info(mcp_servers)"))
    existing_columns = {row[1] for row in result.fetchall()}

    migrations = [
        ("working_dir", "ALTER TABLE mcp_servers ADD COLUMN working_dir VARCHAR(500)"),
        ("headers", "ALTER TABLE mcp_servers ADD COLUMN headers JSON"),
    ]

    for col_name, ddl in migrations:
        if col_name not in existing_columns:
            logger.info("Adding column mcp_servers.%s", col_name)
            await conn.execute(text(ddl))


async def _migrate_user_email_required(conn) -> None:
    """Backfill NULL emails with a placeholder so the column can be NOT NULL."""
    result = await conn.execute(
        text("SELECT id, username FROM users WHERE email IS NULL")
    )
    rows = result.fetchall()
    if not rows:
        return

    logger.info("Backfilling email for %d users with NULL email", len(rows))
    for row in rows:
        user_id, username = row
        placeholder = f"{username}@change.me"
        await conn.execute(
            text("UPDATE users SET email = :email WHERE id = :id"),
            {"email": placeholder, "id": user_id},
        )
    logger.info("Email backfill complete")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an ``AsyncSession`` — intended for use with FastAPI ``Depends``."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with _session_factory() as session:
        yield session


def create_session() -> AsyncSession:
    """Create an ``AsyncSession`` directly — caller must close it.

    Unlike :func:`get_session` (which is an async-generator suited for FastAPI
    ``Depends``), this returns a plain session object whose lifetime is managed
    by the caller.  Use this inside SSE async generators where breaking out of
    an ``async for`` would prematurely close the generator-managed session.
    """
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _session_factory()


async def shutdown_db() -> None:
    """Dispose of the engine and release all connections."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("Database engine disposed")
        _engine = None
        _session_factory = None
