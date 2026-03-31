"""Tests for the Model layer (types, base, openai_compatible)."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.model import (
    BaseLLM,
    ChatMessage,
    LLMResult,
    OpenAICompatibleLLM,
    StreamChunk,
    ToolCallRequest,
)
from fim_one.core.model.openai_compatible import _resolve_litellm_model


# ======================================================================
# ChatMessage.to_openai_dict
# ======================================================================


class TestChatMessageToOpenAIDict:
    """Verify ``ChatMessage.to_openai_dict()`` serialisation."""

    def test_basic_user_message(self) -> None:
        msg = ChatMessage(role="user", content="Hello")
        d = msg.to_openai_dict()
        assert d == {"role": "user", "content": "Hello"}

    def test_system_message(self) -> None:
        msg = ChatMessage(role="system", content="You are helpful.")
        d = msg.to_openai_dict()
        assert d == {"role": "system", "content": "You are helpful."}

    def test_assistant_message_no_content(self) -> None:
        """Assistant message with content=None should not include 'content' key."""
        msg = ChatMessage(role="assistant")
        d = msg.to_openai_dict()
        assert d == {"role": "assistant"}
        assert "content" not in d

    def test_message_with_tool_calls(self) -> None:
        tc = ToolCallRequest(id="call_1", name="my_tool", arguments={"x": 1})
        msg = ChatMessage(role="assistant", tool_calls=[tc])
        d = msg.to_openai_dict()

        assert d["role"] == "assistant"
        assert len(d["tool_calls"]) == 1

        tc_dict = d["tool_calls"][0]
        assert tc_dict["id"] == "call_1"
        assert tc_dict["type"] == "function"
        assert tc_dict["function"]["name"] == "my_tool"
        assert json.loads(tc_dict["function"]["arguments"]) == {"x": 1}

    def test_message_with_multiple_tool_calls(self) -> None:
        tc1 = ToolCallRequest(id="call_1", name="tool_a", arguments={"a": 1})
        tc2 = ToolCallRequest(id="call_2", name="tool_b", arguments={"b": 2})
        msg = ChatMessage(role="assistant", tool_calls=[tc1, tc2])
        d = msg.to_openai_dict()
        assert len(d["tool_calls"]) == 2
        assert d["tool_calls"][0]["function"]["name"] == "tool_a"
        assert d["tool_calls"][1]["function"]["name"] == "tool_b"

    def test_message_with_tool_call_id(self) -> None:
        msg = ChatMessage(
            role="tool",
            content="result data",
            tool_call_id="call_1",
        )
        d = msg.to_openai_dict()
        assert d["role"] == "tool"
        assert d["content"] == "result data"
        assert d["tool_call_id"] == "call_1"

    def test_message_with_name(self) -> None:
        msg = ChatMessage(role="tool", content="ok", name="my_fn")
        d = msg.to_openai_dict()
        assert d["name"] == "my_fn"

    def test_message_empty_tool_calls_list_omitted(self) -> None:
        """An empty tool_calls list should NOT produce a 'tool_calls' key."""
        msg = ChatMessage(role="assistant", content="hi", tool_calls=[])
        d = msg.to_openai_dict()
        assert "tool_calls" not in d

    def test_all_fields_combined(self) -> None:
        tc = ToolCallRequest(id="c1", name="fn", arguments={})
        msg = ChatMessage(
            role="assistant",
            content="thinking",
            tool_calls=[tc],
            tool_call_id="c0",
            name="helper",
        )
        d = msg.to_openai_dict()
        assert d["role"] == "assistant"
        assert d["content"] == "thinking"
        assert d["tool_call_id"] == "c0"
        assert d["name"] == "helper"
        assert len(d["tool_calls"]) == 1


# ======================================================================
# ToolCallRequest
# ======================================================================


class TestToolCallRequest:
    """Verify ``ToolCallRequest`` dataclass creation."""

    def test_creation(self) -> None:
        tc = ToolCallRequest(id="abc", name="my_tool", arguments={"k": "v"})
        assert tc.id == "abc"
        assert tc.name == "my_tool"
        assert tc.arguments == {"k": "v"}

    def test_empty_arguments(self) -> None:
        tc = ToolCallRequest(id="x", name="t", arguments={})
        assert tc.arguments == {}

    def test_complex_arguments(self) -> None:
        args: dict[str, Any] = {"nested": {"a": [1, 2, 3]}, "flag": True}
        tc = ToolCallRequest(id="x", name="t", arguments=args)
        assert tc.arguments["nested"]["a"] == [1, 2, 3]


# ======================================================================
# StreamChunk
# ======================================================================


class TestStreamChunk:
    """Verify ``StreamChunk`` dataclass creation."""

    def test_defaults(self) -> None:
        chunk = StreamChunk()
        assert chunk.delta_content is None
        assert chunk.finish_reason is None
        assert chunk.tool_calls is None

    def test_with_content(self) -> None:
        chunk = StreamChunk(delta_content="hello")
        assert chunk.delta_content == "hello"

    def test_with_finish_reason(self) -> None:
        chunk = StreamChunk(finish_reason="stop")
        assert chunk.finish_reason == "stop"

    def test_with_tool_calls(self) -> None:
        tc = ToolCallRequest(id="c1", name="fn", arguments={})
        chunk = StreamChunk(tool_calls=[tc])
        assert chunk.tool_calls is not None
        assert len(chunk.tool_calls) == 1


# ======================================================================
# LLMResult
# ======================================================================


class TestLLMResult:
    """Verify ``LLMResult`` dataclass creation."""

    def test_basic(self) -> None:
        msg = ChatMessage(role="assistant", content="answer")
        result = LLMResult(message=msg)
        assert result.message.content == "answer"
        assert result.usage == {}

    def test_with_usage(self) -> None:
        msg = ChatMessage(role="assistant", content="ok")
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        result = LLMResult(message=msg, usage=usage)
        assert result.usage["total_tokens"] == 15

    def test_usage_default_factory(self) -> None:
        """Each LLMResult should get its own default dict, not a shared one."""
        r1 = LLMResult(message=ChatMessage(role="assistant"))
        r2 = LLMResult(message=ChatMessage(role="assistant"))
        r1.usage["x"] = 1
        assert "x" not in r2.usage


# ======================================================================
# BaseLLM abstract class
# ======================================================================


class TestBaseLLM:
    """Verify ``BaseLLM`` is abstract and cannot be directly instantiated."""

    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            BaseLLM()  # type: ignore[abstract]

    def test_default_abilities(self) -> None:
        """A minimal concrete subclass should inherit the default abilities."""

        class MinimalLLM(BaseLLM):
            async def chat(self, messages, **kwargs):  # type: ignore[override]
                return LLMResult(message=ChatMessage(role="assistant"))

            async def stream_chat(self, messages, **kwargs):  # type: ignore[override]
                yield StreamChunk()

        llm = MinimalLLM()
        abilities = llm.abilities
        assert abilities["tool_call"] is False
        assert abilities["json_mode"] is False
        assert abilities["vision"] is False
        assert abilities["streaming"] is False


# ======================================================================
# OpenAICompatibleLLM init (no real API calls)
# ======================================================================


class TestOpenAICompatibleLLMInit:
    """Verify ``OpenAICompatibleLLM`` stores its configuration correctly."""

    def test_stores_model(self) -> None:
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="gpt-4o",
        )
        assert llm._model == "gpt-4o"

    def test_stores_default_temperature(self) -> None:
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="gpt-4o",
            default_temperature=0.2,
        )
        assert llm._default_temperature == 0.2

    def test_stores_default_max_tokens(self) -> None:
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="gpt-4o",
            default_max_tokens=2048,
        )
        assert llm._default_max_tokens == 2048

    def test_stores_api_key(self) -> None:
        llm = OpenAICompatibleLLM(
            api_key="sk-key",
            base_url="https://api.example.com/v1",
            model="m",
        )
        assert llm.api_key == "sk-key"

    def test_abilities_all_true(self) -> None:
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="gpt-4o",
        )
        abilities = llm.abilities
        assert abilities["tool_call"] is True
        assert abilities["json_mode"] is True
        assert abilities["vision"] is True
        assert abilities["streaming"] is True

    def test_abilities_tool_call_true_even_with_anthropic_thinking(self) -> None:
        """Anthropic + thinking → tool_call still True.

        ReAct uses tool_choice="auto" which works with thinking.
        structured_llm_call's forced tool_choice gets a 400 but its
        own try/except fallback handles it gracefully.
        """
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.anthropic.com/v1/",
            model="claude-sonnet-4-6",
            reasoning_effort="high",
        )
        assert llm.abilities["tool_call"] is True

    def test_abilities_tool_call_true_even_with_anthropic_budget_tokens(self) -> None:
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.anthropic.com/v1/",
            model="claude-sonnet-4-6",
            reasoning_effort="high",
            reasoning_budget_tokens=8192,
        )
        assert llm.abilities["tool_call"] is True

    def test_abilities_tool_call_true_for_non_thinking_anthropic(self) -> None:
        """Anthropic without thinking still supports tool_call."""
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.anthropic.com/v1/",
            model="claude-sonnet-4-6",
        )
        assert llm.abilities["tool_call"] is True

    def test_abilities_json_mode_disabled_explicitly(self) -> None:
        """json_mode_enabled=False disables json_mode regardless of model/URL."""
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://relay.example.com/v1",
            model="claude-sonnet-4-6",
            json_mode_enabled=False,
        )
        assert llm.abilities["json_mode"] is False

    def test_abilities_json_mode_enabled_by_default(self) -> None:
        """json_mode_enabled defaults to True — no behavioral change for most models."""
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://relay.example.com/v1",
            model="claude-sonnet-4-6",
        )
        assert llm.abilities["json_mode"] is True

    def test_default_config_values(self) -> None:
        """Verify factory defaults when no optional kwargs are provided."""
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="gpt-4o",
        )
        assert llm._default_temperature == 0.7
        assert llm._default_max_tokens == 64000


# ======================================================================
# LiteLLM provider resolution
# ======================================================================


class TestResolveLiteLLMModel:
    """Verify ``_resolve_litellm_model`` maps providers correctly."""

    def test_resolve_openai(self) -> None:
        model, base = _resolve_litellm_model("https://api.openai.com/v1", "gpt-5.4")
        assert model == "openai/gpt-5.4"
        assert base is None

    def test_resolve_anthropic(self) -> None:
        model, base = _resolve_litellm_model(
            "https://api.anthropic.com/v1/", "claude-sonnet-4-6"
        )
        assert model == "anthropic/claude-sonnet-4-6"
        assert base is None

    def test_resolve_gemini(self) -> None:
        model, base = _resolve_litellm_model(
            "https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-pro"
        )
        assert model == "gemini/gemini-2.5-pro"
        assert base is None

    def test_resolve_deepseek(self) -> None:
        model, base = _resolve_litellm_model(
            "https://api.deepseek.com/v1", "deepseek-chat"
        )
        assert model == "deepseek/deepseek-chat"
        assert base is None

    def test_resolve_mistral(self) -> None:
        model, base = _resolve_litellm_model(
            "https://api.mistral.ai/v1", "mistral-large"
        )
        assert model == "mistral/mistral-large"
        assert base is None

    def test_resolve_unknown_proxy(self) -> None:
        model, base = _resolve_litellm_model("https://my-proxy.com/v1", "qwen3.5-plus")
        assert model == "openai/qwen3.5-plus"
        assert base == "https://my-proxy.com/v1"

    def test_resolve_ollama_local(self) -> None:
        model, base = _resolve_litellm_model("http://localhost:11434/v1", "llama3")
        assert model == "openai/llama3"
        assert base == "http://localhost:11434/v1"

    # --- Relay path hints ---

    def test_resolve_relay_claude_path(self) -> None:
        """Relay with /claude path → Anthropic native protocol."""
        model, base = _resolve_litellm_model(
            "https://api.uniapi.io/claude", "claude-sonnet-4-6"
        )
        assert model == "anthropic/claude-sonnet-4-6"
        assert base == "https://api.uniapi.io/claude"

    def test_resolve_relay_gemini_path(self) -> None:
        """Relay with /gemini path → Google native protocol."""
        model, base = _resolve_litellm_model(
            "https://api.uniapi.io/gemini", "gemini-2.5-pro"
        )
        assert model == "gemini/gemini-2.5-pro"
        assert base == "https://api.uniapi.io/gemini"

    def test_resolve_relay_openai_compat(self) -> None:
        """Relay with /v1 path (no hint) → OpenAI compatible fallback."""
        model, base = _resolve_litellm_model(
            "https://api.uniapi.io/v1", "claude-sonnet-4-6"
        )
        assert model == "openai/claude-sonnet-4-6"
        assert base == "https://api.uniapi.io/v1"

    # --- Explicit provider override ---

    def test_explicit_provider_relay(self) -> None:
        """DB provider overrides auto-detection."""
        model, base = _resolve_litellm_model(
            "https://my-relay.com/v1", "claude-sonnet-4-6", provider="anthropic"
        )
        assert model == "anthropic/claude-sonnet-4-6"
        assert base == "https://my-relay.com/v1"

    def test_explicit_provider_official(self) -> None:
        """DB provider + official domain → no api_base needed."""
        model, base = _resolve_litellm_model(
            "https://api.anthropic.com/v1/", "claude-sonnet-4-6", provider="anthropic"
        )
        assert model == "anthropic/claude-sonnet-4-6"
        assert base is None


# ======================================================================
# _build_request_kwargs
# ======================================================================


class TestBuildRequestKwargs:
    """Verify ``_build_request_kwargs`` produces correct LiteLLM params."""

    def test_basic_kwargs(self) -> None:
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
        )
        msgs = [ChatMessage(role="user", content="hi")]
        kwargs = llm._build_request_kwargs(
            msgs,
            tools=None,
            temperature=None,
            max_tokens=None,
            stream=False,
        )
        assert kwargs["model"] == "openai/gpt-4o"
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["max_tokens"] == 64000
        assert kwargs["temperature"] == 0.7
        assert kwargs["stream"] is False
        assert "api_base" not in kwargs

    def test_unknown_provider_includes_api_base(self) -> None:
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://my-proxy.com/v1",
            model="custom-model",
        )
        msgs = [ChatMessage(role="user", content="hi")]
        kwargs = llm._build_request_kwargs(
            msgs,
            tools=None,
            temperature=None,
            max_tokens=None,
            stream=False,
        )
        assert kwargs["api_base"] == "https://my-proxy.com/v1"

    def test_reasoning_effort_non_anthropic(self) -> None:
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="o3",
            reasoning_effort="high",
        )
        msgs = [ChatMessage(role="user", content="hi")]
        kwargs = llm._build_request_kwargs(
            msgs,
            tools=None,
            temperature=None,
            max_tokens=None,
            stream=False,
        )
        assert kwargs["reasoning_effort"] == "high"
        assert "thinking" not in kwargs

    def test_reasoning_effort_anthropic_delegates_to_litellm(self) -> None:
        """Anthropic: pass reasoning_effort, preserve user temperature."""
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.anthropic.com/v1/",
            model="claude-sonnet-4-6",
            reasoning_effort="high",
        )
        msgs = [ChatMessage(role="user", content="hi")]
        kwargs = llm._build_request_kwargs(
            msgs,
            tools=None,
            temperature=None,
            max_tokens=None,
            stream=False,
        )
        assert kwargs["reasoning_effort"] == "high"
        assert "thinking" not in kwargs
        # Bedrock/Anthropic thinking requires temperature=1.0 — auto-forced
        assert kwargs["temperature"] == 1.0

    def test_reasoning_budget_anthropic_explicit_thinking(self) -> None:
        """Explicit budget override → pass thinking directly, preserve user temperature."""
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.anthropic.com/v1/",
            model="claude-sonnet-4-6",
            reasoning_effort="high",
            reasoning_budget_tokens=8192,
        )
        msgs = [ChatMessage(role="user", content="hi")]
        kwargs = llm._build_request_kwargs(
            msgs,
            tools=None,
            temperature=None,
            max_tokens=None,
            stream=False,
        )
        assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 8192}
        assert "reasoning_effort" not in kwargs
        # Bedrock/Anthropic thinking requires temperature=1.0 — auto-forced
        assert kwargs["temperature"] == 1.0

    def test_reasoning_effort_openai_keeps_temperature(self) -> None:
        """OpenAI: reasoning_effort does NOT force temperature=1."""
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="o3",
            reasoning_effort="high",
            default_temperature=0.5,
        )
        msgs = [ChatMessage(role="user", content="hi")]
        kwargs = llm._build_request_kwargs(
            msgs,
            tools=None,
            temperature=None,
            max_tokens=None,
            stream=False,
        )
        assert kwargs["temperature"] == 0.5  # NOT forced to 1

    def test_gpt5_tools_drops_reasoning(self) -> None:
        """GPT-5 + tools → silently drop reasoning_effort."""
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="gpt-5.4",
            reasoning_effort="medium",
        )
        msgs = [ChatMessage(role="user", content="hi")]
        tools = [{"type": "function", "function": {"name": "test", "parameters": {}}}]
        kwargs = llm._build_request_kwargs(
            msgs,
            tools=tools,
            temperature=None,
            max_tokens=None,
            stream=False,
        )
        assert "reasoning_effort" not in kwargs
        assert "thinking" not in kwargs

    def test_no_max_completion_tokens_key(self) -> None:
        """LiteLLM handles the max_tokens → max_completion_tokens translation
        internally, so we should always use max_tokens."""
        llm = OpenAICompatibleLLM(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="o3",
        )
        msgs = [ChatMessage(role="user", content="hi")]
        kwargs = llm._build_request_kwargs(
            msgs,
            tools=None,
            temperature=None,
            max_tokens=None,
            stream=False,
        )
        assert "max_tokens" in kwargs
        assert "max_completion_tokens" not in kwargs


# ======================================================================
# Connection pooling — shared httpx.AsyncClient
# ======================================================================


class TestSharedHttpClient:
    """Verify shared HTTP connection pool lifecycle and configuration."""

    def test_get_returns_open_client(self) -> None:
        from fim_one.core.model.openai_compatible import _get_shared_http_client

        client = _get_shared_http_client()
        assert not client.is_closed

    def test_get_returns_same_instance(self) -> None:
        from fim_one.core.model.openai_compatible import _get_shared_http_client

        a = _get_shared_http_client()
        b = _get_shared_http_client()
        assert a is b

    def test_litellm_aclient_session_is_set(self) -> None:
        import litellm as _litellm
        from fim_one.core.model.openai_compatible import _get_shared_http_client

        client = _get_shared_http_client()
        assert _litellm.aclient_session is client

    def test_pool_limits_configured(self) -> None:
        from fim_one.core.model.openai_compatible import _get_shared_http_client

        client = _get_shared_http_client()
        pool = client._transport._pool  # type: ignore[attr-defined]
        assert pool._max_connections == 100
        assert pool._max_keepalive_connections == 20
        assert pool._keepalive_expiry == 30

    def test_timeout_configured(self) -> None:
        import httpx

        from fim_one.core.model.openai_compatible import _get_shared_http_client

        client = _get_shared_http_client()
        assert client.timeout == httpx.Timeout(300.0, connect=10.0)

    @pytest.mark.asyncio
    async def test_close_and_recreate(self) -> None:
        import litellm as _litellm
        from fim_one.core.model.openai_compatible import (
            _get_shared_http_client,
            close_shared_http_client,
        )

        original = _get_shared_http_client()
        await close_shared_http_client()

        # After close, litellm session is cleared and client is closed
        assert _litellm.aclient_session is None
        assert original.is_closed

        # Re-acquire creates a fresh client
        fresh = _get_shared_http_client()
        assert fresh is not original
        assert not fresh.is_closed
        assert _litellm.aclient_session is fresh

    @pytest.mark.asyncio
    async def test_close_idempotent(self) -> None:
        from fim_one.core.model.openai_compatible import close_shared_http_client

        # Closing multiple times should not raise
        await close_shared_http_client()
        await close_shared_http_client()

        # Ensure state is clean after double-close
        from fim_one.core.model.openai_compatible import _SHARED_HTTP_CLIENT
        assert _SHARED_HTTP_CLIENT is None
