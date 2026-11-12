"""Conversations carry the host system's business reference (T08 E4).

A system that embeds FIM One (a contract register, a ticket desk) tags each
conversation with the id of the business object it is about. The owner can
set it when creating the conversation, change or clear it, list their
conversations for one reference, and ask for the most recent one to resume
it. The reference is opaque text, scoped to its owner, and leaves nothing
behind when the account is deleted.

The migration ``e4b1a2c3d4e5`` is run up, again, down and up on SQLite and
(with ``FIM_TEST_PG_URL`` pointing at an empty throwaway database) on
PostgreSQL.
"""

from __future__ import annotations

import importlib
import logging
import os
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import make_url, select, text

import fim_one
import fim_one.db.models  # register all models with metadata
from fim_one.db import create_session, init_db, shutdown_db
from fim_one.db.base import Base
from fim_one.db.models import Agent, Conversation, Message, User
from fim_one.web import create_app
from fim_one.web.auth import create_access_token
from fim_one.web.services.user_deletion import purge_user_data

REF = "HT-2026-0042"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def db_env() -> AsyncIterator[None]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    prev_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"
    await init_db()

    from fim_one.db import engine as engine_mod

    assert engine_mod._engine is not None
    async with engine_mod._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield
    finally:
        await shutdown_db()
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        os.unlink(path)


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return create_app()


@pytest_asyncio.fixture()
async def client(app: FastAPI, db_env: None) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api.test") as c:
        yield c


async def _user(*, is_admin: bool = False) -> tuple[User, dict[str, str]]:
    session = create_session()
    try:
        user = User(
            id=str(uuid.uuid4()),
            email=f"{uuid.uuid4().hex[:8]}@example.com",
            username=f"u_{uuid.uuid4().hex[:8]}",
            password_hash="x",
            is_active=True,
            is_admin=is_admin,
        )
        session.add(user)
        await session.commit()
    finally:
        await session.close()
    token = create_access_token(user.id, user.email or "")
    return user, {"Authorization": f"Bearer {token}"}


async def _create(client: AsyncClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    resp = await client.post("/api/conversations", headers=headers, json={"mode": "react", **body})
    assert resp.status_code == 200, resp.text
    data: dict[str, Any] = resp.json()["data"]
    return data


async def _list(client: AsyncClient, headers: dict[str, str], **params: Any) -> dict[str, Any]:
    resp = await client.get("/api/conversations", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    data: dict[str, Any] = resp.json()
    return data


async def _latest(client: AsyncClient, headers: dict[str, str], ref: str) -> Any:
    resp = await client.get(
        "/api/conversations/latest", headers=headers, params={"business_ref": ref}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _add_message(conversation_id: str, at: datetime) -> None:
    session = create_session()
    try:
        session.add(
            Message(
                conversation_id=conversation_id,
                role="user",
                content="hello",
                message_type="text",
                created_at=at,
            )
        )
        await session.commit()
    finally:
        await session.close()


async def _set_created(conversation_id: str, at: datetime) -> None:
    session = create_session()
    try:
        await session.execute(
            sa.update(Conversation).where(Conversation.id == conversation_id).values(created_at=at)
        )
        await session.commit()
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Create, read, filter
# ---------------------------------------------------------------------------


class TestCreateAndFilter:
    async def test_create_with_ref_returns_and_stores_it(self, client: AsyncClient) -> None:
        _, h = await _user()
        conv = await _create(client, h, title="contract", business_ref=REF)
        assert conv["business_ref"] == REF

        detail = (await client.get(f"/api/conversations/{conv['id']}", headers=h)).json()["data"]
        assert detail["business_ref"] == REF

    async def test_create_without_ref_is_null(self, client: AsyncClient) -> None:
        _, h = await _user()
        conv = await _create(client, h)
        assert conv["business_ref"] is None

    async def test_list_filters_by_exact_ref(self, client: AsyncClient) -> None:
        _, h = await _user()
        a = await _create(client, h, title="a", business_ref=REF)
        b = await _create(client, h, title="b", business_ref=REF)
        await _create(client, h, title="other", business_ref=REF + "-1")
        await _create(client, h, title="lower", business_ref=REF.lower())
        await _create(client, h, title="none")

        page = await _list(client, h, business_ref=REF)
        assert {c["id"] for c in page["items"]} == {a["id"], b["id"]}
        assert page["total"] == 2
        # Without the filter every conversation is listed, as before.
        assert (await _list(client, h))["total"] == 5

    async def test_filter_trims_and_blank_means_no_filter(self, client: AsyncClient) -> None:
        _, h = await _user()
        await _create(client, h, business_ref=REF)
        await _create(client, h)
        assert (await _list(client, h, business_ref=f"  {REF} "))["total"] == 1
        assert (await _list(client, h, business_ref="   "))["total"] == 2

    async def test_filter_composes_with_search_status_and_pages(
        self, client: AsyncClient
    ) -> None:
        _, h = await _user()
        ids = [
            (await _create(client, h, title=f"review {n}", business_ref=REF))["id"]
            for n in range(3)
        ]
        await _create(client, h, title="review elsewhere", business_ref="X-1")
        await _create(client, h, title="unrelated", business_ref=REF)
        await client.patch(f"/api/conversations/{ids[0]}", headers=h, json={"status": "archived"})

        page = await _list(client, h, business_ref=REF, q="review", size=1, page=2)
        assert page["total"] == 2
        assert page["pages"] == 2
        assert len(page["items"]) == 1
        assert page["items"][0]["id"] in ids[1:]

        archived = await _list(client, h, business_ref=REF, status="archived")
        assert [c["id"] for c in archived["items"]] == [ids[0]]


# ---------------------------------------------------------------------------
# Latest for a reference
# ---------------------------------------------------------------------------


class TestLatest:
    async def test_none_yet_is_null(self, client: AsyncClient) -> None:
        _, h = await _user()
        await _create(client, h, business_ref="other")
        assert await _latest(client, h, REF) is None

    async def test_latest_activity_wins_not_creation_or_star(self, client: AsyncClient) -> None:
        _, h = await _user()
        now = datetime.now(UTC)
        old = await _create(client, h, title="old", business_ref=REF)
        new = await _create(client, h, title="new", business_ref=REF)
        await _set_created(old["id"], now - timedelta(days=3))
        await _set_created(new["id"], now - timedelta(days=1))

        # Newest by creation while no messages exist.
        assert (await _latest(client, h, REF))["id"] == new["id"]

        # A message in the older one makes it the most recently active ...
        await _add_message(old["id"], now - timedelta(hours=1))
        assert (await _latest(client, h, REF))["id"] == old["id"]

        # ... and a star on the other changes nothing (the list puts it first).
        await client.patch(f"/api/conversations/{new['id']}", headers=h, json={"starred": True})
        assert (await _latest(client, h, REF))["id"] == old["id"]
        assert (await _list(client, h, business_ref=REF))["items"][0]["id"] == new["id"]

    async def test_archived_is_not_resumed_by_default(self, client: AsyncClient) -> None:
        _, h = await _user()
        conv = await _create(client, h, business_ref=REF)
        await client.patch(
            f"/api/conversations/{conv['id']}", headers=h, json={"status": "archived"}
        )
        assert await _latest(client, h, REF) is None
        resp = await client.get(
            "/api/conversations/latest",
            headers=h,
            params={"business_ref": REF, "status": "archived"},
        )
        assert resp.json()["data"]["id"] == conv["id"]

    async def test_builder_conversations_are_left_out(self, client: AsyncClient) -> None:
        user, h = await _user()
        session = create_session()
        try:
            builder = Agent(user_id=user.id, name="builder", status="draft", is_builder=True)
            session.add(builder)
            await session.commit()
            builder_id = builder.id
        finally:
            await session.close()
        await _create(client, h, business_ref=REF, agent_id=builder_id)
        assert await _latest(client, h, REF) is None

    async def test_ref_is_required(self, client: AsyncClient) -> None:
        _, h = await _user()
        for params in ({}, {"business_ref": "  "}):
            resp = await client.get("/api/conversations/latest", headers=h, params=params)
            assert resp.status_code == 422, params


# ---------------------------------------------------------------------------
# Update and clear
# ---------------------------------------------------------------------------


class TestUpdate:
    async def _patch(
        self, client: AsyncClient, h: dict[str, str], cid: str, body: dict[str, Any]
    ) -> Any:
        resp = await client.patch(f"/api/conversations/{cid}", headers=h, json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["business_ref"]

    async def test_set_change_and_clear(self, client: AsyncClient) -> None:
        _, h = await _user()
        cid = (await _create(client, h))["id"]
        assert await self._patch(client, h, cid, {"business_ref": REF}) == REF
        assert await self._patch(client, h, cid, {"business_ref": " HT-2 "}) == "HT-2"
        # Another field alone leaves the reference as it is.
        assert await self._patch(client, h, cid, {"title": "renamed"}) == "HT-2"
        assert await self._patch(client, h, cid, {"business_ref": None}) is None
        await self._patch(client, h, cid, {"business_ref": REF})
        assert await self._patch(client, h, cid, {"business_ref": ""}) is None
        assert (await _list(client, h, business_ref=REF))["total"] == 0

    async def test_another_user_cannot_change_it(self, client: AsyncClient) -> None:
        _, alice = await _user()
        _, bob = await _user()
        cid = (await _create(client, alice, business_ref=REF))["id"]
        resp = await client.patch(
            f"/api/conversations/{cid}", headers=bob, json={"business_ref": "stolen"}
        )
        assert resp.status_code == 404
        assert (await _latest(client, alice, REF))["id"] == cid


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    async def test_blank_is_stored_as_null(self, client: AsyncClient) -> None:
        _, h = await _user()
        assert (await _create(client, h, business_ref="   "))["business_ref"] is None

    async def test_value_is_trimmed_and_otherwise_kept(self, client: AsyncClient) -> None:
        _, h = await _user()
        raw = "  合同/2026 #7 'a' OR 1=1; %_  "
        assert (await _create(client, h, business_ref=raw))["business_ref"] == raw.strip()
        assert (await _latest(client, h, raw.strip())) is not None
        # LIKE wildcards are not interpreted: "%" does not match everything.
        assert await _latest(client, h, "%") is None

    async def test_max_length(self, client: AsyncClient) -> None:
        _, h = await _user()
        assert (await _create(client, h, business_ref="x" * 200))["business_ref"] == "x" * 200
        for bad in ("x" * 201, "a\nb", "a\x00b", 42):
            resp = await client.post(
                "/api/conversations", headers=h, json={"mode": "react", "business_ref": bad}
            )
            assert resp.status_code == 422, bad
        cid = (await _create(client, h))["id"]
        resp = await client.patch(
            f"/api/conversations/{cid}", headers=h, json={"business_ref": "x" * 201}
        )
        assert resp.status_code == 422

    async def test_query_parameter_is_checked_too(self, client: AsyncClient) -> None:
        _, h = await _user()
        for path in ("/api/conversations", "/api/conversations/latest"):
            resp = await client.get(path, headers=h, params={"business_ref": "x" * 201})
            assert resp.status_code == 422, path
            assert resp.json()["error_code"] == "invalid_business_ref"

    async def test_not_unique(self, client: AsyncClient) -> None:
        _, h = await _user()
        await _create(client, h, business_ref=REF)
        await _create(client, h, business_ref=REF)
        assert (await _list(client, h, business_ref=REF))["total"] == 2


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


class TestIsolation:
    async def test_one_users_ref_never_reaches_another(self, client: AsyncClient) -> None:
        _, alice = await _user()
        _, bob = await _user()
        _, admin = await _user(is_admin=True)
        mine = await _create(client, alice, business_ref=REF)

        for other in (bob, admin):
            assert (await _list(client, other, business_ref=REF))["total"] == 0
            assert await _latest(client, other, REF) is None
            resp = await client.get(f"/api/conversations/{mine['id']}", headers=other)
            assert resp.status_code == 404

        # Bob tagging his own conversation with the same ref sees only his.
        his = await _create(client, bob, business_ref=REF)
        assert (await _latest(client, bob, REF))["id"] == his["id"]
        assert (await _latest(client, alice, REF))["id"] == mine["id"]

    async def test_admin_views_do_not_show_the_ref(self, client: AsyncClient) -> None:
        _, alice = await _user()
        _, admin = await _user(is_admin=True)
        await _create(client, alice, title="t", business_ref=REF)
        for path in (
            "/api/admin/conversations",
            "/api/admin/export/conversations",
            "/api/admin/export/full-backup",
        ):
            resp = await client.get(path, headers=admin)
            assert resp.status_code == 200, (path, resp.text)
            assert REF not in resp.text, path

    async def test_own_data_export_includes_it(self, client: AsyncClient) -> None:
        _, alice = await _user()
        await _create(client, alice, business_ref=REF)
        resp = await client.get("/api/auth/me/export", headers=alice)
        assert resp.status_code == 200
        assert [c["business_ref"] for c in resp.json()["conversations"]] == [REF]


# ---------------------------------------------------------------------------
# Deletion and privacy
# ---------------------------------------------------------------------------


async def _rows_mentioning(needle: str) -> list[str]:
    """Every table and column whose stored text contains *needle*."""
    found: list[str] = []
    session = create_session()
    try:
        for table in Base.metadata.sorted_tables:
            rows = (await session.execute(select(table))).mappings().all()
            for row in rows:
                for column, value in row.items():
                    if value is not None and needle in str(value):
                        found.append(f"{table.name}.{column}")
    finally:
        await session.close()
    return found


class TestDeletion:
    async def test_account_purge_leaves_no_copy(
        self, client: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        alice, h = await _user()
        cid = (await _create(client, h, business_ref=REF))["id"]
        await client.patch(f"/api/conversations/{cid}", headers=h, json={"title": "x"})
        await _list(client, h, business_ref=REF)
        await _latest(client, h, REF)
        assert await _rows_mentioning(REF) == ["conversations.business_ref"]

        session = create_session()
        try:
            await purge_user_data(session, alice.id)
            await session.commit()
        finally:
            await session.close()

        assert await _rows_mentioning(REF) == []
        # Application logs never carried it. Left out: the database driver's
        # DEBUG statement log (parameters of every query) and the HTTP
        # server's access log (request lines, as for ``q``), both outside
        # the application.
        outside = ("httpx", "uvicorn", "aiosqlite", "asyncpg", "sqlalchemy")
        app_records = [r for r in caplog.records if not r.name.startswith(outside)]
        assert not any(REF in r.getMessage() for r in app_records)

    async def test_deleting_the_conversation_removes_it(self, client: AsyncClient) -> None:
        _, h = await _user()
        cid = (await _create(client, h, business_ref=REF))["id"]
        assert (await client.delete(f"/api/conversations/{cid}", headers=h)).status_code == 200
        assert await _rows_mentioning(REF) == []
        assert await _latest(client, h, REF) is None


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

_PG = os.environ.get("FIM_TEST_PG_URL")
_MIGRATION = "fim_one.migrations.versions.e4b1a2c3d4e5_conversation_business_ref"
_INDEX = "ix_conversations_user_id_business_ref"


def _engine_params() -> list[Any]:
    return [
        pytest.param("sqlite:///:memory:", id="sqlite"),
        pytest.param(
            _PG or "",
            id="postgresql",
            marks=pytest.mark.skipif(not _PG, reason="FIM_TEST_PG_URL not set"),
        ),
    ]


@pytest.fixture(params=_engine_params())
def legacy_db(request: pytest.FixtureRequest) -> Iterator[sa.Engine]:
    """``conversations`` as it stood before the migration, with one row."""
    url = make_url(request.param)
    if url.get_backend_name() == "postgresql":
        url = url.set(drivername="postgresql+psycopg2")
    engine = sa.create_engine(url, future=True)
    schema = None
    if engine.dialect.name == "postgresql":
        schema = f"e4_migration_{uuid.uuid4().hex[:12]}"
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = sa.create_engine(
            url, future=True, connect_args={"options": f"-csearch_path={schema}"}
        )
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE agents (id VARCHAR(36) PRIMARY KEY)"))
        conn.execute(
            text(
                """
                CREATE TABLE conversations (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36) NOT NULL REFERENCES users (id),
                    title VARCHAR(200),
                    mode VARCHAR(20) NOT NULL,
                    agent_id VARCHAR(36) REFERENCES agents (id) ON DELETE SET NULL,
                    status VARCHAR(20),
                    starred BOOLEAN NOT NULL DEFAULT FALSE,
                    model_name VARCHAR(100),
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    fast_llm_tokens INTEGER NOT NULL DEFAULT 0,
                    kb_ids JSON,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX ix_conversations_user_id ON conversations (user_id)"))
        conn.execute(text("INSERT INTO users (id) VALUES ('user-1')"))
        conn.execute(
            text(
                "INSERT INTO conversations (id, user_id, title, mode, status)"
                " VALUES ('old-1', 'user-1', 'kept', 'react', 'active')"
            )
        )
    yield engine
    if schema is not None:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


def _migrate(engine: sa.Engine, direction: str) -> None:
    module = importlib.import_module(_MIGRATION)
    with engine.connect() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(module, direction)()
        conn.commit()


def _shape(engine: sa.Engine) -> dict[str, Any]:
    inspector = sa.inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns("conversations")}
    return {
        "columns": set(columns),
        "nullable": {name for name, c in columns.items() if c["nullable"]},
        "foreign_keys": sorted(
            (tuple(fk["constrained_columns"]), fk["referred_table"])
            for fk in inspector.get_foreign_keys("conversations")
        ),
        "indexes": {
            i["name"]: tuple(i["column_names"]) for i in inspector.get_indexes("conversations")
        },
    }


def _old_row(engine: sa.Engine) -> list[tuple[Any, ...]]:
    with engine.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                text("SELECT id, user_id, title, mode, status FROM conversations")
            )
        ]


_OLD = [("old-1", "user-1", "kept", "react", "active")]


class TestMigration:
    def test_upgrade_adds_a_nullable_column_and_the_owner_index(
        self, legacy_db: sa.Engine
    ) -> None:
        before = _shape(legacy_db)
        _migrate(legacy_db, "upgrade")
        shape = _shape(legacy_db)

        assert shape["columns"] == before["columns"] | {"business_ref"}
        assert "business_ref" in shape["nullable"]
        assert shape["indexes"][_INDEX] == ("user_id", "business_ref")
        assert shape["foreign_keys"] == before["foreign_keys"]
        assert _old_row(legacy_db) == _OLD
        with legacy_db.connect() as conn:
            refs = conn.execute(text("SELECT business_ref FROM conversations")).scalars().all()
            assert list(refs) == [None]

    def test_upgrade_twice_changes_nothing_more(self, legacy_db: sa.Engine) -> None:
        _migrate(legacy_db, "upgrade")
        once = _shape(legacy_db)
        _migrate(legacy_db, "upgrade")
        assert _shape(legacy_db) == once

    def test_down_then_up_again(self, legacy_db: sa.Engine) -> None:
        before = _shape(legacy_db)
        _migrate(legacy_db, "upgrade")
        upgraded = _shape(legacy_db)

        _migrate(legacy_db, "downgrade")
        assert _shape(legacy_db) == before
        assert _old_row(legacy_db) == _OLD
        _migrate(legacy_db, "downgrade")  # nothing left to remove
        assert _shape(legacy_db) == before

        _migrate(legacy_db, "upgrade")
        assert _shape(legacy_db) == upgraded

    def test_a_long_ref_fits_after_upgrade(self, legacy_db: sa.Engine) -> None:
        _migrate(legacy_db, "upgrade")
        ref = "合" * 200
        with legacy_db.begin() as conn:
            conn.execute(
                text("UPDATE conversations SET business_ref = :ref WHERE id = 'old-1'"),
                {"ref": ref},
            )
        with legacy_db.connect() as conn:
            ids = conn.execute(
                text("SELECT id FROM conversations WHERE user_id = 'user-1' AND business_ref = :r"),
                {"r": ref},
            ).scalars().all()
            assert list(ids) == ["old-1"]


def test_the_model_and_the_migration_agree() -> None:
    table = Base.metadata.tables["conversations"]
    column = table.c["business_ref"]
    assert column.nullable
    assert isinstance(column.type, sa.String) and column.type.length == 200
    index = next(i for i in table.indexes if i.name == _INDEX)
    assert [c.name for c in index.columns] == ["user_id", "business_ref"]


def test_it_chains_after_c2p4_and_leaves_one_head() -> None:
    script = ScriptDirectory(str(Path(fim_one.__file__).parent / "migrations"))
    assert script.get_heads() == ["e4b1a2c3d4e5"]
    revision = script.get_revision("e4b1a2c3d4e5")
    assert revision is not None and revision.down_revision == "c2p4a1b2c3d4"
