"""Tests for CallAgentTool: delegation through the agent runtime port."""
from __future__ import annotations

import json
from typing import Any

import pytest

from fim_one.core.agent.hooks import (
    Hook,
    HookContext,
    HookPoint,
    HookRegistry,
    HookResult,
)
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import CallContext, provisional_principal
from fim_one.core.assembly.runtime import AgentRuntime, set_agent_runtime_factory
from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.tool.base import BaseTool
from fim_one.core.tool.builtin.call_agent import CallAgentTool
from fim_one.core.tool.registry import ToolRegistry

from .conftest import FakeLLM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_llm(answer: str = "sub-agent reply") -> FakeLLM:
    """Create a FakeLLM that returns a final_answer JSON response."""
    return FakeLLM(
        responses=[
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps(
                        {
                            "type": "final_answer",
                            "reasoning": "Done.",
                            "answer": answer,
                        }
                    ),
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )
        ]
    )


def _make_agent_catalog(
    *, agent_id: str = "agent-1", name: str = "Helper"
) -> list[dict[str, Any]]:
    return [{"id": agent_id, "name": name, "description": "A helpful agent"}]


def _ctx(user_id: str = "user-1") -> CallContext:
    return CallContext(
        principal=provisional_principal(user_id), binding=None, entry="sub_agent"
    )


class _Client:
    """Stands in for the MCP client a runtime holds."""

    def __init__(self) -> None:
        self.closed = 0

    async def disconnect_all(self) -> None:
        self.closed += 1


class _Factory:
    """A runtime factory with fixed parts that records what it was asked for."""

    def __init__(
        self,
        *,
        llm: FakeLLM | None = None,
        tools: ToolRegistry | None = None,
        hooks: HookRegistry | None = None,
        instructions: str | None = "Be helpful.",
        raises: BaseException | None = None,
    ) -> None:
        self.llm = llm or _make_fake_llm()
        self.tools = tools or ToolRegistry()
        self.hooks = hooks or HookRegistry()
        self.instructions = instructions
        self.raises = raises
        self.client = _Client()
        self.asked: list[tuple[CallContext, str | None, str | None]] = []

    async def build(
        self, ctx: CallContext, agent_id: str | None, *, conversation_id: str | None = None
    ) -> AgentRuntime:
        self.asked.append((ctx, agent_id, conversation_id))
        if self.raises is not None:
            raise self.raises
        return AgentRuntime(
            ctx=ctx,
            agent={"agent_id": agent_id},
            llm=self.llm,
            fast_llm=self.llm,
            tools=self.tools,
            hooks=self.hooks,
            instructions=self.instructions,
            mcp_client=self.client,
        )


def _tool(factory: _Factory, **catalog: Any) -> CallAgentTool:
    return CallAgentTool(
        available_agents=_make_agent_catalog(**catalog),
        call_context=_ctx(),
        runtime_factory=factory,
    )


# ---------------------------------------------------------------------------
# Construction and schema tests
# ---------------------------------------------------------------------------


class TestCallAgentToolConstruction:
    """Test that CallAgentTool builds correct metadata from the agent catalog."""

    def test_name(self) -> None:
        assert _tool(_Factory()).name == "call_agent"

    def test_description_includes_agent(self) -> None:
        tool = _tool(_Factory(), name="Searcher")
        assert "Searcher" in tool.description
        assert "agent-1" in tool.description

    def test_parameters_schema_enum(self) -> None:
        catalog = _make_agent_catalog(agent_id="a1") + _make_agent_catalog(
            agent_id="a2", name="Other"
        )
        tool = CallAgentTool(available_agents=catalog, call_context=_ctx())
        schema = tool.parameters_schema
        agent_ids = schema["properties"]["agent_id"]["enum"]
        assert set(agent_ids) == {"a1", "a2"}
        assert schema["required"] == ["agent_id", "task"]


# ---------------------------------------------------------------------------
# The delegate's runtime comes from the port
# ---------------------------------------------------------------------------


class TestRuntime:
    @pytest.mark.asyncio
    async def test_the_delegate_is_built_for_the_caller_and_closed_afterwards(self) -> None:
        factory = _Factory(llm=_make_fake_llm("hello from sub-agent"))
        tool = _tool(factory)

        result = await tool.run(agent_id="agent-1", task="say hello")

        assert "hello from sub-agent" in result
        ((ctx, agent_id, conversation_id),) = factory.asked
        assert (ctx.principal.user_id, ctx.entry, agent_id) == ("user-1", "sub_agent", "agent-1")
        assert conversation_id is None
        # Its MCP connections are released once it has answered.
        assert factory.client.closed == 1

    @pytest.mark.asyncio
    async def test_the_registered_factory_is_used_by_default(self) -> None:
        factory = _Factory(llm=_make_fake_llm("from the port"))
        set_agent_runtime_factory(factory)
        tool = CallAgentTool(available_agents=_make_agent_catalog(), call_context=_ctx())

        assert await tool.run(agent_id="agent-1", task="x") == "from the port"

    @pytest.mark.asyncio
    async def test_run_agent_not_found(self) -> None:
        factory = _Factory()
        result = await _tool(factory).run(agent_id="nonexistent", task="test")
        assert "Error: agent nonexistent not found" in result
        assert factory.asked == []  # only the catalogue can be delegated to

    @pytest.mark.asyncio
    async def test_an_agent_that_is_no_longer_usable_is_reported_not_run(self) -> None:
        factory = _Factory(
            raises=AssemblyRefusal("resource_inactive", 409, "Agent 'Helper' is disabled")
        )
        result = await _tool(factory).run(agent_id="agent-1", task="test")
        assert result == "Error: agent agent-1 is not available: Agent 'Helper' is disabled"

    @pytest.mark.asyncio
    async def test_a_runtime_that_cannot_be_built_aborts_the_delegation(self) -> None:
        """Fail closed: no model or no hooks means no run, not a stripped run."""
        factory = _Factory(raises=RuntimeError("db down"))
        result = await _tool(factory).run(agent_id="agent-1", task="do it")
        assert "delegation aborted" in result
        assert "db down" not in result

    @pytest.mark.asyncio
    async def test_run_excludes_call_agent_from_delegate_tools(self) -> None:
        """Delegated agent tools must not include call_agent (recursion prevention)."""
        from fim_one.core.agent import react

        delegate_tools = ToolRegistry()
        delegate_tools.register(_tool(_Factory()))
        delegate_tools.register(_RecordingTool())
        built: list[dict[str, Any]] = []

        class _Recording(react.ReActAgent):
            def __init__(self, **kwargs: Any) -> None:
                built.append(kwargs)
                super().__init__(**kwargs)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(react, "ReActAgent", _Recording)
            result = await _tool(
                _Factory(llm=_make_fake_llm("done"), tools=delegate_tools)
            ).run(agent_id="agent-1", task="test")

        assert "done" in result
        names = {t.name for t in built[0]["tools"].list_tools()}
        assert "danger" in names and "call_agent" not in names
        assert built[0]["extra_instructions"] == "Be helpful."

    @pytest.mark.asyncio
    async def test_a_delegate_that_fails_still_releases_its_connections(self) -> None:
        class _Broken(FakeLLM):
            async def chat(self, *_a: Any, **_k: Any) -> Any:
                raise RuntimeError("model unavailable")

            async def stream_chat(self, *_a: Any, **_k: Any) -> Any:  # type: ignore[override]
                raise RuntimeError("model unavailable")
                yield  # pragma: no cover

        factory = _Factory(llm=_Broken(responses=[]))
        result = await _tool(factory).run(agent_id="agent-1", task="test")
        assert isinstance(result, str) and result
        assert factory.client.closed == 1


# ---------------------------------------------------------------------------
# Hook inheritance tests
# ---------------------------------------------------------------------------


class _RecordingTool(BaseTool):
    """A tool that records whether it was ever executed."""

    def __init__(self) -> None:
        self.ran = False

    @property
    def name(self) -> str:
        return "danger"

    @property
    def description(self) -> str:
        return "A sensitive action"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def run(self, **kwargs: Any) -> str:
        self.ran = True
        return "executed"


def _delegate_llm_calling(tool_name: str) -> FakeLLM:
    """A FakeLLM that calls ``tool_name`` once, then answers."""
    return FakeLLM(
        responses=[
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps(
                        {
                            "type": "tool_call",
                            "reasoning": "Do the thing",
                            "tool_name": tool_name,
                            "tool_args": {},
                        }
                    ),
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps(
                        {
                            "type": "final_answer",
                            "reasoning": "Done",
                            "answer": "finished",
                        }
                    ),
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ]
    )


class TestHookInheritance:
    """A delegated agent must run its own enforcement hooks.

    Regression cover for the gap documented in ``dev/hook-system.md``: a
    delegate spun up with an empty registry (and no ``agent_id``) let a
    sensitive call skip the confirmation gate purely by being reached
    through ``call_agent``.
    """

    @pytest.mark.asyncio
    async def test_pre_hook_blocks_delegated_tool_call(self) -> None:
        """A deny hook of the delegate's runtime blocks its tool call."""
        danger = _RecordingTool()
        delegate_tools = ToolRegistry()
        delegate_tools.register(danger)

        registry = HookRegistry()

        async def _deny(_ctx: HookContext) -> HookResult:
            return HookResult(allow=False, error="blocked by gate")

        registry.register(Hook("gate", HookPoint.PRE_TOOL_USE, _deny))

        tool = _tool(
            _Factory(llm=_delegate_llm_calling("danger"), tools=delegate_tools, hooks=registry)
        )

        result = await tool.run(agent_id="agent-1", task="do it")

        assert danger.ran is False, "hook did not block the delegated tool call"
        assert "finished" in result

    @pytest.mark.asyncio
    async def test_delegate_receives_agent_and_user_context(self) -> None:
        """agent_id/user_id reach the hook — without them the gate bows out."""
        seen: list[HookContext] = []

        async def _observe(ctx: HookContext) -> HookResult:
            seen.append(ctx)
            return HookResult()

        registry = HookRegistry()
        registry.register(Hook("observer", HookPoint.PRE_TOOL_USE, _observe))

        delegate_tools = ToolRegistry()
        delegate_tools.register(_RecordingTool())

        tool = CallAgentTool(
            available_agents=_make_agent_catalog(agent_id="agent-42"),
            call_context=_ctx("user-7"),
            runtime_factory=_Factory(
                llm=_delegate_llm_calling("danger"), tools=delegate_tools, hooks=registry
            ),
        )

        await tool.run(agent_id="agent-42", task="do it")

        assert len(seen) == 1
        assert seen[0].agent_id == "agent-42", "gate would skip without agent_id"
        assert seen[0].user_id == "user-7"


class TestDelegateResultShape:
    """The caller gets the delegate's answer, never its ``AgentResult`` repr.

    ``return str(result)`` on a dataclass renders the entire trajectory —
    every StepResult, observation, and message — so the caller's context
    filled with trace instead of the answer.  Substring assertions could
    not catch it: the answer is inside the repr too.
    """

    @pytest.mark.asyncio
    async def test_returns_exactly_the_answer(self) -> None:
        result = await _tool(_Factory(llm=_make_fake_llm("the answer"))).run(
            agent_id="agent-1", task="test"
        )

        assert result == "the answer"

    @pytest.mark.asyncio
    async def test_no_dataclass_repr_leaks(self) -> None:
        result = await _tool(_Factory(llm=_make_fake_llm("clean"))).run(
            agent_id="agent-1", task="test"
        )

        for marker in ("AgentResult(", "StepResult(", "Action(", "steps=", "messages="):
            assert marker not in result

    @pytest.mark.asyncio
    async def test_empty_answer_reports_instead_of_returning_blank(self) -> None:
        """An answerless run must say so, not hand back an empty string."""
        result = await _tool(_Factory(llm=_make_fake_llm(""))).run(
            agent_id="agent-1", task="test"
        )

        assert result.strip()
        assert "without producing a textual answer" in result
