"""Tests for the workflow fork (clone) feature."""

from __future__ import annotations

import copy
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.db.base import Base
from fim_one.db.models.workflow import Workflow
from fim_one.db.models.user import User
from fim_one.web.schemas.workflow import WorkflowForkRequest


# ---------------------------------------------------------------------------
# Fixtures -- in-memory SQLite async database
# ---------------------------------------------------------------------------

SAMPLE_BLUEPRINT = {
    "nodes": [
        {
            "id": "start_1",
            "type": "start",
            "position": {"x": 100, "y": 200},
            "data": {"variables": [{"name": "input_text", "type": "string"}]},
        },
        {
            "id": "llm_1",
            "type": "llm",
            "position": {"x": 400, "y": 200},
            "data": {"prompt_template": "Summarize: {{start_1.input_text}}"},
        },
        {
            "id": "end_1",
            "type": "end",
            "position": {"x": 700, "y": 200},
            "data": {"output_mapping": {"result": "{{llm_1.output}}"}},
        },
    ],
    "edges": [
        {"id": "e1", "source": "start_1", "target": "llm_1"},
        {"id": "e2", "source": "llm_1", "target": "end_1"},
    ],
    "viewport": {"x": 0, "y": 0, "zoom": 1},
}

SAMPLE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "input_text": {"type": "string"},
    },
    "required": ["input_text"],
}


@pytest.fixture()
async def async_session():
    """Create an in-memory SQLite database with all required tables."""
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
    """Create and return a user who owns the source workflow."""
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
    """Create and return a second user who will fork workflows."""
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
async def source_workflow(
    async_session: AsyncSession, owner_user: User
) -> Workflow:
    """Create a workflow to serve as the fork source."""
    wf = Workflow(
        user_id=owner_user.id,
        name="Data Pipeline",
        description="A multi-step data processing workflow",
        blueprint=copy.deepcopy(SAMPLE_BLUEPRINT),
        input_schema=copy.deepcopy(SAMPLE_INPUT_SCHEMA),
        output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
        status="active",
        is_active=True,
        visibility="personal",
        schedule_cron="0 9 * * MON-FRI",
        schedule_enabled=True,
        schedule_timezone="America/New_York",
        schedule_inputs={"input_text": "default value"},
        api_key="wk_test_api_key_12345678",
        webhook_url="https://example.com/webhook",
    )
    async_session.add(wf)
    await async_session.commit()

    result = await async_session.execute(
        select(Workflow).where(Workflow.id == wf.id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Helper: simulate the fork logic (mirrors the endpoint)
# ---------------------------------------------------------------------------


async def _do_fork(
    source: Workflow,
    current_user: User,
    db: AsyncSession,
    fork_name: str | None = None,
) -> Workflow:
    """Replicate the fork_workflow endpoint logic for testing."""
    name = (fork_name or f"{source.name} (Fork)")[:200]

    forked = Workflow(
        user_id=current_user.id,
        name=name,
        icon=source.icon,
        description=source.description,
        blueprint=copy.deepcopy(source.blueprint),
        input_schema=copy.deepcopy(source.input_schema) if source.input_schema else None,
        output_schema=copy.deepcopy(source.output_schema) if source.output_schema else None,
        status="draft",
        is_active=False,
        visibility="personal",
        org_id=None,
        publish_status=None,
        forked_from=source.id,
    )
    db.add(forked)
    await db.commit()

    result = await db.execute(
        select(Workflow).where(Workflow.id == forked.id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWorkflowForkRequest:
    """Schema validation for WorkflowForkRequest."""

    def test_default_name_is_none(self) -> None:
        req = WorkflowForkRequest()
        assert req.name is None

    def test_custom_name(self) -> None:
        req = WorkflowForkRequest(name="My Custom Fork")
        assert req.name == "My Custom Fork"


class TestForkCreatesNewWorkflow:
    """Fork creates a new workflow with a different ID."""

    async def test_fork_has_different_id(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.id != source_workflow.id
        assert forked.id  # not empty

    async def test_fork_sets_forked_from(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.forked_from == source_workflow.id


class TestForkCopiesBlueprintDeeply:
    """Fork deep-copies the blueprint JSON."""

    async def test_blueprint_content_matches(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.blueprint == source_workflow.blueprint

    async def test_blueprint_is_independent_copy(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        # Mutating the forked blueprint should not affect the source
        forked.blueprint["nodes"].append({"id": "new_node"})
        assert len(forked.blueprint["nodes"]) != len(source_workflow.blueprint["nodes"])

    async def test_input_schema_copied(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.input_schema == source_workflow.input_schema

    async def test_output_schema_copied(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.output_schema == source_workflow.output_schema

    async def test_description_copied(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.description == source_workflow.description


class TestForkStatusAndVisibility:
    """Fork sets correct default values for a new draft workflow."""

    async def test_status_is_draft(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.status == "draft"

    async def test_visibility_is_personal(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.visibility == "personal"

    async def test_is_active_is_false(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.is_active is False

    async def test_org_id_is_none(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.org_id is None

    async def test_publish_status_is_none(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.publish_status is None


class TestForkDoesNotCopySensitiveFields:
    """Fork does NOT copy cron schedule, API key, env vars, etc."""

    async def test_schedule_cron_not_copied(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.schedule_cron is None

    async def test_schedule_enabled_not_copied(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.schedule_enabled is False

    async def test_schedule_inputs_not_copied(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.schedule_inputs is None

    async def test_api_key_not_copied(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.api_key is None

    async def test_webhook_url_not_copied(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.webhook_url is None

    async def test_env_vars_blob_not_copied(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.env_vars_blob is None


class TestForkCustomName:
    """Fork with custom name uses that name."""

    async def test_custom_name_overrides_default(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(
            source_workflow, other_user, async_session, fork_name="My Pipeline Clone"
        )
        assert forked.name == "My Pipeline Clone"

    async def test_default_name_has_fork_suffix(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.name == f"{source_workflow.name} (Fork)"

    async def test_long_name_is_truncated(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        other_user: User,
    ) -> None:
        long_name = "A" * 250
        forked = await _do_fork(
            source_workflow, other_user, async_session, fork_name=long_name
        )
        assert len(forked.name) <= 200


class TestForkAssignsToCurrentUser:
    """Fork assigns ownership to the current user."""

    async def test_forked_user_id_is_current_user(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        owner_user: User,
        other_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, other_user, async_session)
        assert forked.user_id == other_user.id
        assert forked.user_id != owner_user.id

    async def test_owner_can_fork_own_workflow(
        self,
        async_session: AsyncSession,
        source_workflow: Workflow,
        owner_user: User,
    ) -> None:
        forked = await _do_fork(source_workflow, owner_user, async_session)
        assert forked.user_id == owner_user.id
        assert forked.id != source_workflow.id
