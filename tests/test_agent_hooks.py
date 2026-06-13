"""Tests for the Agent Hook System.

Covers:
- Hook registration and ordering
- PRE_TOOL_USE can block calls
- PRE_TOOL_USE can modify args
- POST_TOOL_USE can modify results
- Result truncation hook
- Rate limiter hook
- Connector call logger hook
- Hook filtering by tool name
- Multiple hooks run in priority order
- Hooks don't crash the agent on error
- HookRegistry integration with ReActAgent (JSON + native modes)
- Built-in hook factory and config builder
"""

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
from fim_one.core.agent.builtin_hooks import (
    BUILTIN_HOOKS,
    MAX_RESULT_LENGTH,
    RATE_LIMIT_MAX_CALLS,
    build_hook_registry_from_config,
    create_builtin_hook,
    create_connector_call_logger,
    create_rate_limiter,
    create_result_truncator,
    reset_rate_limits,
)
from fim_one.core.agent import ReActAgent
from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.model.types import ToolCallRequest
from fim_one.core.tool import BaseTool, ToolRegistry

from .conftest import EchoTool, FakeLLM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_handler(ctx: HookContext) -> HookResult:
    """A no-op hook handler."""
    return HookResult(side_effects=["noop"])


async def _blocking_handler(ctx: HookContext) -> HookResult:
    """A PRE hook that blocks the tool call."""
    return HookResult(allow=False, error="Blocked by test hook")


async def _args_modifier_handler(ctx: HookContext) -> HookResult:
    """A PRE hook that modifies tool args."""
    new_args = dict(ctx.tool_args or {})
    new_args["injected"] = "by_hook"
    return HookResult(modified_args=new_args)


async def _result_modifier_handler(ctx: HookContext) -> HookResult:
    """A POST hook that modifies the tool result."""
    result = ctx.tool_result or ""
    return HookResult(modified_result=f"[modified] {result}")


async def _crashing_handler(ctx: HookContext) -> HookResult:
    """A hook that raises an exception."""
    raise RuntimeError("Hook crashed intentionally")


class CaptureTool(BaseTool):
    """Tool that captures kwargs for inspection."""

    def __init__(self, name: str = "capture") -> None:
        self._name_val = name
        self.last_kwargs: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return self._name_val

    @property
    def description(self) -> str:
        return "Captures kwargs"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "injected": {"type": "string"},
            },
        }

    async def run(self, **kwargs: Any) -> str:
        self.last_kwargs = kwargs
        return f"captured: {kwargs}"


# ---------------------------------------------------------------------------
# Hook Registration and Ordering
# ---------------------------------------------------------------------------


class TestHookRegistration:
    def test_register_and_list(self) -> None:
        registry = HookRegistry()
        hook = Hook("test", HookPoint.PRE_TOOL_USE, _noop_handler)
        registry.register(hook)

        hooks = registry.list_hooks(HookPoint.PRE_TOOL_USE)
        assert len(hooks) == 1
        assert hooks[0].name == "test"

    def test_list_all_hooks(self) -> None:
        registry = HookRegistry()
        registry.register(Hook("pre1", HookPoint.PRE_TOOL_USE, _noop_handler))
        registry.register(Hook("post1", HookPoint.POST_TOOL_USE, _noop_handler))
        registry.register(Hook("session1", HookPoint.SESSION_START, _noop_handler))

        all_hooks = registry.list_hooks()
        assert len(all_hooks) == 3

    def test_priority_ordering(self) -> None:
        registry = HookRegistry()
        registry.register(Hook("high", HookPoint.PRE_TOOL_USE, _noop_handler, priority=10))
        registry.register(Hook("low", HookPoint.PRE_TOOL_USE, _noop_handler, priority=1))
        registry.register(Hook("mid", HookPoint.PRE_TOOL_USE, _noop_handler, priority=5))

        hooks = registry.list_hooks(HookPoint.PRE_TOOL_USE)
        assert [h.name for h in hooks] == ["low", "mid", "high"]

    def test_unregister(self) -> None:
        registry = HookRegistry()
        registry.register(Hook("a", HookPoint.PRE_TOOL_USE, _noop_handler))
        registry.register(Hook("b", HookPoint.PRE_TOOL_USE, _noop_handler))
        registry.unregister("a")

        hooks = registry.list_hooks(HookPoint.PRE_TOOL_USE)
        assert len(hooks) == 1
        assert hooks[0].name == "b"

    def test_len(self) -> None:
        registry = HookRegistry()
        assert len(registry) == 0
        registry.register(Hook("a", HookPoint.PRE_TOOL_USE, _noop_handler))
        registry.register(Hook("b", HookPoint.POST_TOOL_USE, _noop_handler))
        assert len(registry) == 2


# ---------------------------------------------------------------------------
# PRE_TOOL_USE Hooks
# ---------------------------------------------------------------------------


class TestPreToolHooks:
    @pytest.mark.asyncio
    async def test_block_tool_call(self) -> None:
        registry = HookRegistry()
        registry.register(
            Hook("blocker", HookPoint.PRE_TOOL_USE, _blocking_handler)
        )

        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="some_tool",
            tool_args={"key": "value"},
        )
        result = await registry.run_pre_tool(ctx)

        assert not result.allow
        assert result.error == "Blocked by test hook"

    @pytest.mark.asyncio
    async def test_modify_args(self) -> None:
        registry = HookRegistry()
        registry.register(
            Hook("modifier", HookPoint.PRE_TOOL_USE, _args_modifier_handler)
        )

        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="some_tool",
            tool_args={"text": "hello"},
        )
        result = await registry.run_pre_tool(ctx)

        assert result.allow
        assert result.modified_args is not None
        assert result.modified_args["text"] == "hello"
        assert result.modified_args["injected"] == "by_hook"

    @pytest.mark.asyncio
    async def test_chained_modification(self) -> None:
        """When multiple PRE hooks modify args, the chain is carried forward."""

        async def add_field_a(ctx: HookContext) -> HookResult:
            args = dict(ctx.tool_args or {})
            args["field_a"] = "from_hook_a"
            return HookResult(modified_args=args)

        async def add_field_b(ctx: HookContext) -> HookResult:
            args = dict(ctx.tool_args or {})
            args["field_b"] = "from_hook_b"
            return HookResult(modified_args=args)

        registry = HookRegistry()
        registry.register(Hook("a", HookPoint.PRE_TOOL_USE, add_field_a, priority=1))
        registry.register(Hook("b", HookPoint.PRE_TOOL_USE, add_field_b, priority=2))

        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="test",
            tool_args={"original": "yes"},
        )
        result = await registry.run_pre_tool(ctx)

        assert result.allow
        assert result.modified_args is not None
        assert result.modified_args["original"] == "yes"
        assert result.modified_args["field_a"] == "from_hook_a"
        assert result.modified_args["field_b"] == "from_hook_b"

    @pytest.mark.asyncio
    async def test_block_stops_chain(self) -> None:
        """When a hook blocks, subsequent hooks don't run."""
        call_log: list[str] = []

        async def first_hook(ctx: HookContext) -> HookResult:
            call_log.append("first")
            return HookResult(allow=False, error="blocked")

        async def second_hook(ctx: HookContext) -> HookResult:
            call_log.append("second")
            return HookResult()

        registry = HookRegistry()
        registry.register(Hook("first", HookPoint.PRE_TOOL_USE, first_hook, priority=1))
        registry.register(Hook("second", HookPoint.PRE_TOOL_USE, second_hook, priority=2))

        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="test",
            tool_args={},
        )
        result = await registry.run_pre_tool(ctx)

        assert not result.allow
        assert call_log == ["first"]


# ---------------------------------------------------------------------------
# POST_TOOL_USE Hooks
# ---------------------------------------------------------------------------


class TestPostToolHooks:
    @pytest.mark.asyncio
    async def test_modify_result(self) -> None:
        registry = HookRegistry()
        registry.register(
            Hook("modifier", HookPoint.POST_TOOL_USE, _result_modifier_handler)
        )

        ctx = HookContext(
            hook_point=HookPoint.POST_TOOL_USE,
            tool_name="some_tool",
            tool_result="original result",
        )
        result = await registry.run_post_tool(ctx)

        assert result.modified_result == "[modified] original result"

    @pytest.mark.asyncio
    async def test_chained_result_modification(self) -> None:
        """Multiple POST hooks each see the modified result from the previous hook."""

        async def wrap_a(ctx: HookContext) -> HookResult:
            return HookResult(modified_result=f"[A]{ctx.tool_result}[/A]")

        async def wrap_b(ctx: HookContext) -> HookResult:
            return HookResult(modified_result=f"[B]{ctx.tool_result}[/B]")

        registry = HookRegistry()
        registry.register(Hook("a", HookPoint.POST_TOOL_USE, wrap_a, priority=1))
        registry.register(Hook("b", HookPoint.POST_TOOL_USE, wrap_b, priority=2))

        ctx = HookContext(
            hook_point=HookPoint.POST_TOOL_USE,
            tool_name="test",
            tool_result="data",
        )
        result = await registry.run_post_tool(ctx)

        assert result.modified_result == "[B][A]data[/A][/B]"


# ---------------------------------------------------------------------------
# Tool Name Filtering
# ---------------------------------------------------------------------------


class TestToolFiltering:
    def test_glob_match(self) -> None:
        hook = Hook(
            "github_only",
            HookPoint.PRE_TOOL_USE,
            _noop_handler,
            tool_filter="github__*",
        )
        assert hook.matches_tool("github__list_repos")
        assert hook.matches_tool("github__create_issue")
        assert not hook.matches_tool("slack__send_message")
        assert not hook.matches_tool("echo")

    def test_no_filter_matches_all(self) -> None:
        hook = Hook("all", HookPoint.PRE_TOOL_USE, _noop_handler)
        assert hook.matches_tool("anything")
        assert hook.matches_tool("github__repos")
        # No filter = match everything, including None tool names.
        assert hook.matches_tool(None) is True

    def test_connector_filter(self) -> None:
        hook = Hook(
            "connectors",
            HookPoint.PRE_TOOL_USE,
            _noop_handler,
            tool_filter="*__*",
        )
        assert hook.matches_tool("github__list_repos")
        assert hook.matches_tool("slack__post")
        assert not hook.matches_tool("echo")
        assert not hook.matches_tool("python_exec")

    @pytest.mark.asyncio
    async def test_filtered_hook_skipped(self) -> None:
        """Hook with filter skips non-matching tools."""
        call_log: list[str] = []

        async def logging_hook(ctx: HookContext) -> HookResult:
            call_log.append(ctx.tool_name or "")
            return HookResult()

        registry = HookRegistry()
        registry.register(
            Hook(
                "connector_only",
                HookPoint.PRE_TOOL_USE,
                logging_hook,
                tool_filter="*__*",
            )
        )

        # Non-connector tool — should be skipped.
        ctx1 = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="echo",
            tool_args={},
        )
        await registry.run_pre_tool(ctx1)
        assert call_log == []

        # Connector tool — should fire.
        ctx2 = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="github__repos",
            tool_args={},
        )
        await registry.run_pre_tool(ctx2)
        assert call_log == ["github__repos"]


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------


class TestHookErrorHandling:
    @pytest.mark.asyncio
    async def test_crashing_pre_hook_fails_closed_by_default(self) -> None:
        """A PRE hook that raises must BLOCK the call (fail-closed).

        Enforcement hooks — security gates, human approvals — run at
        PRE_TOOL_USE. If such a hook crashes, silently allowing the tool
        through would turn a broken gate into a bypass, so the default is to
        deny.
        """
        hook = Hook("crasher", HookPoint.PRE_TOOL_USE, _crashing_handler)
        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="test",
            tool_args={},
        )
        result = await hook.execute(ctx)
        assert result.allow is False
        assert result.error is not None
        assert any("crash" in s.lower() or "failed" in s.lower() for s in result.side_effects)

    @pytest.mark.asyncio
    async def test_crashing_pre_hook_fail_open_opt_in(self) -> None:
        """A non-enforcement PRE hook can opt into fail-open via ``fail_open=True``.

        Loggers and rate-limiters are not security boundaries; if they crash
        they should not take the agent down.
        """
        hook = Hook(
            "crasher",
            HookPoint.PRE_TOOL_USE,
            _crashing_handler,
            fail_open=True,
        )
        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="test",
            tool_args={},
        )
        result = await hook.execute(ctx)
        assert result.allow is True
        assert any("failed" in s.lower() for s in result.side_effects)

    @pytest.mark.asyncio
    async def test_crashing_post_hook_does_not_block(self) -> None:
        """A POST hook that raises leaves the already-produced result intact."""
        hook = Hook("crasher", HookPoint.POST_TOOL_USE, _crashing_handler)
        ctx = HookContext(
            hook_point=HookPoint.POST_TOOL_USE,
            tool_name="test",
            tool_result="original",
        )
        result = await hook.execute(ctx)
        # POST hooks cannot block execution; the result is simply not modified.
        assert result.modified_result is None

    @pytest.mark.asyncio
    async def test_crashing_pre_hook_blocks_chain(self) -> None:
        """A crashing enforcement PRE hook blocks the call and stops the chain."""
        call_log: list[str] = []

        async def good_hook(ctx: HookContext) -> HookResult:
            call_log.append("good")
            return HookResult()

        registry = HookRegistry()
        registry.register(
            Hook("crasher", HookPoint.PRE_TOOL_USE, _crashing_handler, priority=1)
        )
        registry.register(Hook("good", HookPoint.PRE_TOOL_USE, good_hook, priority=2))

        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="test",
            tool_args={},
        )
        result = await registry.run_pre_tool(ctx)

        # Fail-closed: the call is blocked and the later hook never runs.
        assert result.allow is False
        assert call_log == []

    @pytest.mark.asyncio
    async def test_fail_open_pre_hook_does_not_block_chain(self) -> None:
        """A crashing fail-open PRE hook is skipped; subsequent hooks still run."""
        call_log: list[str] = []

        async def good_hook(ctx: HookContext) -> HookResult:
            call_log.append("good")
            return HookResult()

        registry = HookRegistry()
        registry.register(
            Hook(
                "crasher",
                HookPoint.PRE_TOOL_USE,
                _crashing_handler,
                priority=1,
                fail_open=True,
            )
        )
        registry.register(Hook("good", HookPoint.PRE_TOOL_USE, good_hook, priority=2))

        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="test",
            tool_args={},
        )
        result = await registry.run_pre_tool(ctx)

        assert result.allow is True
        assert call_log == ["good"]


# ---------------------------------------------------------------------------
# Session Start Hook
# ---------------------------------------------------------------------------


class TestSessionStartHooks:
    @pytest.mark.asyncio
    async def test_session_start(self) -> None:
        call_log: list[str] = []

        async def session_hook(ctx: HookContext) -> HookResult:
            call_log.append(f"started:{ctx.agent_id}")
            return HookResult(side_effects=["session initialized"])

        registry = HookRegistry()
        registry.register(
            Hook("session", HookPoint.SESSION_START, session_hook)
        )

        ctx = HookContext(
            hook_point=HookPoint.SESSION_START,
            agent_id="agent-123",
        )
        result = await registry.run_session_start(ctx)

        assert call_log == ["started:agent-123"]
        assert "session initialized" in result.side_effects


# ---------------------------------------------------------------------------
# Built-in Hooks
# ---------------------------------------------------------------------------


class TestResultTruncatorHook:
    @pytest.mark.asyncio
    async def test_short_result_not_truncated(self) -> None:
        hook = create_result_truncator()
        registry = HookRegistry()
        registry.register(hook)

        ctx = HookContext(
            hook_point=HookPoint.POST_TOOL_USE,
            tool_name="test",
            tool_result="short result",
        )
        result = await registry.run_post_tool(ctx)
        assert result.modified_result is None

    @pytest.mark.asyncio
    async def test_long_result_truncated(self) -> None:
        hook = create_result_truncator()
        registry = HookRegistry()
        registry.register(hook)

        long_text = "x" * (MAX_RESULT_LENGTH + 1000)
        ctx = HookContext(
            hook_point=HookPoint.POST_TOOL_USE,
            tool_name="test",
            tool_result=long_text,
        )
        result = await registry.run_post_tool(ctx)

        assert result.modified_result is not None
        assert len(result.modified_result) < len(long_text)
        assert "[truncated" in result.modified_result
        assert f"{len(long_text)} chars" in result.modified_result

    @pytest.mark.asyncio
    async def test_none_result_unchanged(self) -> None:
        hook = create_result_truncator()
        registry = HookRegistry()
        registry.register(hook)

        ctx = HookContext(
            hook_point=HookPoint.POST_TOOL_USE,
            tool_name="test",
            tool_result=None,
        )
        result = await registry.run_post_tool(ctx)
        assert result.modified_result is None


class TestRateLimiterHook:
    @pytest.fixture(autouse=True)
    def _reset_limits(self) -> None:
        reset_rate_limits()

    @pytest.mark.asyncio
    async def test_allows_under_limit(self) -> None:
        hook = create_rate_limiter()
        registry = HookRegistry()
        registry.register(hook)

        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="github__repos",
            tool_args={},
        )

        for _ in range(RATE_LIMIT_MAX_CALLS):
            result = await registry.run_pre_tool(ctx)
            assert result.allow

    @pytest.mark.asyncio
    async def test_blocks_over_limit(self) -> None:
        hook = create_rate_limiter()
        registry = HookRegistry()
        registry.register(hook)

        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="github__repos",
            tool_args={},
        )

        # Fill up the limit.
        for _ in range(RATE_LIMIT_MAX_CALLS):
            await registry.run_pre_tool(ctx)

        # Next call should be blocked.
        result = await registry.run_pre_tool(ctx)
        assert not result.allow
        assert "Rate limit exceeded" in (result.error or "")

    @pytest.mark.asyncio
    async def test_non_connector_not_rate_limited(self) -> None:
        hook = create_rate_limiter()
        registry = HookRegistry()
        registry.register(hook)

        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="echo",  # Not a connector tool.
            tool_args={},
        )

        # Should not be rate-limited at all.
        for _ in range(RATE_LIMIT_MAX_CALLS + 5):
            result = await registry.run_pre_tool(ctx)
            assert result.allow


class TestConnectorCallLoggerHook:
    @pytest.mark.asyncio
    async def test_logs_connector_calls(self) -> None:
        hook = create_connector_call_logger()
        registry = HookRegistry()
        registry.register(hook)

        ctx = HookContext(
            hook_point=HookPoint.POST_TOOL_USE,
            tool_name="github__list_repos",
            tool_args={"org": "test"},
            tool_result='[{"name": "repo1"}]',
            agent_id="agent-1",
            user_id="user-1",
        )
        result = await registry.run_post_tool(ctx)

        assert any("Logged connector call" in s for s in result.side_effects)
        assert result.modified_result is None  # Logger does not modify results.

    @pytest.mark.asyncio
    async def test_skips_non_connector_tools(self) -> None:
        hook = create_connector_call_logger()
        registry = HookRegistry()
        registry.register(hook)

        ctx = HookContext(
            hook_point=HookPoint.POST_TOOL_USE,
            tool_name="echo",
            tool_result="hello",
        )
        result = await registry.run_post_tool(ctx)

        # Should not log anything (filter is *__*).
        assert not result.side_effects


# ---------------------------------------------------------------------------
# Built-in Hook Factory
# ---------------------------------------------------------------------------


class TestBuiltinHookFactory:
    def test_known_hooks(self) -> None:
        for name in BUILTIN_HOOKS:
            hook = create_builtin_hook(name)
            assert hook is not None
            assert hook.name == name

    def test_unknown_hook_returns_none(self) -> None:
        assert create_builtin_hook("nonexistent") is None

    def test_build_registry_from_config(self) -> None:
        config = {
            "builtin": ["result_truncator", "rate_limiter"],
        }
        registry = build_hook_registry_from_config(config)
        assert registry is not None
        assert len(registry) == 2

    def test_build_registry_from_empty_config(self) -> None:
        assert build_hook_registry_from_config(None) is None
        assert build_hook_registry_from_config({}) is None
        assert build_hook_registry_from_config({"builtin": []}) is None

    def test_build_registry_skips_unknown(self) -> None:
        config = {
            "builtin": ["result_truncator", "fake_hook"],
        }
        registry = build_hook_registry_from_config(config)
        assert registry is not None
        assert len(registry) == 1


# ---------------------------------------------------------------------------
# Integration with ReActAgent (JSON mode)
# ---------------------------------------------------------------------------


class TestReActAgentHooksJsonMode:
    @pytest.mark.asyncio
    async def test_pre_hook_blocks_tool_call(self) -> None:
        """PRE hook blocks a tool call, agent receives the error."""
        registry = HookRegistry()
        registry.register(
            Hook("blocker", HookPoint.PRE_TOOL_USE, _blocking_handler)
        )

        # LLM response: call echo tool, then final answer.
        responses = [
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps({
                        "type": "tool_call",
                        "reasoning": "Call echo",
                        "tool_name": "echo",
                        "tool_args": {"text": "hello"},
                    }),
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps({
                        "type": "final_answer",
                        "reasoning": "Done",
                        "answer": "done",
                    }),
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ]

        llm = FakeLLM(responses)
        tool_reg = ToolRegistry()
        tool_reg.register(EchoTool())

        agent = ReActAgent(
            llm=llm,
            tools=tool_reg,
            max_iterations=5,
            hook_registry=registry,
        )

        result = await agent.run("test")

        # The tool call should have been blocked.
        assert len(result.steps) >= 1
        step = result.steps[0]
        assert step.error is not None
        assert "Blocked by test hook" in step.error

    @pytest.mark.asyncio
    async def test_pre_hook_modifies_args(self) -> None:
        """PRE hook modifies args, tool receives modified args."""
        registry = HookRegistry()
        registry.register(
            Hook("modifier", HookPoint.PRE_TOOL_USE, _args_modifier_handler)
        )

        capture_tool = CaptureTool()

        responses = [
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps({
                        "type": "tool_call",
                        "reasoning": "Call capture",
                        "tool_name": "capture",
                        "tool_args": {"text": "original"},
                    }),
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps({
                        "type": "final_answer",
                        "reasoning": "Done",
                        "answer": "done",
                    }),
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ]

        llm = FakeLLM(responses)
        tool_reg = ToolRegistry()
        tool_reg.register(capture_tool)

        agent = ReActAgent(
            llm=llm,
            tools=tool_reg,
            max_iterations=5,
            hook_registry=registry,
        )

        await agent.run("test")

        # The capture tool should have received the injected arg.
        assert capture_tool.last_kwargs.get("injected") == "by_hook"
        assert capture_tool.last_kwargs.get("text") == "original"

    @pytest.mark.asyncio
    async def test_post_hook_modifies_result(self) -> None:
        """POST hook modifies the tool result, agent sees modified observation."""
        registry = HookRegistry()
        registry.register(
            Hook("modifier", HookPoint.POST_TOOL_USE, _result_modifier_handler)
        )

        responses = [
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps({
                        "type": "tool_call",
                        "reasoning": "Call echo",
                        "tool_name": "echo",
                        "tool_args": {"text": "hello"},
                    }),
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps({
                        "type": "final_answer",
                        "reasoning": "Done",
                        "answer": "done",
                    }),
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ]

        llm = FakeLLM(responses)
        tool_reg = ToolRegistry()
        tool_reg.register(EchoTool())

        agent = ReActAgent(
            llm=llm,
            tools=tool_reg,
            max_iterations=5,
            hook_registry=registry,
        )

        result = await agent.run("test")

        # The observation should be modified by the POST hook.
        step = result.steps[0]
        assert step.observation is not None
        assert step.observation.startswith("[modified]")

    @pytest.mark.asyncio
    async def test_no_hooks_baseline(self) -> None:
        """Without hooks, agent works normally."""
        responses = [
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps({
                        "type": "tool_call",
                        "reasoning": "Call echo",
                        "tool_name": "echo",
                        "tool_args": {"text": "hello"},
                    }),
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps({
                        "type": "final_answer",
                        "reasoning": "Done",
                        "answer": "done",
                    }),
                ),
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ]

        llm = FakeLLM(responses)
        tool_reg = ToolRegistry()
        tool_reg.register(EchoTool())

        agent = ReActAgent(
            llm=llm,
            tools=tool_reg,
            max_iterations=5,
            hook_registry=None,
        )

        result = await agent.run("test")

        step = result.steps[0]
        assert step.observation == "hello"
        assert step.error is None


# ---------------------------------------------------------------------------
# Integration with ReActAgent (Native function-calling mode)
# ---------------------------------------------------------------------------


class TestReActAgentHooksNativeMode:
    @pytest.mark.asyncio
    async def test_native_pre_hook_blocks(self) -> None:
        """PRE hook blocks a native tool call."""
        registry = HookRegistry()
        registry.register(
            Hook("blocker", HookPoint.PRE_TOOL_USE, _blocking_handler)
        )

        # LLM returns a tool_call via native interface, then final answer.
        tool_call_response = LLMResult(
            message=ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="echo",
                        arguments={"text": "hello"},
                    ),
                ],
            ),
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        final_response = LLMResult(
            message=ChatMessage(
                role="assistant",
                content="done",
            ),
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

        llm = FakeLLM(
            [tool_call_response, final_response],
            abilities={"tool_call": True, "json_mode": False, "vision": False, "streaming": False},
        )
        tool_reg = ToolRegistry()
        tool_reg.register(EchoTool())

        agent = ReActAgent(
            llm=llm,
            tools=tool_reg,
            max_iterations=5,
            use_native_tools=True,
            hook_registry=registry,
        )

        result = await agent.run("test")

        # The tool call step should show it was blocked.
        tool_steps = [s for s in result.steps if s.action.type == "tool_call"]
        assert len(tool_steps) >= 1
        assert tool_steps[0].error is not None
        assert "Blocked by test hook" in tool_steps[0].error

    @pytest.mark.asyncio
    async def test_native_post_hook_modifies_result(self) -> None:
        """POST hook modifies native tool call result."""
        registry = HookRegistry()
        registry.register(
            Hook("modifier", HookPoint.POST_TOOL_USE, _result_modifier_handler)
        )

        tool_call_response = LLMResult(
            message=ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="echo",
                        arguments={"text": "hello"},
                    ),
                ],
            ),
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        final_response = LLMResult(
            message=ChatMessage(
                role="assistant",
                content="done",
            ),
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

        llm = FakeLLM(
            [tool_call_response, final_response],
            abilities={"tool_call": True, "json_mode": False, "vision": False, "streaming": False},
        )
        tool_reg = ToolRegistry()
        tool_reg.register(EchoTool())

        agent = ReActAgent(
            llm=llm,
            tools=tool_reg,
            max_iterations=5,
            use_native_tools=True,
            hook_registry=registry,
        )

        result = await agent.run("test")

        tool_steps = [s for s in result.steps if s.action.type == "tool_call"]
        assert len(tool_steps) >= 1
        assert tool_steps[0].observation is not None
        assert tool_steps[0].observation.startswith("[modified]")


# ---------------------------------------------------------------------------
# Multiple Hooks in Priority Order
# ---------------------------------------------------------------------------


class TestMultipleHooksPriorityOrder:
    @pytest.mark.asyncio
    async def test_hooks_run_in_priority_order(self) -> None:
        """Verify hooks execute in ascending priority order."""
        execution_order: list[str] = []

        async def hook_a(ctx: HookContext) -> HookResult:
            execution_order.append("A")
            return HookResult()

        async def hook_b(ctx: HookContext) -> HookResult:
            execution_order.append("B")
            return HookResult()

        async def hook_c(ctx: HookContext) -> HookResult:
            execution_order.append("C")
            return HookResult()

        registry = HookRegistry()
        registry.register(Hook("c", HookPoint.PRE_TOOL_USE, hook_c, priority=30))
        registry.register(Hook("a", HookPoint.PRE_TOOL_USE, hook_a, priority=10))
        registry.register(Hook("b", HookPoint.PRE_TOOL_USE, hook_b, priority=20))

        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="test",
            tool_args={},
        )
        await registry.run_pre_tool(ctx)

        assert execution_order == ["A", "B", "C"]

    @pytest.mark.asyncio
    async def test_side_effects_aggregated(self) -> None:
        """Side effects from all hooks are collected."""

        async def hook_1(ctx: HookContext) -> HookResult:
            return HookResult(side_effects=["effect_1"])

        async def hook_2(ctx: HookContext) -> HookResult:
            return HookResult(side_effects=["effect_2"])

        registry = HookRegistry()
        registry.register(Hook("h1", HookPoint.PRE_TOOL_USE, hook_1, priority=1))
        registry.register(Hook("h2", HookPoint.PRE_TOOL_USE, hook_2, priority=2))

        ctx = HookContext(
            hook_point=HookPoint.PRE_TOOL_USE,
            tool_name="test",
            tool_args={},
        )
        result = await registry.run_pre_tool(ctx)

        assert "effect_1" in result.side_effects
        assert "effect_2" in result.side_effects
