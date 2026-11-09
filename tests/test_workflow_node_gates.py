"""The Human Intervention switch (``WORKFLOW_HUMAN_INTERVENTION_ENABLED``).

Off by default. With it off no blueprint containing the node can be
written, by any route; a workflow stored earlier still loads so its author
can remove the node; validation marks the node. The run-start refusal is
covered per entry point in ``test_workflow_run_service.py``.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.core.workflow import node_gates
from fim_one.core.workflow.templates import get_template
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowVersion
from fim_one.web.api import workflow_templates as tpl_api
from fim_one.web.api import workflows as wf_api
from fim_one.web.api.version import get_version
from fim_one.web.exceptions import AppError
from fim_one.web.schemas.workflow import (
    WorkflowCreate,
    WorkflowExportData,
    WorkflowFromTemplateRequest,
    WorkflowImportFileRequest,
    WorkflowUpdate,
)

APPROVAL_TEMPLATE = "human-approval-pipeline"

PLAIN: dict[str, Any] = {
    "schema_version": 2,
    "nodes": [
        {"id": "start_1", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "end_1", "type": "end", "position": {"x": 0, "y": 0}, "data": {}},
    ],
    "edges": [{"id": "e1", "source": "start_1", "target": "end_1"}],
    "viewport": {"x": 0, "y": 0, "zoom": 1},
}


def _with_approval() -> dict[str, Any]:
    bp = copy.deepcopy(PLAIN)
    bp["nodes"].insert(
        1,
        {
            "id": "human_1",
            "type": "humanIntervention",
            "position": {"x": 0, "y": 0},
            "data": {"label": "Sign off", "prompt_message": "ok?"},
        },
    )
    bp["edges"] = [
        {"id": "e1", "source": "start_1", "target": "human_1"},
        {"id": "e2", "source": "human_1", "target": "end_1"},
    ]
    return bp


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    import fim_one.db.models  # noqa: F401
    from fim_one.db.base import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


@pytest.fixture()
async def user(session: AsyncSession) -> User:
    u = User(id=str(uuid.uuid4()), email="author@x.io")
    session.add(u)
    await session.commit()
    return u


async def _stored_with_approval(session: AsyncSession, user: User) -> Workflow:
    """A workflow saved while the switch was on."""
    wf = Workflow(user_id=user.id, name="legacy", blueprint=_with_approval(), status="draft")
    session.add(wf)
    await session.commit()
    return wf


def _assert_refused(exc: pytest.ExceptionInfo[AppError]) -> None:
    assert exc.value.status_code == 422
    assert exc.value.error_code == "human_intervention_disabled"
    (err,) = exc.value.detail_args["errors"]
    assert err["node_id"] == "human_1"
    assert err["loc"] == "nodes.1.type"
    assert "Sign off" in err["message"]
    assert exc.value.detail_args["nodes"] == "Sign off"
    assert "Sign off" in str(exc.value.detail)


class TestSwitch:
    def test_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(node_gates.ENV_HUMAN_INTERVENTION, raising=False)
        assert node_gates.human_intervention_enabled() is False

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("true", True), ("1", True), ("ON", True), ("false", False), ("", False), ("0", False)],
    )
    def test_values(self, monkeypatch: pytest.MonkeyPatch, value: str, expected: bool) -> None:
        monkeypatch.setenv(node_gates.ENV_HUMAN_INTERVENTION, value)
        assert node_gates.human_intervention_enabled() is expected

    @pytest.mark.asyncio
    async def test_version_endpoint_reports_it(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert (await get_version(db=session))["workflow_human_intervention"] is False
        monkeypatch.setenv(node_gates.ENV_HUMAN_INTERVENTION, "true")
        assert (await get_version(db=session))["workflow_human_intervention"] is True


class TestWritesRefusedWhenOff:
    @pytest.mark.asyncio
    async def test_create(self, session: AsyncSession, user: User) -> None:
        with pytest.raises(AppError) as exc:
            await wf_api.create_workflow(
                WorkflowCreate(name="w", blueprint=_with_approval()), current_user=user, db=session
            )
        _assert_refused(exc)

    @pytest.mark.asyncio
    async def test_update(self, session: AsyncSession, user: User) -> None:
        created = await wf_api.create_workflow(
            WorkflowCreate(name="w", blueprint=PLAIN), current_user=user, db=session
        )
        with pytest.raises(AppError) as exc:
            await wf_api.update_workflow(
                created.data["id"],
                WorkflowUpdate(blueprint=_with_approval()),
                current_user=user,
                db=session,
            )
        _assert_refused(exc)

    @pytest.mark.asyncio
    async def test_import(self, session: AsyncSession, user: User) -> None:
        body = WorkflowImportFileRequest(
            format="fim_workflow_v1",
            workflow=WorkflowExportData(name="w", blueprint=_with_approval()),
        )
        with pytest.raises(AppError) as exc:
            await wf_api.import_workflow(body, current_user=user, db=session)
        _assert_refused(exc)

    @pytest.mark.asyncio
    async def test_import_of_a_pre_v2_export(self, session: AsyncSession, user: User) -> None:
        """The node is recognised under its older spelling too."""
        bp = _with_approval()
        bp.pop("schema_version")
        bp["nodes"][1]["type"] = "custom"
        bp["nodes"][1]["data"]["type"] = "HUMAN_INTERVENTION"
        body = WorkflowImportFileRequest(data=WorkflowExportData(name="w", blueprint=bp))
        with pytest.raises(AppError) as exc:
            await wf_api.import_workflow(body, current_user=user, db=session)
        _assert_refused(exc)

    @pytest.mark.asyncio
    async def test_fork_of_a_stored_workflow(self, session: AsyncSession, user: User) -> None:
        wf = await _stored_with_approval(session, user)
        with pytest.raises(AppError) as exc:
            await wf_api.fork_workflow(wf.id, None, current_user=user, db=session)
        _assert_refused(exc)

    @pytest.mark.asyncio
    async def test_restore_of_an_old_version(self, session: AsyncSession, user: User) -> None:
        created = await wf_api.create_workflow(
            WorkflowCreate(name="w", blueprint=PLAIN), current_user=user, db=session
        )
        old = WorkflowVersion(
            workflow_id=created.data["id"],
            version_number=1,
            blueprint=_with_approval(),
            created_by=user.id,
        )
        session.add(old)
        await session.commit()
        with pytest.raises(AppError) as exc:
            await wf_api.restore_workflow_version(
                created.data["id"], old.id, current_user=user, db=session
            )
        _assert_refused(exc)

    @pytest.mark.asyncio
    async def test_builtin_template(self, session: AsyncSession, user: User) -> None:
        for call in (
            wf_api.create_workflow_from_template(
                WorkflowFromTemplateRequest(template_id=APPROVAL_TEMPLATE),
                current_user=user,
                db=session,
            ),
            tpl_api.create_workflow_from_template(
                APPROVAL_TEMPLATE,
                WorkflowFromTemplateRequest(template_id=APPROVAL_TEMPLATE),
                current_user=user,
                db=session,
            ),
        ):
            with pytest.raises(AppError) as exc:
                await call
            assert exc.value.error_code == "human_intervention_disabled"
            assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_templates_built_on_it_are_not_listed(
        self, session: AsyncSession, user: User
    ) -> None:
        assert get_template(APPROVAL_TEMPLATE) is not None
        builtin = (await wf_api.list_workflow_templates(current_user=user)).data
        assert builtin
        assert APPROVAL_TEMPLATE not in {t["id"] for t in builtin}
        gallery = (await tpl_api.list_all_templates(current_user=user, db=session)).data
        assert gallery["templates"]
        assert APPROVAL_TEMPLATE not in {t["id"] for t in gallery["templates"]}


class TestStoredWorkflowWhenOff:
    @pytest.mark.asyncio
    async def test_still_loads_with_the_node(self, session: AsyncSession, user: User) -> None:
        wf = await _stored_with_approval(session, user)
        loaded = await wf_api.get_workflow(wf.id, current_user=user, db=session)
        assert [n["type"] for n in loaded.data["blueprint"]["nodes"]] == [
            "start",
            "humanIntervention",
            "end",
        ]

    @pytest.mark.asyncio
    async def test_saves_once_the_node_is_removed(self, session: AsyncSession, user: User) -> None:
        wf = await _stored_with_approval(session, user)
        saved = await wf_api.update_workflow(
            wf.id, WorkflowUpdate(blueprint=PLAIN), current_user=user, db=session
        )
        assert [n["type"] for n in saved.data["blueprint"]["nodes"]] == ["start", "end"]

    @pytest.mark.asyncio
    async def test_other_fields_can_still_be_edited(
        self, session: AsyncSession, user: User
    ) -> None:
        wf = await _stored_with_approval(session, user)
        saved = await wf_api.update_workflow(
            wf.id, WorkflowUpdate(name="renamed"), current_user=user, db=session
        )
        assert saved.data["name"] == "renamed"

    @pytest.mark.asyncio
    async def test_validation_marks_the_node(self, session: AsyncSession, user: User) -> None:
        wf = await _stored_with_approval(session, user)

        live = (await wf_api.validate_blueprint_endpoint(_with_approval(), current_user=user)).data
        assert live["valid"] is False
        assert "Sign off" in live["error"]
        assert {"node_id": "human_1", "code": "human_intervention_disabled"}.items() <= (
            live["warnings"][0].items()
        )

        saved = (await wf_api.validate_workflow(wf.id, current_user=user, db=session)).data
        assert saved["valid"] is False
        assert "Sign off" in saved["errors"][0]
        assert saved["warnings"][0]["node_id"] == "human_1"
        assert saved["topology_order"] == ["start_1", "human_1", "end_1"]


@pytest.mark.usefixtures("human_intervention_enabled")
class TestSwitchedOn:
    """With the switch on nothing is refused or hidden."""

    @pytest.mark.asyncio
    async def test_create_fork_and_template(self, session: AsyncSession, user: User) -> None:
        created = await wf_api.create_workflow(
            WorkflowCreate(name="w", blueprint=_with_approval()), current_user=user, db=session
        )
        assert created.data["blueprint"]["nodes"][1]["type"] == "humanIntervention"
        forked = await wf_api.fork_workflow(
            created.data["id"], None, current_user=user, db=session
        )
        assert forked.data["blueprint"]["nodes"][1]["type"] == "humanIntervention"
        from_tpl = await wf_api.create_workflow_from_template(
            WorkflowFromTemplateRequest(template_id=APPROVAL_TEMPLATE),
            current_user=user,
            db=session,
        )
        assert from_tpl.data["id"]

    @pytest.mark.asyncio
    async def test_template_listed_and_validation_clean(
        self, session: AsyncSession, user: User
    ) -> None:
        builtin = (await wf_api.list_workflow_templates(current_user=user)).data
        assert APPROVAL_TEMPLATE in {t["id"] for t in builtin}
        gallery = (await tpl_api.list_all_templates(current_user=user, db=session)).data
        assert APPROVAL_TEMPLATE in {t["id"] for t in gallery["templates"]}
        live = (await wf_api.validate_blueprint_endpoint(_with_approval(), current_user=user)).data
        assert live["valid"] is True
        assert "error" not in live
        assert all(w["code"] != "human_intervention_disabled" for w in live["warnings"])
