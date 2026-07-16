"""Knowledge bases are not shareable (Reduce Feature).

The KB publish / resubmit / unpublish endpoints are gone, all previously
org/market-published KBs were destructively recalled to personal
(migration ``f7h9j1l3n567``), and every sharing surface rejects the
``knowledge_base`` resource type:

- the Market cannot list or subscribe KBs,
- the admin Market surface cannot list, publish, or unpublish KBs,
- browse never returns a KB even if a legacy shared row somehow exists.

The only path by which a KB reaches another user is agent-owner
delegation at chat time (see test_kb_binding_visibility.py and
test_kb_owner_delegation.py).
"""

from __future__ import annotations

import importlib
import uuid
from typing import AsyncIterator
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fim_one.db.base import Base
from fim_one.db.models.knowledge_base import KnowledgeBase
from fim_one.db.models.user import User
from fim_one.web.api import admin_market, market
from fim_one.web.api.knowledge_bases import router as kb_router
from fim_one.web.exceptions import AppError
from fim_one.web.platform import MARKET_ORG_ID

RECALL_MIGRATION_MODULE = (
    "fim_one.migrations.versions.f7h9j1l3n567_recall_shared_knowledge_bases"
)


# ---------------------------------------------------------------------------
# Constants — no sharing surface knows the knowledge_base type
# ---------------------------------------------------------------------------


class TestKbAbsentFromSharingSurfaces:
    def test_market_resource_types(self) -> None:
        assert "knowledge_base" not in market._ALL_RESOURCE_TYPES
        assert "knowledge_base" not in market.MARKET_RESOURCE_TYPES
        assert "knowledge_base" not in market.SOLUTION_TYPES
        assert "knowledge_base" not in market.COMPONENT_TYPES

    def test_market_subscribe_models(self) -> None:
        assert "knowledge_base" not in market._RESOURCE_MODELS

    def test_admin_market_models_and_publishable(self) -> None:
        assert "knowledge_base" not in admin_market._RESOURCE_MODELS
        assert "knowledge_base" not in admin_market._PUBLISHABLE_TYPES

    def test_kb_router_has_no_publish_routes(self) -> None:
        paths = [getattr(route, "path", "") for route in kb_router.routes]
        for path in paths:
            assert not path.endswith("/unpublish")
            assert not path.endswith("/publish")
            assert not path.endswith("/resubmit")


# ---------------------------------------------------------------------------
# Behavior — subscribe / admin publish paths reject knowledge_base
# ---------------------------------------------------------------------------


def _mock_user(*, is_admin: bool = False) -> User:
    user = User(
        id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.io", is_admin=is_admin
    )
    return user


class TestKbSharingRejected:
    @pytest.mark.asyncio
    async def test_market_subscribe_kb_rejected(self) -> None:
        body = market.SubscribeRequest(
            resource_type="knowledge_base", resource_id="kb-1"
        )
        with pytest.raises(AppError) as exc:
            await market.subscribe_resource(
                body, current_user=_mock_user(), db=AsyncMock()
            )
        assert exc.value.error_code == "invalid_resource_type"

    @pytest.mark.asyncio
    async def test_admin_market_lookup_kb_rejected(self) -> None:
        with pytest.raises(AppError) as exc:
            await admin_market._get_market_resource(
                AsyncMock(), "knowledge_base", "kb-1"
            )
        assert exc.value.error_code == "invalid_resource_type"

    @pytest.mark.asyncio
    async def test_admin_market_unpublish_kb_rejected(self) -> None:
        with pytest.raises(AppError) as exc:
            await admin_market.unpublish_market_resource(
                "knowledge_base",
                "kb-1",
                db=AsyncMock(),
                admin=_mock_user(is_admin=True),
            )
        assert exc.value.error_code == "unpublish_not_supported"


# ---------------------------------------------------------------------------
# Behavior — browse never returns a KB, even a legacy shared row
# ---------------------------------------------------------------------------


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    import fim_one.db.models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


class TestBrowseNeverReturnsKb:
    @pytest.mark.asyncio
    async def test_legacy_shared_kb_invisible_in_market(
        self, session: AsyncSession
    ) -> None:
        owner = _mock_user()
        browser = _mock_user()
        session.add(owner)
        session.add(browser)
        # A legacy shared row that predates the recall migration.
        session.add(
            KnowledgeBase(
                id="kb-legacy",
                user_id=owner.id,
                name="legacy shared kb",
                visibility="org",
                org_id=MARKET_ORG_ID,
                status="active",
            )
        )
        await session.commit()

        result = await market.browse_market(
            resource_type="knowledge_base",
            scope="market",
            category=None,
            page=1,
            size=20,
            current_user=browser,
            db=session,
        )
        data = result.data
        assert data is not None
        assert data["total"] == 0
        assert data["items"] == []


# ---------------------------------------------------------------------------
# Migration — destructive recall semantics
# ---------------------------------------------------------------------------


def _bootstrap_recall_schema(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE knowledge_bases ("
                "  id VARCHAR(36) PRIMARY KEY,"
                "  user_id VARCHAR(36),"
                "  name VARCHAR(200) NOT NULL,"
                "  visibility VARCHAR(20) NOT NULL DEFAULT 'personal',"
                "  org_id VARCHAR(36),"
                "  publish_status VARCHAR(20),"
                "  reviewed_by VARCHAR(36),"
                "  reviewed_at TIMESTAMP,"
                "  review_note TEXT"
                ")"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TABLE resource_subscriptions ("
                "  id VARCHAR(36) PRIMARY KEY,"
                "  user_id VARCHAR(36) NOT NULL,"
                "  resource_type VARCHAR(50) NOT NULL,"
                "  resource_id VARCHAR(36) NOT NULL,"
                "  org_id VARCHAR(36)"
                ")"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO knowledge_bases "
                "(id, user_id, name, visibility, org_id, publish_status, reviewed_by, review_note) VALUES "
                "('kb-org', 'u1', 'shared', 'org', 'org-1', 'approved', 'admin-1', 'ok'),"
                "('kb-personal', 'u2', 'private', 'personal', NULL, NULL, NULL, NULL)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO resource_subscriptions "
                "(id, user_id, resource_type, resource_id, org_id) VALUES "
                "('sub-kb', 'u3', 'knowledge_base', 'kb-org', 'org-1'),"
                "('sub-agent', 'u3', 'agent', 'agent-1', 'org-1')"
            )
        )


def _run_upgrade(engine: sa.Engine, module_name: str) -> None:
    module = importlib.import_module(module_name)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            module.upgrade()
        conn.commit()


class TestRecallMigration:
    def test_recalls_shared_kbs_and_drops_kb_subscriptions(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        _bootstrap_recall_schema(engine)
        _run_upgrade(engine, RECALL_MIGRATION_MODULE)

        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT visibility, org_id, publish_status, reviewed_by, "
                    "reviewed_at, review_note FROM knowledge_bases "
                    "WHERE id='kb-org'"
                )
            ).first()
            assert row is not None
            assert row[0] == "personal"
            assert all(v is None for v in row[1:])

            # Personal KB untouched.
            personal = conn.execute(
                sa.text(
                    "SELECT visibility FROM knowledge_bases WHERE id='kb-personal'"
                )
            ).scalar()
            assert personal == "personal"

            # KB subscriptions deleted; other types kept.
            subs = conn.execute(
                sa.text("SELECT resource_type FROM resource_subscriptions")
            ).scalars().all()
            assert subs == ["agent"]

    def test_idempotent_replay(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        _bootstrap_recall_schema(engine)
        _run_upgrade(engine, RECALL_MIGRATION_MODULE)
        _run_upgrade(engine, RECALL_MIGRATION_MODULE)

        with engine.connect() as conn:
            count = conn.execute(
                sa.text("SELECT COUNT(*) FROM knowledge_bases")
            ).scalar()
            assert count == 2
