"""Workflow trigger keys are stored hashed and sent in a header (T10 P1, S8).

- Generating a key stores only its SHA-256 hash and a display prefix.
- ``POST /api/workflows/trigger`` with ``Authorization: Bearer <key>`` runs
  the workflow; the old ``POST /api/workflows/trigger/{key}`` still works for
  one release and answers with ``Deprecation`` and ``Link`` headers.
- A key whose author account is disabled gets 401.
- The stored hash is compared with ``hmac.compare_digest``.
- Migration ``t10p1b2c3d4e`` hashes existing plain-text keys and drops the
  column, idempotently, on SQLite and (when ``FIM_TEST_PG_URL`` is set) on
  PostgreSQL.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db import get_session
from fim_one.db.base import Base
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow
from fim_one.web.api import workflows as wf_api
from fim_one.web.schemas.common import ApiResponse
from fim_one.web.services import workflow_trigger_keys as keys
from fim_one.web.services.feature_flags import require_workflows_enabled

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    import fim_one.db.models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _owner_and_workflow(
    factory: async_sessionmaker[AsyncSession], *, owner_active: bool = True
) -> tuple[User, Workflow]:
    async with factory() as s:
        owner = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.io")
        owner.is_active = owner_active
        s.add(owner)
        await s.flush()
        wf = Workflow(user_id=owner.id, name="hooked", blueprint={"nodes": [], "edges": []})
        s.add(wf)
        await s.commit()
        return owner, wf


async def _generate(
    factory: async_sessionmaker[AsyncSession], owner: User, wf: Workflow
) -> str:
    async with factory() as s:
        resp = await wf_api.generate_workflow_api_key(wf.id, current_user=owner, db=s)
    return str(resp.data["api_key"])


@pytest.fixture()
async def client(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    """The workflows router on a bare app; the run itself is stubbed out."""
    app = FastAPI()
    app.include_router(wf_api.router)

    async def _session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    async def _enabled() -> None:
        return None

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[require_workflows_enabled] = _enabled

    async def _fake_execute(wf: Workflow, body: Any, db: Any) -> ApiResponse:
        return ApiResponse(data={"ran": wf.id})

    with patch.object(wf_api, "_execute_trigger", _fake_execute):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class TestKeyStorage:
    @pytest.mark.asyncio
    async def test_no_plain_text_key_in_database(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        owner, wf = await _owner_and_workflow(factory)
        raw = await _generate(factory, owner, wf)
        assert raw.startswith("wf_")

        async with factory() as s:
            row = (
                await s.execute(sa.text("SELECT * FROM workflows WHERE id = :id"), {"id": wf.id})
            ).mappings().one()
        assert raw not in {str(v) for v in row.values()}
        assert "api_key" not in row
        assert row["api_key_hash"] == hashlib.sha256(raw.encode()).hexdigest()
        assert row["api_key_prefix"] == raw[: keys.DISPLAY_PREFIX_LEN]

    @pytest.mark.asyncio
    async def test_owner_response_shows_prefix_only(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        owner, wf = await _owner_and_workflow(factory)
        raw = await _generate(factory, owner, wf)
        async with factory() as s:
            resp = await wf_api.get_workflow(wf.id, current_user=owner, db=s)
        assert resp.data["has_api_key"] is True
        assert resp.data["api_key_prefix"] == raw[: keys.DISPLAY_PREFIX_LEN]
        assert raw not in repr(resp.data)

    @pytest.mark.asyncio
    async def test_revoke_clears_hash(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        owner, wf = await _owner_and_workflow(factory)
        await _generate(factory, owner, wf)
        async with factory() as s:
            await wf_api.revoke_workflow_api_key(wf.id, current_user=owner, db=s)
        async with factory() as s:
            fresh = await s.get(Workflow, wf.id)
        assert fresh is not None
        assert fresh.api_key_hash is None and fresh.api_key_prefix is None


# ---------------------------------------------------------------------------
# Trigger routes
# ---------------------------------------------------------------------------


class TestTriggerRoutes:
    @pytest.mark.asyncio
    async def test_header_route_triggers(
        self, factory: async_sessionmaker[AsyncSession], client: httpx.AsyncClient
    ) -> None:
        owner, wf = await _owner_and_workflow(factory)
        raw = await _generate(factory, owner, wf)
        resp = await client.post(
            "/api/workflows/trigger",
            json={"inputs": {}},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == {"ran": wf.id}
        assert "deprecation" not in resp.headers

    @pytest.mark.asyncio
    async def test_legacy_path_triggers_with_deprecation_headers(
        self, factory: async_sessionmaker[AsyncSession], client: httpx.AsyncClient
    ) -> None:
        owner, wf = await _owner_and_workflow(factory)
        raw = await _generate(factory, owner, wf)
        resp = await client.post(f"/api/workflows/trigger/{raw}", json={"inputs": {}})
        assert resp.status_code == 200
        assert resp.json()["data"] == {"ran": wf.id}
        assert resp.headers["deprecation"] == "true"
        assert 'rel="successor-version"' in resp.headers["link"]
        assert "/api/workflows/trigger" in resp.headers["link"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Authorization": "Bearer wf_not-a-real-key"},
            {"Authorization": "Basic d2ZfeDp5"},
        ],
    )
    async def test_missing_or_wrong_key_is_401(
        self,
        factory: async_sessionmaker[AsyncSession],
        client: httpx.AsyncClient,
        headers: dict[str, str],
    ) -> None:
        owner, wf = await _owner_and_workflow(factory)
        await _generate(factory, owner, wf)
        resp = await client.post("/api/workflows/trigger", json={"inputs": {}}, headers=headers)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_revoked_key_is_401_on_both_routes(
        self, factory: async_sessionmaker[AsyncSession], client: httpx.AsyncClient
    ) -> None:
        owner, wf = await _owner_and_workflow(factory)
        raw = await _generate(factory, owner, wf)
        async with factory() as s:
            await wf_api.revoke_workflow_api_key(wf.id, current_user=owner, db=s)
        r1 = await client.post(
            "/api/workflows/trigger",
            json={"inputs": {}},
            headers={"Authorization": f"Bearer {raw}"},
        )
        r2 = await client.post(f"/api/workflows/trigger/{raw}", json={"inputs": {}})
        assert (r1.status_code, r2.status_code) == (401, 401)

    @pytest.mark.asyncio
    async def test_disabled_author_is_401(
        self, factory: async_sessionmaker[AsyncSession], client: httpx.AsyncClient
    ) -> None:
        owner, wf = await _owner_and_workflow(factory)
        raw = await _generate(factory, owner, wf)
        async with factory() as s:
            row = await s.get(User, owner.id)
            assert row is not None
            row.is_active = False
            await s.commit()
        r1 = await client.post(
            "/api/workflows/trigger",
            json={"inputs": {}},
            headers={"Authorization": f"Bearer {raw}"},
        )
        r2 = await client.post(f"/api/workflows/trigger/{raw}", json={"inputs": {}})
        assert (r1.status_code, r2.status_code) == (401, 401)


class TestConstantTimeComparison:
    @pytest.mark.asyncio
    async def test_hash_compared_with_compare_digest(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        owner, wf = await _owner_and_workflow(factory)
        raw = await _generate(factory, owner, wf)
        import hmac

        with patch(
            "fim_one.web.services.workflow_trigger_keys.hmac.compare_digest",
            wraps=hmac.compare_digest,
        ) as cmp:
            async with factory() as s:
                found = await keys.resolve_trigger_key(raw, s)
        assert found.id == wf.id
        cmp.assert_called_once_with(found.api_key_hash, keys.hash_trigger_key(raw))


class TestParseBearer:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("Bearer wf_abc", "wf_abc"),
            ("bearer   wf_abc  ", "wf_abc"),
            ("Basic wf_abc", None),
            ("Bearer", None),
            (None, None),
            ("", None),
        ],
    )
    def test_parse(self, header: str | None, expected: str | None) -> None:
        assert keys.parse_bearer(header) == expected


# ---------------------------------------------------------------------------
# Migration (SQLite always; PostgreSQL when FIM_TEST_PG_URL is set)
# ---------------------------------------------------------------------------

MIGRATION = "fim_one.migrations.versions.t10p1b2c3d4e_hash_workflow_trigger_keys"


def _engines() -> list[Any]:
    params: list[Any] = [pytest.param("sqlite:///:memory:", id="sqlite")]
    pg_url = os.environ.get("FIM_TEST_PG_URL")
    params.append(
        pytest.param(
            pg_url or "",
            id="postgresql",
            marks=pytest.mark.skipif(not pg_url, reason="FIM_TEST_PG_URL not set"),
        )
    )
    return params


@pytest.fixture(params=_engines())
def legacy_engine(request: pytest.FixtureRequest) -> Any:
    engine = sa.create_engine(request.param, future=True)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS workflows"))
        conn.execute(
            sa.text(
                "CREATE TABLE workflows ("
                " id VARCHAR(36) PRIMARY KEY,"
                " name VARCHAR(200) NOT NULL,"
                " api_key VARCHAR(64)"
                ")"
            )
        )
        conn.execute(sa.text("CREATE UNIQUE INDEX ix_workflows_api_key ON workflows (api_key)"))
        conn.execute(
            sa.text(
                "INSERT INTO workflows (id, name, api_key) VALUES "
                "('w1', 'one', 'wf_legacyKeyNumberOne0000000000000000000000000'),"
                "('w2', 'two', NULL)"
            )
        )
    yield engine
    with engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS workflows"))
    engine.dispose()


def _run(engine: Any, fn: str) -> None:
    module = importlib.import_module(MIGRATION)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(module, fn)()
        conn.commit()


class TestMigration:
    def test_upgrade_hashes_and_drops_plain_text(self, legacy_engine: Any) -> None:
        _run(legacy_engine, "upgrade")
        cols = {c["name"] for c in sa.inspect(legacy_engine).get_columns("workflows")}
        assert "api_key" not in cols
        assert {"api_key_hash", "api_key_prefix"} <= cols
        with legacy_engine.connect() as conn:
            rows = dict(
                conn.execute(
                    sa.text("SELECT id, api_key_hash FROM workflows ORDER BY id")
                ).all()
            )
            prefix = conn.execute(
                sa.text("SELECT api_key_prefix FROM workflows WHERE id = 'w1'")
            ).scalar_one()
        raw = "wf_legacyKeyNumberOne0000000000000000000000000"
        assert rows == {"w1": hashlib.sha256(raw.encode()).hexdigest(), "w2": None}
        assert prefix == raw[:10]

    def test_upgrade_is_idempotent(self, legacy_engine: Any) -> None:
        _run(legacy_engine, "upgrade")
        _run(legacy_engine, "upgrade")
        with legacy_engine.connect() as conn:
            count = conn.execute(
                sa.text("SELECT COUNT(*) FROM workflows WHERE api_key_hash IS NOT NULL")
            ).scalar_one()
        assert count == 1

    def test_downgrade_restores_empty_column(self, legacy_engine: Any) -> None:
        _run(legacy_engine, "upgrade")
        _run(legacy_engine, "downgrade")
        cols = {c["name"] for c in sa.inspect(legacy_engine).get_columns("workflows")}
        assert "api_key" in cols
        assert "api_key_hash" not in cols and "api_key_prefix" not in cols
