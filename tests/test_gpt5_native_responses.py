"""Tests for the native /v1/responses dispatch path (GPT-5.x).

Covers the gating decision, the request shape, reasoning-item replay, the
two error-classification rules, and the ``FIM_GPT5_RESPONSES_MODE``
switch.  ``litellm.aresponses`` / ``litellm.acompletion`` are patched, so
nothing here touches the network.
"""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from fim_one.core.model import ChatMessage, OpenAICompatibleLLM, ToolCallRequest


@pytest.fixture(autouse=True)
def _clean_caches(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Isolate the module-level endpoint caches and the mode switch."""
    from fim_one.core.model.openai_compatible import (
        _RESPONSES_BRIDGE_SUPPORT,
        _RESPONSES_NATIVE_SUPPORT,
    )

    monkeypatch.delenv("FIM_GPT5_RESPONSES_MODE", raising=False)
    _RESPONSES_NATIVE_SUPPORT.clear()
    _RESPONSES_BRIDGE_SUPPORT.clear()
    yield
    _RESPONSES_NATIVE_SUPPORT.clear()
    _RESPONSES_BRIDGE_SUPPORT.clear()


def _llm(model: str = "gpt-5.6-luna", **kw: Any) -> OpenAICompatibleLLM:
    params: dict[str, Any] = {
        "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1",
        "model": model,
        "reasoning_effort": "medium",
        "retry_config": None,
        "rate_limit_config": None,
    }
    params.update(kw)
    return OpenAICompatibleLLM(**params)


def _tools() -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": "search", "parameters": {}}}]


def _reasoning_item(item_id: str = "rs_1") -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "reasoning",
        "encrypted_content": f"gAAAAA-{item_id}",
        "summary": [{"type": "summary_text", "text": "planning"}],
        "status": "completed",
    }


def _response_payload(**kw: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "hi"}]}
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }
    payload.update(kw)
    return payload


def _not_found() -> Exception:
    import litellm

    return litellm.NotFoundError(
        message="no route", model="gpt-5.6-luna", llm_provider="openai"
    )


def _bad_request() -> Exception:
    import litellm

    return litellm.BadRequestError(
        message="stale reasoning item", model="gpt-5.6-luna", llm_provider="openai"
    )


# ======================================================================
# Gating
# ======================================================================


class TestNativeGating:
    def test_gpt5_on_openai_route_is_native_by_default(self) -> None:
        assert _llm()._should_use_native_responses() is True

    def test_non_gpt5_never_goes_native(self) -> None:
        """Only GPT-5.x gains anything; other families stay on completions."""
        assert _llm(model="gpt-4o")._should_use_native_responses() is False

    def test_anthropic_route_never_goes_native(self) -> None:
        llm = _llm(
            model="claude-opus-4-8",
            base_url="https://api.anthropic.com",
            provider="anthropic",
        )
        assert llm._should_use_native_responses() is False

    def test_explicit_none_effort_stays_on_completions(self) -> None:
        """``structured_llm_call`` wants no reasoning, so gains nothing here."""
        assert _llm()._should_use_native_responses(reasoning_effort=None) is False

    def test_explicit_effort_string_still_goes_native(self) -> None:
        assert _llm()._should_use_native_responses(reasoning_effort="high") is True

    @pytest.mark.parametrize("mode", ["bridge", "off"])
    def test_mode_switch_disables_native(
        self, monkeypatch: pytest.MonkeyPatch, mode: str
    ) -> None:
        monkeypatch.setenv("FIM_GPT5_RESPONSES_MODE", mode)
        assert _llm()._should_use_native_responses() is False

    def test_unknown_mode_falls_back_to_native(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A typo must not silently disable reasoning replay."""
        monkeypatch.setenv("FIM_GPT5_RESPONSES_MODE", "nativ")
        assert _llm()._should_use_native_responses() is True

    def test_cached_false_disables_native(self) -> None:
        from fim_one.core.model.openai_compatible import _RESPONSES_NATIVE_SUPPORT

        llm = _llm()
        _RESPONSES_NATIVE_SUPPORT[(llm._api_base, llm._litellm_model)] = False
        assert llm._should_use_native_responses() is False


# ======================================================================
# Request shape
# ======================================================================


class TestResponsesKwargs:
    def _kwargs(self, **kw: Any) -> dict[str, Any]:
        params: dict[str, Any] = {
            "tools": None,
            "tool_choice": None,
            "max_tokens": None,
        }
        params.update(kw)
        return _llm()._build_responses_kwargs(
            [ChatMessage(role="user", content="hi")], **params
        )

    def test_model_is_bare_with_explicit_provider(self) -> None:
        """The ``openai/`` prefix belongs to the completions router only."""
        kwargs = self._kwargs()
        assert kwargs["model"] == "gpt-5.6-luna"
        assert kwargs["custom_llm_provider"] == "openai"

    def test_stateless_with_encrypted_reasoning_requested(self) -> None:
        """Without the include, items come back empty and replay is a no-op."""
        kwargs = self._kwargs()
        assert kwargs["store"] is False
        assert kwargs["include"] == ["reasoning.encrypted_content"]

    def test_temperature_is_never_sent(self) -> None:
        """GPT-5 reasoning models reject temperature outright."""
        assert "temperature" not in self._kwargs()

    def test_reasoning_effort_and_summary(self) -> None:
        assert self._kwargs()["reasoning"] == {"summary": "auto", "effort": "medium"}

    def test_tools_are_flattened_and_choice_converted(self) -> None:
        kwargs = self._kwargs(
            tools=_tools(),
            tool_choice={"type": "function", "function": {"name": "search"}},
        )
        assert kwargs["tools"][0]["name"] == "search"
        assert "function" not in kwargs["tools"][0]
        assert kwargs["tool_choice"] == {"type": "function", "name": "search"}

    def test_max_output_tokens_is_used(self) -> None:
        assert self._kwargs(max_tokens=512)["max_output_tokens"] == 512

    def test_reasoning_items_are_replayed_in_input(self) -> None:
        """The whole point: a prior turn's items go back out verbatim."""
        messages = [
            ChatMessage(role="user", content="q"),
            ChatMessage(
                role="assistant",
                content="calling",
                tool_calls=[ToolCallRequest(id="c1", name="search", arguments={})],
                reasoning_items=[_reasoning_item()],
            ),
            ChatMessage(role="tool", content="result", tool_call_id="c1"),
        ]
        kwargs = _llm()._build_responses_kwargs(
            messages, tools=_tools(), tool_choice="auto", max_tokens=None
        )
        replayed = [i for i in kwargs["input"] if i.get("type") == "reasoning"]
        assert len(replayed) == 1
        assert replayed[0]["encrypted_content"] == "gAAAAA-rs_1"


# ======================================================================
# Non-streaming dispatch
# ======================================================================


class TestNativeChatDispatch:
    async def test_chat_uses_aresponses_not_acompletion(self) -> None:
        llm = _llm()
        with (
            patch(
                "fim_one.core.model.openai_compatible.litellm.aresponses",
                new=AsyncMock(return_value=_response_payload()),
            ) as mock_responses,
            patch(
                "fim_one.core.model.openai_compatible.litellm.acompletion",
                new=AsyncMock(),
            ) as mock_completion,
        ):
            result = await llm.chat([ChatMessage(role="user", content="hi")])
        assert mock_responses.call_count == 1
        assert mock_completion.call_count == 0
        assert result.message.content == "hi"
        assert result.usage["prompt_tokens"] == 10

    async def test_reasoning_items_land_on_the_result_message(self) -> None:
        llm = _llm()
        payload = _response_payload(
            output=[
                _reasoning_item(),
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "search",
                    "arguments": "{}",
                },
            ]
        )
        with patch(
            "fim_one.core.model.openai_compatible.litellm.aresponses",
            new=AsyncMock(return_value=payload),
        ):
            result = await llm.chat([ChatMessage(role="user", content="hi")])
        assert result.message.reasoning_items is not None
        assert result.message.reasoning_content == "planning"
        assert result.message.tool_calls is not None
        assert result.message.tool_calls[0].id == "c1"

    async def test_truncated_tool_call_is_dropped(self) -> None:
        """Half-written arguments must never be dispatched to a tool."""
        llm = _llm()
        payload = _response_payload(
            status="incomplete",
            incomplete_details={"reason": "max_output_tokens"},
            output=[
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "search",
                    "arguments": '{"q":',
                }
            ],
        )
        with patch(
            "fim_one.core.model.openai_compatible.litellm.aresponses",
            new=AsyncMock(return_value=payload),
        ):
            result = await llm.chat([ChatMessage(role="user", content="hi")])
        assert result.truncated_tool_call is True
        assert result.message.tool_calls is None

    async def test_structured_call_stays_on_completions(self) -> None:
        """``reasoning_effort=None`` opts out of the native path entirely."""
        llm = _llm()
        completion = AsyncMock(return_value=_fake_completion())
        with (
            patch(
                "fim_one.core.model.openai_compatible.litellm.aresponses",
                new=AsyncMock(),
            ) as mock_responses,
            patch(
                "fim_one.core.model.openai_compatible.litellm.acompletion",
                new=completion,
            ),
        ):
            await llm.chat(
                [ChatMessage(role="user", content="hi")],
                tools=_tools(),
                reasoning_effort=None,
            )
        assert mock_responses.call_count == 0
        assert completion.call_count == 1


# ======================================================================
# Error classification
# ======================================================================


class TestNativeErrorClassification:
    async def test_not_found_falls_back_and_is_cached(self) -> None:
        """No /v1/responses route is structural: stop probing this endpoint."""
        llm = _llm()
        with (
            patch(
                "fim_one.core.model.openai_compatible.litellm.aresponses",
                new=AsyncMock(side_effect=_not_found()),
            ) as mock_responses,
            patch(
                "fim_one.core.model.openai_compatible.litellm.acompletion",
                new=AsyncMock(return_value=_fake_completion()),
            ) as mock_completion,
        ):
            await llm.chat([ChatMessage(role="user", content="hi")])
            await llm.chat([ChatMessage(role="user", content="hi")])
        assert mock_responses.call_count == 1  # probed once, then remembered
        assert mock_completion.call_count == 2

    async def test_bad_request_falls_back_without_caching(self) -> None:
        """One stale reasoning item must not blacklist the path forever."""
        llm = _llm()
        with (
            patch(
                "fim_one.core.model.openai_compatible.litellm.aresponses",
                new=AsyncMock(side_effect=[_bad_request(), _response_payload()]),
            ) as mock_responses,
            patch(
                "fim_one.core.model.openai_compatible.litellm.acompletion",
                new=AsyncMock(return_value=_fake_completion()),
            ) as mock_completion,
        ):
            await llm.chat([ChatMessage(role="user", content="hi")])
            second = await llm.chat([ChatMessage(role="user", content="hi")])
        assert mock_responses.call_count == 2  # retried, not blacklisted
        assert mock_completion.call_count == 1  # only the first call fell back
        assert second.message.content == "hi"

    async def test_other_errors_propagate(self) -> None:
        """A transient failure belongs to the retry layer, not the fallback."""
        llm = _llm()
        with (
            patch(
                "fim_one.core.model.openai_compatible.litellm.aresponses",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch(
                "fim_one.core.model.openai_compatible.litellm.acompletion",
                new=AsyncMock(return_value=_fake_completion()),
            ) as mock_completion,
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await llm.chat([ChatMessage(role="user", content="hi")])
        assert mock_completion.call_count == 0


# ======================================================================
# Streaming dispatch
# ======================================================================


class TestNativeStreamDispatch:
    async def test_stream_translates_events(self) -> None:
        llm = _llm()
        events = [
            {"type": "response.reasoning_summary_text.delta", "delta": "plan"},
            {"type": "response.output_item.done", "item": _reasoning_item()},
            {"type": "response.output_text.delta", "delta": "hello"},
            {"type": "response.completed", "response": _response_payload(output=[])},
        ]

        async def _stream() -> AsyncIterator[dict[str, Any]]:
            for event in events:
                yield event

        with patch(
            "fim_one.core.model.openai_compatible.litellm.aresponses",
            new=AsyncMock(return_value=_stream()),
        ):
            chunks = [
                c async for c in llm.stream_chat([ChatMessage(role="user", content="hi")])
            ]
        assert any(c.delta_reasoning == "plan" for c in chunks)
        assert any(c.reasoning_item for c in chunks)
        assert any(c.delta_content == "hello" for c in chunks)
        assert chunks[-1].finish_reason == "stop"

    async def test_stream_falls_back_on_not_found(self) -> None:
        llm = _llm()

        async def _completion_stream() -> AsyncIterator[Any]:
            yield _fake_stream_chunk()

        with (
            patch(
                "fim_one.core.model.openai_compatible.litellm.aresponses",
                new=AsyncMock(side_effect=_not_found()),
            ),
            patch(
                "fim_one.core.model.openai_compatible.litellm.acompletion",
                new=AsyncMock(return_value=_completion_stream()),
            ) as mock_completion,
        ):
            chunks = [
                c async for c in llm.stream_chat([ChatMessage(role="user", content="hi")])
            ]
        assert mock_completion.call_count == 1
        assert chunks[-1].finish_reason == "stop"


# ======================================================================
# Chat-completions fakes (for the fallback assertions above)
# ======================================================================


class _Obj:
    def __init__(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


def _fake_completion() -> Any:
    message = _Obj(role="assistant", content="fallback", tool_calls=None)
    choice = _Obj(message=message, finish_reason="stop")
    usage = _Obj(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    return _Obj(choices=[choice], usage=usage)


def _fake_stream_chunk() -> Any:
    delta = _Obj(content="fallback", tool_calls=None)
    choice = _Obj(delta=delta, finish_reason="stop")
    return _Obj(choices=[choice], usage=None)
