"""Where a run's shell, code-execution and file tools work.

A runtime built with no conversation (a workflow Agent step, a
``call_agent`` delegate, an evaluation run) used to get those tools on the
fallback directories ``tmp/default/{sandbox,exec}`` and ``tmp/workspace``:
one directory for every user and tenant, and on the local backend a
directory of the host. Each run now has a directory of its own, removed
when the run ends, and a runtime that has no directory to give has none of
those tools.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly import sandbox
from fim_one.core.assembly.principal import CallContext, provisional_principal
from fim_one.core.assembly.runtime import get_agent_runtime_factory
from fim_one.core.tool.builtin import file_ops, node_exec, python_exec, shell_exec
from fim_one.core.workflow import run_service as rs
from fim_one.core.workflow.nodes import AgentExecutor
from fim_one.core.workflow.types import (
    ExecutionContext,
    NodeStatus,
    NodeType,
    WorkflowNodeDef,
)
from fim_one.core.workflow.variable_store import VariableStore
from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.workflow import Workflow
from fim_one.web.services import agent_runtime

from .assembly import matrix as m

pytestmark = pytest.mark.timeout(60)

SANDBOXED = set(sandbox.SANDBOXED_TOOLS)

# The directories every caller shared.
SHARED_DEFAULTS = {
    shell_exec._DEFAULT_SANDBOX_DIR,
    python_exec._DEFAULT_EXEC_DIR,
    node_exec._DEFAULT_EXEC_DIR,
    file_ops._DEFAULT_WORKSPACE_DIR,
}


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sandbox.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Sandboxes and uploads live under the test's own directory."""
    root = tmp_path / "data" / "sandbox"
    monkeypatch.setattr(sandbox, "_BASE", root)
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CODE_EXEC_BACKEND", "local")
    return root


async def _scene(factory: async_sessionmaker[AsyncSession], role: str = "subscriber") -> m.Scene:
    """A scene whose carrier agent has the shell, code and file tools."""
    scene = await m.seed_scene(factory, m.SceneKey(role, True, True, False))
    async with factory() as s:
        agent = await s.get(Agent, scene.carrier_agent_id)
        assert agent is not None
        agent.tool_categories = ["computation", "filesystem"]
        await s.commit()
    return scene


def _names(tools: Any) -> set[str]:
    return {t.name for t in tools.list_tools()}


def _dirs(tools: Any) -> dict[str, Path]:
    """The directory each sandboxed tool of *tools* works in."""
    found: dict[str, Path] = {}
    for tool in tools.list_tools():
        for attr in ("_sandbox_dir", "_exec_dir", "_workspace_dir"):
            if tool.name in SANDBOXED and getattr(tool, attr, None) is not None:
                found[tool.name] = Path(getattr(tool, attr))
    return found


def _call_agent(tools: Any) -> Any:
    tool = tools.get("call_agent")
    assert tool is not None
    return tool


def _ctx(user_id: str, entry: Any = "sub_agent", **kwargs: Any) -> CallContext:
    return CallContext(
        principal=provisional_principal(user_id), binding=None, entry=entry, **kwargs
    )


def _agent_node(agent_id: str | None) -> WorkflowNodeDef:
    data: dict[str, Any] = {"prompt_template": "go"}
    if agent_id is not None:
        data["agent_id"] = agent_id
    return WorkflowNodeDef(id="agent_1", type=NodeType.AGENT, data=data)


async def _workflow(scene: m.Scene, factory: async_sessionmaker[AsyncSession]) -> Workflow:
    async with factory() as s:
        wf = await s.get(Workflow, scene.ids["workflow"])
        assert wf is not None
        return wf


class TestSandboxIds:
    def test_a_runs_directory_is_under_the_conversations_parent(self, base: Path) -> None:
        from fim_one.web.api import chat

        run_root = sandbox.sandbox_root(sandbox.workflow_run_sandbox_id("r1"))
        conversation_root = chat._conversation_sandbox_root("c1")
        assert conversation_root is not None
        assert run_root.parent == conversation_root.parent == base
        assert run_root.name == "wfrun-r1"

    @pytest.mark.parametrize("bad", ["", "..", "a/b", "../x", ".hidden", "a\\b"])
    def test_an_id_that_is_not_one_path_segment_is_refused(self, bad: str) -> None:
        with pytest.raises(ValueError):
            sandbox.sandbox_root(bad)

    async def test_release_removes_a_runs_directory_and_never_a_conversations(
        self, base: Path
    ) -> None:
        run_id = sandbox.delegation_sandbox_id()
        (base / run_id / "workspace").mkdir(parents=True)
        (base / "conversation-1" / "workspace").mkdir(parents=True)

        await sandbox.release_sandbox(run_id)
        await sandbox.release_sandbox("conversation-1")
        await sandbox.release_sandbox(None)
        await sandbox.release_sandbox("wfrun-../conversation-1")

        assert not (base / run_id).exists()
        assert (base / "conversation-1" / "workspace").is_dir()


class TestTwoWorkflowRuns:
    async def test_runs_by_different_users_cannot_see_each_others_files(
        self, factory: async_sessionmaker[AsyncSession], base: Path
    ) -> None:
        """Both ran in ``tmp/workspace``: the second read the first one's file."""
        scene = await _scene(factory)
        first_wrote = asyncio.Event()
        second_looked = asyncio.Event()
        seen: dict[str, Any] = {}

        with m.outside_world(factory) as out:

            class _Engine:
                def __init__(self, **_kwargs: Any) -> None:
                    pass

                async def execute_streaming(
                    self, _parsed: Any, _inputs: Any, context: Any = None
                ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
                    result = await AgentExecutor().execute(
                        _agent_node(scene.carrier_agent_id), VariableStore(), context
                    )
                    assert result.status == NodeStatus.COMPLETED, result.error
                    tools = out.agents_built[-1]["tools"]
                    files = tools.get("file_ops")
                    if context.user_id == scene.users["owner"]:
                        seen["first_dirs"] = _dirs(tools)
                        await files.run(operation="write", path="secret.txt", content="s3cret")
                        await tools.get("shell_exec").run(command="echo by-shell > shell.txt")
                        first_wrote.set()
                        # Still running while the other user's run looks.
                        await second_looked.wait()
                    else:
                        await first_wrote.wait()
                        seen["second_dirs"] = _dirs(tools)
                        seen["read"] = str(await files.run(operation="read", path="secret.txt"))
                        seen["list"] = str(await files.run(operation="list", path="."))
                        seen["shell"] = str(await tools.get("shell_exec").run(command="ls -a"))
                        second_looked.set()
                    yield "run_completed", {"status": "completed", "outputs": {}}

            wf = await _workflow(scene, factory)
            service = rs.WorkflowRunService(engine_factory=_Engine)
            first = await service.start_run(
                wf, caller_id=scene.users["owner"], inputs={}, mode=rs.RunMode.INTERACTIVE
            )
            second = await service.start_run(
                wf, caller_id=scene.users["subscriber"], inputs={}, mode=rs.RunMode.INTERACTIVE
            )
            outcomes = [await first.wait(), await second.wait()]

        assert [o.status for o in outcomes] == ["completed", "completed"], outcomes
        # Each run's tools work in that run's own directory.
        for run, dirs in ((first, seen["first_dirs"]), (second, seen["second_dirs"])):
            assert set(dirs) == SANDBOXED
            assert set(dirs.values()) == {base / f"wfrun-{run.run_id}" / "workspace"}
        # The second user finds nothing of the first.
        assert "s3cret" not in seen["read"]
        for listing in (seen["list"], seen["shell"]):
            assert "secret.txt" not in listing and "shell.txt" not in listing
            assert "s3cret" not in listing
        # And the files went with the runs.
        assert not (base / f"wfrun-{first.run_id}").exists()
        assert not (base / f"wfrun-{second.run_id}").exists()

    @pytest.mark.parametrize("ending", ["completed", "failed", "cancelled"])
    async def test_the_runs_directory_is_removed_however_the_run_ends(
        self, factory: async_sessionmaker[AsyncSession], base: Path, ending: str
    ) -> None:
        scene = await _scene(factory)
        roots: list[Path] = []
        started = asyncio.Event()

        class _Engine:
            def __init__(self, **_kwargs: Any) -> None:
                pass

            async def execute_streaming(
                self, _parsed: Any, _inputs: Any, context: Any = None
            ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
                root = sandbox.sandbox_root(context.sandbox_id)
                (root / "workspace").mkdir(parents=True)
                (root / "workspace" / "left.txt").write_text("x")
                roots.append(root)
                started.set()
                if ending == "failed":
                    raise RuntimeError("engine broke")
                if ending == "cancelled":
                    await asyncio.Event().wait()
                yield "run_completed", {"status": "completed", "outputs": {}}

        with m.outside_world(factory):
            wf = await _workflow(scene, factory)
            run = await rs.WorkflowRunService(engine_factory=_Engine).start_run(
                wf, caller_id=scene.users["owner"], inputs={}, mode=rs.RunMode.INTERACTIVE
            )
            await started.wait()
            if ending == "cancelled":
                assert run._driver is not None
                run._driver.cancel()
                await asyncio.wait([run._driver])
            else:
                await run.wait()

        assert run.outcome.status == ending
        (root,) = roots
        assert root == base / f"wfrun-{run.run_id}"
        assert not root.exists()

    async def test_a_single_step_test_has_its_own_directory_too(
        self, factory: async_sessionmaker[AsyncSession], base: Path
    ) -> None:
        scene = await _scene(factory)
        with m.outside_world(factory) as out:
            wf = await _workflow(scene, factory)
            result, _variables = await rs.WorkflowRunService().test_node(
                wf,
                caller_id=scene.users["owner"],
                node=_agent_node(scene.carrier_agent_id),
                variables={},
                env_overrides={},
            )
            assert result.status == NodeStatus.COMPLETED, result.error
            dirs = _dirs(out.agents_built[-1]["tools"])

        assert set(dirs) == SANDBOXED
        (root,) = set(dirs.values())
        assert root.parent.parent == base and root.parent.name.startswith("wfrun-test-")
        assert not root.parent.exists()


class TestADelegation:
    """``call_agent``: the calling conversation's files, or the delegation's own."""

    async def test_from_a_conversation_the_delegate_works_in_that_conversations_directory(
        self, factory: async_sessionmaker[AsyncSession], base: Path
    ) -> None:
        """As a chat with that agent in the conversation does; it had ``tmp/default``."""
        scene = await _scene(factory)
        with m.outside_world(factory) as out:
            parent = await agent_runtime.resolve_toolset(
                _ctx(scene.actor, "chat_noagent", conversation_id="conv-1"), None
            )
            (base / "conv-1" / "workspace").mkdir(parents=True)
            answer = await _call_agent(parent).run(
                agent_id=scene.carrier_agent_id, task="go"
            )
            assert answer == "done"
            delegate = out.agents_built[-1]["tools"]

            cfg = await agent_runtime.resolve_agent(_ctx(scene.actor), scene.carrier_agent_id)
            chat_with_it = await agent_runtime.resolve_toolset(
                agent_runtime.call_context(
                    provisional_principal(scene.actor), cfg, conversation_id="conv-1"
                ),
                cfg,
            )

        assert set(_dirs(delegate)) == SANDBOXED
        assert _dirs(delegate) == _dirs(chat_with_it)
        assert set(_dirs(delegate).values()) == {base / "conv-1" / "workspace"}
        assert _dirs(delegate) == _dirs(parent)
        # The conversation's files outlive the delegation.
        assert (base / "conv-1" / "workspace").is_dir()

    async def test_its_calls_are_still_recorded_as_its_own_not_the_conversations(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scene = await _scene(factory)
        with m.outside_world(factory):
            parent = await agent_runtime.resolve_toolset(
                _ctx(scene.actor, "chat_noagent", conversation_id="conv-1"), None
            )
        call_agent = _call_agent(parent)
        assert call_agent._sandbox_id == "conv-1"
        assert call_agent._call_context.entry == "sub_agent"
        assert call_agent._call_context.conversation_id is None

    @pytest.mark.parametrize("fails", [False, True])
    async def test_outside_a_conversation_each_delegation_has_its_own_removed_afterwards(
        self,
        factory: async_sessionmaker[AsyncSession],
        base: Path,
        fails: bool,
    ) -> None:
        scene = await _scene(factory)
        roots: list[Path] = []
        with m.outside_world(factory) as out:
            from fim_one.core.agent import react

            class _Agent:
                def __init__(self, **kwargs: Any) -> None:
                    out.agents_built.append(kwargs)

                async def run(self, _query: str) -> Any:
                    dirs = _dirs(out.agents_built[-1]["tools"])
                    assert set(dirs) == SANDBOXED
                    (root,) = set(dirs.values())
                    root.mkdir(parents=True)
                    (root / "left.txt").write_text("x")
                    roots.append(root)
                    if fails:
                        raise RuntimeError("delegate broke")
                    return type("R", (), {"answer": "done", "iterations": 1, "usage": None})()

            parent = await agent_runtime.resolve_toolset(_ctx(scene.actor, "chat_noagent"), None)
            # No conversation: the caller itself has no directory, so no such tools.
            assert not _names(parent) & SANDBOXED
            call_agent = _call_agent(parent)
            with patch.object(react, "ReActAgent", _Agent):
                for _ in range(2):
                    await call_agent.run(agent_id=scene.carrier_agent_id, task="go")

        first, second = roots
        assert first != second
        for root in roots:
            assert root.parent.parent == base and root.parent.name.startswith("subagent-")
            assert not root.parent.exists()


class TestAnEvaluationRun:
    async def test_its_cases_work_in_the_runs_own_directory_removed_afterwards(
        self,
        factory: async_sessionmaker[AsyncSession],
        base: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fim_one.db.models.eval import EvalRun
        from fim_one.web.api import eval_runs

        async with factory() as s:
            s.add(
                EvalRun(
                    id="eval-1",
                    user_id="u1",
                    agent_id="a1",
                    dataset_id="d1",
                    status="pending",
                    total_cases=1,
                )
            )
            await s.commit()
        seen: list[dict[str, Path]] = []

        async def _case(_case: Any, _instructions: Any, tools: Any, *_rest: Any) -> None:
            dirs = _dirs(tools)
            (root,) = set(dirs.values())
            root.mkdir(parents=True)
            seen.append(dirs)

        monkeypatch.setattr(eval_runs, "create_session", factory)
        monkeypatch.setattr(eval_runs, "_run_one_case", _case)

        await eval_runs._execute_eval_run("eval-1", None, [{"id": "c1"}], None, None)

        (dirs,) = seen
        assert set(dirs) == SANDBOXED
        assert set(dirs.values()) == {base / "evalrun-eval-1" / "workspace"}
        assert not (base / "evalrun-eval-1").exists()


class TestNoRuntimeIsBuiltOnTheSharedDirectories:
    """The gate: an entry point with no directory to give has none of the tools."""

    def _assert_none_shared(self, tools: Any, base: Path) -> None:
        for name, directory in _dirs(tools).items():
            assert directory not in SHARED_DEFAULTS, name
            assert base in directory.parents, (name, directory)

    def test_the_registry_built_with_no_directory_has_none_of_them(self) -> None:
        from fim_one.web.deps import get_tools

        tools = get_tools()
        assert not _names(tools) & SANDBOXED
        assert {"calculator", "web_fetch"} <= _names(tools)

    def test_the_registry_built_with_a_directory_has_them_there(self, base: Path) -> None:
        from fim_one.web.deps import get_tools

        tools = get_tools(sandbox_root=base / "c1")
        assert set(_dirs(tools)) == SANDBOXED
        self._assert_none_shared(tools, base)

    @pytest.mark.parametrize("entry", ["sub_agent", "workflow_node", "chat", "chat_noagent"])
    async def test_a_runtime_with_neither_a_conversation_nor_a_sandbox(
        self, factory: async_sessionmaker[AsyncSession], entry: str
    ) -> None:
        scene = await _scene(factory)
        with m.outside_world(factory):
            built = await get_agent_runtime_factory().build(
                _ctx(scene.actor, entry),
                None if entry == "chat_noagent" else scene.carrier_agent_id,
            )
            await built.aclose()
        assert not _names(built.tools) & SANDBOXED
        assert _dirs(built.tools) == {}

    @pytest.mark.parametrize("agent", ["carrier", None])
    async def test_a_workflow_step_whose_run_has_no_directory(
        self, factory: async_sessionmaker[AsyncSession], agent: str | None
    ) -> None:
        """A context built by hand: nobody would remove a directory made for it."""
        scene = await _scene(factory)
        with m.outside_world(factory) as out:
            result = await AgentExecutor().execute(
                _agent_node(scene.carrier_agent_id if agent else None),
                VariableStore(),
                ExecutionContext(run_id="r", user_id=scene.actor, workflow_id="w"),
            )
        assert result.status == NodeStatus.COMPLETED, result.error
        assert not _names(out.agents_built[-1]["tools"]) & SANDBOXED

    async def test_every_entry_point_that_has_the_tools_has_them_in_its_own_directory(
        self, factory: async_sessionmaker[AsyncSession], base: Path
    ) -> None:
        scene = await _scene(factory)
        with m.outside_world(factory) as out:
            port = get_agent_runtime_factory()
            # A chat turn, with and without an agent.
            for agent_id in (scene.carrier_agent_id, None):
                built = await port.build(
                    _ctx(scene.actor, "chat" if agent_id else "chat_noagent"),
                    agent_id,
                    conversation_id="conv-1",
                )
                await built.aclose()
                assert set(_dirs(built.tools)) == SANDBOXED
                self._assert_none_shared(built.tools, base)
            # A workflow Agent step of a run the run service started.
            run_id = sandbox.workflow_run_sandbox_id("run-1")
            result = await AgentExecutor().execute(
                _agent_node(scene.carrier_agent_id),
                VariableStore(),
                ExecutionContext(
                    run_id="run-1", user_id=scene.actor, workflow_id="w", sandbox_id=run_id
                ),
            )
            assert result.status == NodeStatus.COMPLETED, result.error
            step_tools = out.agents_built[-1]["tools"]
            assert set(_dirs(step_tools)) == SANDBOXED
            self._assert_none_shared(step_tools, base)
            # A delegate, called from a conversation and from none.
            for conversation_id in ("conv-1", None):
                parent = await agent_runtime.resolve_toolset(
                    _ctx(scene.actor, "chat_noagent", conversation_id=conversation_id), None
                )
                await _call_agent(parent).run(agent_id=scene.carrier_agent_id, task="go")
                delegate_tools = out.agents_built[-1]["tools"]
                assert set(_dirs(delegate_tools)) == SANDBOXED
                self._assert_none_shared(delegate_tools, base)
