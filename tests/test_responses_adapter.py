"""Tests for the /v1/responses translation layer.

Everything under test is a pure function over plain data, so these run
with no network, no litellm, and no mocking beyond hand-built payloads
shaped like the ones OpenAI returns.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from fim_one.core.model.responses_adapter import (
    ResponsesStreamError,
    build_responses_input,
    convert_response_format,
    convert_tool_choice,
    convert_tools,
    map_usage,
    parse_response,
    sanitize_reasoning_item,
    stream_to_chunks,
)
from fim_one.core.model.types import ChatMessage, ToolCallRequest


def _reasoning_item(item_id: str = "rs_1", *, summary: str | None = None) -> dict[str, Any]:
    """A reasoning item shaped the way the provider emits one."""
    item: dict[str, Any] = {
        "id": item_id,
        "type": "reasoning",
        "encrypted_content": f"gAAAAA-{item_id}",
        "summary": [],
        "status": "completed",
    }
    if summary:
        item["summary"] = [{"type": "summary_text", "text": summary}]
    return item


# ======================================================================
# build_responses_input
# ======================================================================


class TestBuildResponsesInput:
    def test_system_and_user_become_input_text_messages(self) -> None:
        items = build_responses_input(
            [
                ChatMessage(role="system", content="be terse"),
                ChatMessage(role="user", content="hi"),
            ]
        )
        assert items == [
            {"role": "system", "content": [{"type": "input_text", "text": "be terse"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        ]

    def test_vision_parts_are_converted(self) -> None:
        content = ChatMessage.build_vision_content("what is this", ["data:image/png;base64,AA"])
        items = build_responses_input([ChatMessage(role="user", content=content)])
        assert items[0]["content"] == [
            {"type": "input_text", "text": "what is this"},
            {"type": "input_image", "image_url": "data:image/png;base64,AA"},
        ]

    def test_assistant_emits_reasoning_then_content_then_calls(self) -> None:
        """Order is load-bearing: the API rejects a shuffled turn."""
        msg = ChatMessage(
            role="assistant",
            content="calling a tool",
            tool_calls=[ToolCallRequest(id="call_1", name="search", arguments={"q": "x"})],
            reasoning_items=[_reasoning_item()],
        )
        items = build_responses_input([msg])
        assert [i.get("type") or i.get("role") for i in items] == [
            "reasoning",
            "assistant",
            "function_call",
        ]
        assert items[0]["encrypted_content"] == "gAAAAA-rs_1"
        assert items[2] == {
            "type": "function_call",
            "call_id": "call_1",
            "name": "search",
            "arguments": json.dumps({"q": "x"}),
        }

    def test_volatile_keys_are_stripped_from_replayed_items(self) -> None:
        """``id`` names a record that ``store=false`` never persisted.

        Replaying it earns "Item with id 'rs_...' not found"; the
        ``encrypted_content`` blob carries the state on its own.
        """
        msg = ChatMessage(
            role="assistant",
            content="x",
            reasoning_items=[_reasoning_item()],
        )
        replayed = build_responses_input([msg])[0]
        assert "id" not in replayed
        assert "status" not in replayed
        assert replayed["encrypted_content"] == "gAAAAA-rs_1"

    def test_orphan_reasoning_is_dropped(self) -> None:
        """A reasoning item that resolved into nothing 400s on replay."""
        msg = ChatMessage(role="assistant", reasoning_items=[_reasoning_item()])
        assert build_responses_input([msg]) == []

    def test_tool_result_becomes_function_call_output(self) -> None:
        items = build_responses_input(
            [ChatMessage(role="tool", content="42", tool_call_id="call_1")]
        )
        assert items == [
            {"type": "function_call_output", "call_id": "call_1", "output": "42"}
        ]

    def test_assistant_text_uses_output_text_part(self) -> None:
        items = build_responses_input([ChatMessage(role="assistant", content="done")])
        assert items[0]["content"] == [{"type": "output_text", "text": "done"}]

    def test_empty_content_message_is_skipped(self) -> None:
        assert build_responses_input([ChatMessage(role="user", content="")]) == []

    def test_full_round_trip_ordering(self) -> None:
        """A two-round tool conversation keeps every item in sequence."""
        items = build_responses_input(
            [
                ChatMessage(role="user", content="q"),
                ChatMessage(
                    role="assistant",
                    content="thinking about it",
                    tool_calls=[ToolCallRequest(id="c1", name="t", arguments={})],
                    reasoning_items=[_reasoning_item("rs_1")],
                ),
                ChatMessage(role="tool", content="result", tool_call_id="c1"),
            ]
        )
        assert [i.get("type") or i.get("role") for i in items] == [
            "user",
            "reasoning",
            "assistant",
            "function_call",
            "function_call_output",
        ]


# ======================================================================
# Tool / tool_choice / response_format conversion
# ======================================================================


class TestToolConversion:
    def test_nested_function_is_flattened(self) -> None:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "find things",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        assert convert_tools(tools) == [
            {
                "type": "function",
                "name": "search",
                "description": "find things",
                "parameters": {"type": "object", "properties": {}},
                "strict": False,
            }
        ]

    def test_strict_flag_is_preserved(self) -> None:
        tools = [{"type": "function", "function": {"name": "s", "strict": True}}]
        assert convert_tools(tools)[0]["strict"] is True

    def test_none_and_empty_pass_through(self) -> None:
        assert convert_tools(None) is None
        assert convert_tools([]) is None

    @pytest.mark.parametrize("choice", ["auto", "required", "none"])
    def test_string_choices_unchanged(self, choice: str) -> None:
        assert convert_tool_choice(choice) == choice

    def test_named_choice_loses_the_function_wrapper(self) -> None:
        chat_shape = {"type": "function", "function": {"name": "search"}}
        assert convert_tool_choice(chat_shape) == {"type": "function", "name": "search"}

    def test_none_choice_stays_none(self) -> None:
        assert convert_tool_choice(None) is None

    def test_response_format_maps_onto_text(self) -> None:
        fmt = {"type": "json_object"}
        assert convert_response_format(fmt) == {"format": fmt}
        assert convert_response_format(None) is None


# ======================================================================
# map_usage
# ======================================================================


class _Usage:
    def __init__(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


class TestMapUsage:
    def test_full_payload_maps_every_counter(self) -> None:
        usage = map_usage(
            _Usage(
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                output_tokens_details=_Usage(reasoning_tokens=30),
                input_tokens_details=_Usage(cached_tokens=80),
            )
        )
        assert usage["prompt_tokens"] == 100
        assert usage["completion_tokens"] == 50
        assert usage["total_tokens"] == 150
        assert usage["reasoning_tokens"] == 30
        assert usage["cache_read_input_tokens"] == 80

    def test_missing_details_default_to_zero(self) -> None:
        usage = map_usage(_Usage(input_tokens=10, output_tokens=5))
        assert usage["reasoning_tokens"] == 0
        assert usage["cache_read_input_tokens"] == 0
        assert usage["total_tokens"] == 15  # derived when absent

    def test_absent_usage_never_raises(self) -> None:
        assert map_usage(None) == {}
        assert map_usage(_Usage())["prompt_tokens"] == 0

    def test_dict_shaped_usage_works(self) -> None:
        usage = map_usage({"input_tokens": 7, "output_tokens": 3, "total_tokens": 10})
        assert usage["prompt_tokens"] == 7


# ======================================================================
# parse_response
# ======================================================================


def _response(
    output: list[dict[str, Any]],
    *,
    status: str = "completed",
    incomplete_reason: str | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": status, "output": output, "usage": usage}
    if incomplete_reason:
        payload["incomplete_details"] = {"reason": incomplete_reason}
    return payload


class TestParseResponse:
    def test_text_only_response(self) -> None:
        result = parse_response(
            _response(
                [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}]
            )
        )
        assert result.message.content == "hello"
        assert result.message.tool_calls is None
        assert result.finish_reason == "stop"

    def test_tool_call_id_comes_from_call_id_not_id(self) -> None:
        """``id`` names the output item; ``call_id`` is the replay handle."""
        result = parse_response(
            _response(
                [
                    {
                        "type": "function_call",
                        "id": "fc_internal",
                        "call_id": "call_abc",
                        "name": "search",
                        "arguments": '{"q": "x"}',
                    }
                ]
            )
        )
        assert result.message.tool_calls is not None
        call = result.message.tool_calls[0]
        assert call.id == "call_abc"
        assert call.arguments == {"q": "x"}
        assert result.finish_reason == "tool_calls"

    def test_reasoning_items_and_summary_are_split(self) -> None:
        result = parse_response(
            _response([_reasoning_item(summary="I should search"), ])
        )
        assert result.message.reasoning_items is not None
        assert result.message.reasoning_items[0]["encrypted_content"] == "gAAAAA-rs_1"
        assert result.message.reasoning_content == "I should search"

    def test_incomplete_max_tokens_maps_to_length(self) -> None:
        """The existing truncation guards key on ``finish_reason == "length"``."""
        result = parse_response(
            _response([], status="incomplete", incomplete_reason="max_output_tokens")
        )
        assert result.finish_reason == "length"

    def test_malformed_arguments_are_kept_raw(self) -> None:
        result = parse_response(
            _response(
                [
                    {
                        "type": "function_call",
                        "call_id": "c1",
                        "name": "t",
                        "arguments": "{not json",
                    }
                ]
            )
        )
        assert result.message.tool_calls is not None
        assert result.message.tool_calls[0].arguments == {"_raw": "{not json"}

    def test_usage_is_mapped(self) -> None:
        result = parse_response(
            _response([], usage={"input_tokens": 4, "output_tokens": 6})
        )
        assert result.usage["prompt_tokens"] == 4


# ======================================================================
# stream_to_chunks
# ======================================================================


async def _replay(events: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for event in events:
        yield event


async def _collect(events: list[dict[str, Any]]) -> list[Any]:
    return [chunk async for chunk in stream_to_chunks(_replay(events))]


class TestStreamToChunks:
    async def test_text_deltas_become_content_chunks(self) -> None:
        chunks = await _collect(
            [
                {"type": "response.output_text.delta", "delta": "he"},
                {"type": "response.output_text.delta", "delta": "llo"},
            ]
        )
        assert [c.delta_content for c in chunks] == ["he", "llo"]

    async def test_summary_deltas_reuse_delta_reasoning(self) -> None:
        """The UI already renders ``delta_reasoning``, so no frontend change."""
        chunks = await _collect(
            [{"type": "response.reasoning_summary_text.delta", "delta": "thinking"}]
        )
        assert chunks[0].delta_reasoning == "thinking"

    async def test_completed_reasoning_item_is_emitted_once(self) -> None:
        chunks = await _collect(
            [
                {
                    "type": "response.output_item.done",
                    "item": _reasoning_item(),
                }
            ]
        )
        assert len(chunks) == 1
        assert chunks[0].reasoning_item is not None
        assert chunks[0].reasoning_item["encrypted_content"] == "gAAAAA-rs_1"
        assert "status" not in chunks[0].reasoning_item

    async def test_non_reasoning_item_done_is_ignored(self) -> None:
        chunks = await _collect(
            [{"type": "response.output_item.done", "item": {"type": "message"}}]
        )
        assert chunks == []

    async def test_completed_event_carries_tool_calls_and_usage(self) -> None:
        chunks = await _collect(
            [
                {
                    "type": "response.completed",
                    "response": _response(
                        [
                            {
                                "type": "function_call",
                                "call_id": "c1",
                                "name": "search",
                                "arguments": "{}",
                            }
                        ],
                        usage={"input_tokens": 1, "output_tokens": 2},
                    ),
                }
            ]
        )
        assert chunks[-1].finish_reason == "tool_calls"
        assert chunks[-1].tool_calls is not None
        assert chunks[-1].tool_calls[0].id == "c1"
        assert chunks[-1].usage is not None
        assert chunks[-1].usage["prompt_tokens"] == 1

    async def test_truncated_stream_reports_length(self) -> None:
        chunks = await _collect(
            [
                {
                    "type": "response.incomplete",
                    "response": _response(
                        [], status="incomplete", incomplete_reason="max_output_tokens"
                    ),
                }
            ]
        )
        assert chunks[-1].finish_reason == "length"

    async def test_unknown_events_are_ignored(self) -> None:
        """OpenAI adds event types regularly; one must never break a turn."""
        chunks = await _collect(
            [
                {"type": "response.some_future_thing", "data": 1},
                {"type": "response.output_text.delta", "delta": "ok"},
            ]
        )
        assert [c.delta_content for c in chunks] == ["ok"]

    async def test_failed_event_raises(self) -> None:
        with pytest.raises(ResponsesStreamError, match="boom"):
            await _collect(
                [
                    {
                        "type": "response.failed",
                        "response": {"error": {"message": "boom"}},
                    }
                ]
            )

    async def test_full_tool_round_script(self) -> None:
        """A realistic event script: reason, call a tool, complete."""
        chunks = await _collect(
            [
                {"type": "response.created", "response": {}},
                {"type": "response.reasoning_summary_text.delta", "delta": "plan"},
                {"type": "response.output_item.done", "item": _reasoning_item()},
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_1",
                    "delta": '{"q":',
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_1",
                    "delta": '"x"}',
                },
                {
                    "type": "response.completed",
                    "response": _response(
                        [
                            _reasoning_item(),
                            {
                                "type": "function_call",
                                "call_id": "c1",
                                "name": "search",
                                "arguments": '{"q":"x"}',
                            },
                        ]
                    ),
                },
            ]
        )
        assert any(c.delta_reasoning == "plan" for c in chunks)
        assert any(c.reasoning_item for c in chunks)
        assert chunks[-1].tool_calls is not None
        assert chunks[-1].tool_calls[0].arguments == {"q": "x"}


class TestSanitizeReasoningItem:
    def test_pydantic_style_object_is_dumped(self) -> None:
        class _Item:
            def model_dump(self) -> dict[str, Any]:
                return {"type": "reasoning", "encrypted_content": "abc", "status": "done"}

        assert sanitize_reasoning_item(_Item()) == {
            "type": "reasoning",
            "encrypted_content": "abc",
        }

    def test_unreadable_object_yields_empty_dict(self) -> None:
        assert sanitize_reasoning_item(object()) == {}
