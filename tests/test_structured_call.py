"""Tests for the structured_llm_call utility."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.model.structured import (
    StructuredCallResult,
    StructuredOutputError,
    structured_llm_call,
)
from fim_one.core.model.types import ToolCallRequest

from .conftest import FakeLLM

SIMPLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}

_TC_ABILITIES = {
    "tool_call": True,
    "json_mode": False,
    "vision": False,
    "streaming": False,
}

_JSON_ABILITIES = {
    "tool_call": False,
    "json_mode": True,
    "vision": False,
    "streaming": False,
}

_BOTH_ABILITIES = {
    "tool_call": True,
    "json_mode": True,
    "vision": False,
    "streaming": False,
}


def _usage(prompt: int = 10, completion: int = 5) -> dict[str, int]:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


# ======================================================================
# 1. Native FC happy path
# ======================================================================


class TestNativeFCHappyPath:
    async def test_extracts_from_tool_calls(self) -> None:
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(
                        role="assistant",
                        tool_calls=[
                            ToolCallRequest(
                                id="c1",
                                name="fn",
                                arguments={"answer": "42"},
                            ),
                        ],
                    ),
                    usage=_usage(),
                ),
            ],
            abilities=_TC_ABILITIES,
        )
        result = await structured_llm_call(
            llm,
            [ChatMessage(role="user", content="test")],
            SIMPLE_SCHEMA,
            "fn",
        )
        assert result.value == {"answer": "42"}
        assert result.level_used == "native_fc"
        assert result.llm_calls == 1
        assert result.total_usage["total_tokens"] == 15


# ======================================================================
# 2. JSON Mode happy path
# ======================================================================


class TestJsonModeHappyPath:
    async def test_extracts_from_json_content(self) -> None:
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(
                        role="assistant",
                        content='{"answer": "42"}',
                    ),
                    usage=_usage(),
                ),
            ],
            abilities=_JSON_ABILITIES,
        )
        result = await structured_llm_call(
            llm,
            [ChatMessage(role="user", content="test")],
            SIMPLE_SCHEMA,
            "fn",
        )
        assert result.value == {"answer": "42"}
        assert result.level_used == "json_mode"


# ======================================================================
# 3. Degradation: Native FC → JSON Mode
# ======================================================================


class TestDegradation:
    async def test_native_fc_to_json_mode(self) -> None:
        """When native FC returns no tool_calls, falls to JSON mode."""
        llm = FakeLLM(
            responses=[
                # Native FC attempt — empty content, no tool_calls
                LLMResult(
                    message=ChatMessage(role="assistant", content=""),
                ),
                # JSON Mode attempt — valid JSON
                LLMResult(
                    message=ChatMessage(
                        role="assistant",
                        content='{"answer": "42"}',
                    ),
                ),
            ],
            abilities=_BOTH_ABILITIES,
        )
        result = await structured_llm_call(
            llm,
            [ChatMessage(role="user", content="test")],
            SIMPLE_SCHEMA,
            "fn",
        )
        assert result.value == {"answer": "42"}
        assert result.level_used == "json_mode"
        assert result.llm_calls == 2


# ======================================================================
# 4. Plain text with markdown fences
# ======================================================================


class TestPlainTextMarkdownFences:
    async def test_extracts_from_fenced_json(self) -> None:
        content = '```json\n{"answer": "42"}\n```'
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content=content),
                ),
            ],
        )
        result = await structured_llm_call(
            llm,
            [ChatMessage(role="user", content="test")],
            SIMPLE_SCHEMA,
            "fn",
        )
        assert result.value == {"answer": "42"}
        assert result.level_used == "plain_text"


# ======================================================================
# 5. regex_fallback invoked when extract_json fails
# ======================================================================


class TestRegexFallback:
    async def test_regex_invoked_when_json_fails(self) -> None:
        def my_regex(content: str) -> dict[str, Any] | None:
            if "hello" in content:
                return {"answer": "regex-extracted"}
            return None

        llm = FakeLLM(
            responses=[
                # First attempt: not JSON, but regex matches
                LLMResult(
                    message=ChatMessage(
                        role="assistant",
                        content="hello world, not json",
                    ),
                ),
            ],
        )
        result = await structured_llm_call(
            llm,
            [ChatMessage(role="user", content="test")],
            SIMPLE_SCHEMA,
            "fn",
            regex_fallback=my_regex,
        )
        assert result.value == {"answer": "regex-extracted"}
        assert result.level_used == "plain_text"


# ======================================================================
# 6. default_value returned on total failure
# ======================================================================


class TestDefaultValue:
    async def test_returns_default_on_failure(self) -> None:
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content="nope"),
                ),
            ],
        )
        result = await structured_llm_call(
            llm,
            [ChatMessage(role="user", content="test")],
            SIMPLE_SCHEMA,
            "fn",
            default_value={"answer": "default"},
        )
        assert result.value == {"answer": "default"}
        assert result.raw_data == {}


# ======================================================================
# 7. StructuredOutputError when no default
# ======================================================================


class TestStructuredOutputError:
    async def test_raises_when_no_default(self) -> None:
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content="nope"),
                ),
            ],
        )
        with pytest.raises(StructuredOutputError, match="extraction levels failed"):
            await structured_llm_call(
                llm,
                [ChatMessage(role="user", content="test")],
                SIMPLE_SCHEMA,
                "fn",
            )

    async def test_is_value_error_subclass(self) -> None:
        """Backward compat: callers catching ValueError still work."""
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content="nope"),
                ),
            ],
        )
        with pytest.raises(ValueError):
            await structured_llm_call(
                llm,
                [ChatMessage(role="user", content="test")],
                SIMPLE_SCHEMA,
                "fn",
            )


# ======================================================================
# 8. parse_fn transforms raw dict to domain object
# ======================================================================


class TestParseFn:
    async def test_transforms_raw_dict(self) -> None:
        @dataclass
        class Answer:
            text: str

        def parse(d: dict) -> Answer:
            return Answer(text=d["answer"])

        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(
                        role="assistant",
                        content='{"answer": "42"}',
                    ),
                ),
            ],
        )
        result = await structured_llm_call(
            llm,
            [ChatMessage(role="user", content="test")],
            SIMPLE_SCHEMA,
            "fn",
            parse_fn=parse,
        )
        assert isinstance(result.value, Answer)
        assert result.value.text == "42"
        assert result.raw_data == {"answer": "42"}

    async def test_value_error_triggers_degradation(self) -> None:
        """ValueError from parse_fn triggers degradation, not immediate propagation.

        When parse_fn raises ValueError, _transform catches it and returns None,
        allowing the degradation chain to continue to the next level.  If all
        levels fail, StructuredOutputError is raised.
        """

        def bad_parse(d: dict) -> dict:
            raise ValueError("bad data")

        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(
                        role="assistant",
                        content='{"answer": "42"}',
                    ),
                ),
            ],
        )
        with pytest.raises(StructuredOutputError):
            await structured_llm_call(
                llm,
                [ChatMessage(role="user", content="test")],
                SIMPLE_SCHEMA,
                "fn",
                parse_fn=bad_parse,
            )


# ======================================================================
# 9. Usage accumulation across retries
# ======================================================================


class TestUsageAccumulation:
    async def test_accumulates_across_retries(self) -> None:
        """JSON mode fails + retry succeeds → 2 calls, accumulated usage."""
        llm = FakeLLM(
            responses=[
                # First attempt: invalid JSON
                LLMResult(
                    message=ChatMessage(role="assistant", content="bad"),
                    usage=_usage(10, 5),
                ),
                # Retry: valid JSON
                LLMResult(
                    message=ChatMessage(
                        role="assistant",
                        content='{"answer": "42"}',
                    ),
                    usage=_usage(20, 10),
                ),
            ],
            abilities=_JSON_ABILITIES,
        )
        result = await structured_llm_call(
            llm,
            [ChatMessage(role="user", content="test")],
            SIMPLE_SCHEMA,
            "fn",
        )
        assert result.llm_calls == 2
        assert result.total_usage["prompt_tokens"] == 30
        assert result.total_usage["completion_tokens"] == 15
        assert result.total_usage["total_tokens"] == 45


# ======================================================================
# 10. Nested schema (Planner's steps array)
# ======================================================================


class TestNestedSchema:
    async def test_planner_steps_schema(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "task": {"type": "string"},
                            "dependencies": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["id", "task"],
                    },
                },
            },
            "required": ["steps"],
        }
        content = json.dumps(
            {
                "steps": [
                    {"id": "s1", "task": "research", "dependencies": []},
                    {
                        "id": "s2",
                        "task": "summarize",
                        "dependencies": ["s1"],
                    },
                ]
            }
        )
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(role="assistant", content=content),
                ),
            ],
        )
        result = await structured_llm_call(
            llm,
            [ChatMessage(role="user", content="test")],
            schema,
            "plan",
        )
        assert len(result.value["steps"]) == 2
        assert result.value["steps"][0]["id"] == "s1"
        assert result.value["steps"][1]["dependencies"] == ["s1"]
