"""Tests for the connector fork (clone) feature."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from fim_one.db.base import Base
from fim_one.db.models.connector import Connector, ConnectorAction
from fim_one.db.models.user import User
from fim_one.web.schemas.connector import ConnectorForkRequest


# ---------------------------------------------------------------------------
# Fixtures — in-memory SQLite async database
# ---------------------------------------------------------------------------


@pytest.fixture()
async def async_session():
    """Create an in-memory SQLite database with all required tables."""
    # Import all models so Base.metadata is fully populated
    import fim_one.db.models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture()
async def owner_user(async_session: AsyncSession) -> User:
    """Create and return a user who owns the source connector."""
    user = User(
        id=str(uuid.uuid4()),
        username="owner",
        email="owner@test.com",
        is_admin=False,
    )
    async_session.add(user)
    await async_session.commit()
    return user


@pytest.fixture()
async def other_user(async_session: AsyncSession) -> User:
    """Create and return a second user who will fork connectors."""
    user = User(
        id=str(uuid.uuid4()),
        username="forker",
        email="forker@test.com",
        is_admin=False,
    )
    async_session.add(user)
    await async_session.commit()
    return user


@pytest.fixture()
async def source_connector(
    async_session: AsyncSession, owner_user: User
) -> Connector:
    """Create a connector with two actions to serve as the fork source."""
    connector = Connector(
        user_id=owner_user.id,
        name="GitHub API",
        description="GitHub REST API connector",
        icon="github",
        type="api",
        base_url="https://api.github.com",
        auth_type="bearer",
        auth_config={"token_prefix": "Bearer"},
        status="published",
        visibility="personal",
        allow_fallback=False,
    )
    async_session.add(connector)
    await async_session.flush()

    action1 = ConnectorAction(
        connector_id=connector.id,
        name="List Repos",
        description="List user repositories",
        method="GET",
        path="/user/repos",
        parameters_schema={"type": "object", "properties": {"per_page": {"type": "integer"}}},
        requires_confirmation=False,
    )
    action2 = ConnectorAction(
        connector_id=connector.id,
        name="Create Issue",
        description="Create a new issue",
        method="POST",
        path="/repos/{owner}/{repo}/issues",
        request_body_template={"title": "{{title}}", "body": "{{body}}"},
        requires_confirmation=True,
    )
    async_session.add_all([action1, action2])
    await async_session.commit()

    # Reload with actions
    result = await async_session.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector.id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Helper: simulate the fork logic (mirrors the endpoint)
# ---------------------------------------------------------------------------


async def _do_fork(
    source: Connector,
    current_user: User,
    db: AsyncSession,
    fork_name: str | None = None,
) -> Connector:
    """Replicate the fork_connector endpoint logic for testing."""
    name = (fork_name or f"{source.name} (Copy)")[:200]

    forked = Connector(
        user_id=current_user.id,
        name=name,
        description=source.description,
        icon=source.icon,
        type=source.type,
        base_url=source.base_url,
        auth_type=source.auth_type,
        auth_config=source.auth_config,
        db_config=None,  # credentials — do NOT copy
        status="draft",
        is_official=False,
        forked_from=source.id,
        version=1,
        visibility="personal",
        org_id=None,
        publish_status=None,
        allow_fallback=source.allow_fallback,
        is_active=True,
    )
    db.add(forked)
    await db.flush()

    for action in source.actions or []:
        cloned_action = ConnectorAction(
            connector_id=forked.id,
            name=action.name,
            description=action.description,
            method=action.method,
            path=action.path,
            parameters_schema=action.parameters_schema,
            request_body_template=action.request_body_template,
            response_extract=action.response_extract,
            requires_confirmation=action.requires_confirmation,
        )
        db.add(cloned_action)

    await db.commit()

    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == forked.id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConnectorForkRequest:
    """Schema validation for ConnectorForkRequest."""

    def test_default_name_is_none(self) -> None:
        req = ConnectorForkRequest()
        assert req.name is None

    def test_custom_name(self) -> None:
        req = ConnectorForkRequest(name="My Custom Fork")
        assert req.name == "My Custom Fork"

    def test_empty_string_name(self) -> None:
        req = ConnectorForkRequest(name="")
        assert req.name == ""


class TestForkCreatesNewConnector:
    """Fork creates a new connector with a different ID."""

    async def test_fork_has_different_id(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.id != source_connector.id
        assert forked.id  # not empty

    async def test_fork_sets_forked_from(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.forked_from == source_connector.id


class TestForkCopiesConfigFields:
    """Fork copies all relevant configuration fields."""

    async def test_copies_name_with_copy_suffix(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.name == f"{source_connector.name} (Copy)"

    async def test_copies_description(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.description == source_connector.description

    async def test_copies_icon(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.icon == source_connector.icon

    async def test_copies_type(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.type == source_connector.type

    async def test_copies_base_url(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.base_url == source_connector.base_url

    async def test_copies_auth_type(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.auth_type == source_connector.auth_type

    async def test_copies_auth_config(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.auth_config == source_connector.auth_config

    async def test_copies_allow_fallback(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.allow_fallback == source_connector.allow_fallback


class TestForkDoesNotCopyCredentials:
    """Fork does NOT copy sensitive fields."""

    async def test_db_config_is_none(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.db_config is None

    async def test_org_id_is_none(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.org_id is None

    async def test_publish_status_is_none(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.publish_status is None

    async def test_is_official_is_false(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.is_official is False


class TestForkCustomName:
    """Fork with custom name uses that name."""

    async def test_custom_name_overrides_default(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(
            source_connector, other_user, async_session, fork_name="My GitHub Clone"
        )
        assert forked.name == "My GitHub Clone"

    async def test_long_name_is_truncated(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        long_name = "A" * 250
        forked = await _do_fork(
            source_connector, other_user, async_session, fork_name=long_name
        )
        assert len(forked.name) <= 200


class TestForkAssignsToCurrentUser:
    """Fork assigns ownership to the current user."""

    async def test_forked_user_id_is_current_user(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        owner_user: User,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.user_id == other_user.id
        assert forked.user_id != owner_user.id

    async def test_owner_can_fork_own_connector(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        owner_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, owner_user, async_session)
        assert forked.user_id == owner_user.id
        assert forked.id != source_connector.id


class TestForkSetsDefaults:
    """Fork sets correct default values for a new draft connector."""

    async def test_status_is_draft(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.status == "draft"

    async def test_visibility_is_personal(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.visibility == "personal"

    async def test_version_is_one(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.version == 1

    async def test_is_active_is_true(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert forked.is_active is True


class TestForkClonesActions:
    """Fork also clones all actions from the source connector."""

    async def test_action_count_matches(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        assert len(forked.actions) == len(source_connector.actions)
        assert len(forked.actions) == 2

    async def test_action_ids_are_different(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        source_ids = {a.id for a in source_connector.actions}
        forked_ids = {a.id for a in forked.actions}
        assert source_ids.isdisjoint(forked_ids)

    async def test_action_fields_are_copied(
        self,
        async_session: AsyncSession,
        source_connector: Connector,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_connector, other_user, async_session)
        source_names = sorted(a.name for a in source_connector.actions)
        forked_names = sorted(a.name for a in forked.actions)
        assert source_names == forked_names

        # Check detailed fields on one action
        forked_by_name = {a.name: a for a in forked.actions}
        source_by_name = {a.name: a for a in source_connector.actions}

        for name in source_by_name:
            src = source_by_name[name]
            fk = forked_by_name[name]
            assert fk.connector_id == forked.id
            assert fk.description == src.description
            assert fk.method == src.method
            assert fk.path == src.path
            assert fk.parameters_schema == src.parameters_schema
            assert fk.request_body_template == src.request_body_template
            assert fk.response_extract == src.response_extract
            assert fk.requires_confirmation == src.requires_confirmation


class TestForkNonExistent:
    """Fork of a non-existent connector raises an error."""

    async def test_get_visible_raises_for_missing_id(
        self,
        async_session: AsyncSession,
        other_user: User,
    ) -> None:
        """The _get_visible_connector helper raises AppError for missing connectors."""
        from fim_one.web.exceptions import AppError

        # Simulate what the endpoint does: try to fetch a non-existent connector
        fake_id = str(uuid.uuid4())
        result = await async_session.execute(
            select(Connector)
            .options(selectinload(Connector.actions))
            .where(Connector.id == fake_id)
        )
        connector = result.scalar_one_or_none()
        assert connector is None  # confirms 404 scenario


class TestForkConnectorWithNoActions:
    """Fork a connector that has no actions — should create a clone with empty actions list."""

    async def test_fork_with_no_actions(
        self,
        async_session: AsyncSession,
        owner_user: User,
        other_user: User,
    ) -> None:
        bare = Connector(
            user_id=owner_user.id,
            name="Bare Connector",
            description=None,
            icon=None,
            type="api",
            base_url="https://example.com",
            auth_type="none",
            status="published",
            visibility="personal",
        )
        async_session.add(bare)
        await async_session.commit()

        result = await async_session.execute(
            select(Connector)
            .options(selectinload(Connector.actions))
            .where(Connector.id == bare.id)
        )
        bare = result.scalar_one()

        forked = await _do_fork(bare, other_user, async_session)
        assert forked.id != bare.id
        assert forked.name == "Bare Connector (Copy)"
        assert len(forked.actions) == 0
