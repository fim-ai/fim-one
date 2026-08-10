"""Tests for thinking / reasoning block persistence across turns.

Covers the claude-code-insights A3 fix: multi-turn conversations must
round-trip the assistant ``reasoning_content`` + Anthropic ``signature``
through the DB so subsequent turns can replay the thinking block
unchanged (the provider rejects altered signatures).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from fim_one.core.memory.db import DbMemory
from fim_one.core.model.base import BaseLLM
from fim_one.core.model.openai_compatible import OpenAICompatibleLLM
from fim_one.core.model.types import ChatMessage, StreamChunk
from fim_one.web.api.chat import _extract_final_thinking


# ---------------------------------------------------------------------------
# ChatMessage serialisation
# ---------------------------------------------------------------------------


class TestChatMessageSerialisation:
    """Ensure ``to_openai_dict()`` serialises thinking fields on demand."""

    def test_includes_reasoning_and_signature_when_set(self) -> None:
        msg = ChatMessage(
            role="assistant",
            content="The answer is 42.",
            reasoning_content="Step-by-step: ...",
            signature="sig_opaque_abc123",
        )
        d = msg.to_openai_dict()
        assert d["role"] == "assistant"
        assert d["content"] == "The answer is 42."
        assert d["reasoning_content"] == "Step-by-step: ..."
        assert d["signature"] == "sig_opaque_abc123"

    def test_omits_thinking_fields_when_none(self) -> None:
        msg = ChatMessage(role="assistant", content="Hi")
        d = msg.to_openai_dict()
        assert "reasoning_content" not in d
        assert "signature" not in d

    def test_omits_thinking_fields_when_empty_string(self) -> None:
        msg = ChatMessage(
            role="assistant",
            content="Hi",
            reasoning_content="",
            signature="",
        )
        d = msg.to_openai_dict()
        assert "reasoning_content" not in d
        assert "signature" not in d

    def test_serialises_thinking_only_assistant(self) -> None:
        """Assistant with thinking but no text content still serialises."""
        msg = ChatMessage(
            role="assistant",
            content=None,
            reasoning_content="mid-turn thought",
            signature="sig_mid",
        )
        d = msg.to_openai_dict()
        assert d["role"] == "assistant"
        # content is None → key omitted
        assert "content" not in d
        assert d["reasoning_content"] == "mid-turn thought"
        assert d["signature"] == "sig_mid"

    def test_signature_included_without_reasoning(self) -> None:
        """Edge case: signature is present but reasoning is not yet known."""
        msg = ChatMessage(
            role="assistant",
            content="ok",
            signature="sig_only",
        )
        d = msg.to_openai_dict()
        assert d["signature"] == "sig_only"
        assert "reasoning_content" not in d


# ---------------------------------------------------------------------------
# BaseLLM default abilities
# ---------------------------------------------------------------------------


class TestBaseLLMAbilities:
    """The abstract base should advertise ``thinking`` as False by default."""

    def test_default_abilities_includes_thinking_false(self) -> None:
        class _Dummy(BaseLLM):
            async def chat(
                self,
                messages: list[ChatMessage],
                **_: Any,
            ) -> Any:  # pragma: no cover — unused
                raise NotImplementedError

            async def stream_chat(
                self,
                messages: list[ChatMessage],
                **_: Any,
            ) -> Any:  # pragma: no cover — unused
                if False:
                    yield None
                raise NotImplementedError

        d = _Dummy()
        assert d.abilities["thinking"] is False


# ---------------------------------------------------------------------------
# OpenAICompatibleLLM thinking capability
# ---------------------------------------------------------------------------


class TestThinkingAbility:
    """Model-id based gating for thinking capability."""

    @pytest.mark.parametrize(
        "model_id",
        [
            "claude-opus-4",
            "claude-opus-4-20250514",
            "claude-sonnet-4-20250514",
            "claude-haiku-4-20251001",
        ],
    )
    def test_claude_4_family_supports_thinking(self, model_id: str) -> None:
        llm = OpenAICompatibleLLM(
            api_key="test",
            base_url="https://api.anthropic.com/v1",
            model=model_id,
            retry_config=None,
            rate_limit_config=None,
        )
        assert llm.abilities["thinking"] is True

    @pytest.mark.parametrize(
        "model_id",
        [
            "gpt-4o-mini",
            "claude-3-5-sonnet-20241022",
            "gemini-1.5-pro",
        ],
    )
    def test_non_reasoning_models_skip_thinking(self, model_id: str) -> None:
        llm = OpenAICompatibleLLM(
            api_key="test",
            base_url="https://example.com/v1",
            model=model_id,
            retry_config=None,
            rate_limit_config=None,
        )
        assert llm.abilities["thinking"] is False

    def test_reasoning_effort_implies_thinking(self) -> None:
        llm = OpenAICompatibleLLM(
            api_key="test",
            base_url="https://example.com/v1",
            model="custom-model",
            retry_config=None,
            rate_limit_config=None,
            reasoning_effort="medium",
        )
        assert llm.abilities["thinking"] is True

    def test_deepseek_r1_supports_thinking(self) -> None:
        llm = OpenAICompatibleLLM(
            api_key="test",
            base_url="https://example.com/v1",
            model="deepseek-r1-distill-llama-70b",
            retry_config=None,
            rate_limit_config=None,
        )
        assert llm.abilities["thinking"] is True


# ---------------------------------------------------------------------------
# Signature extraction from provider responses
# ---------------------------------------------------------------------------


class _Message:
    """Mimic the duck-typed message object litellm returns."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)
        # Ensure every attribute has a sensible default.
        for attr in (
            "role",
            "content",
            "tool_calls",
            "reasoning_content",
            "reasoning",
            "signature",
            "thinking_signature",
            "thinking_blocks",
        ):
            if not hasattr(self, attr):
                setattr(self, attr, None)
        if self.role is None:
            self.role = "assistant"


class _Choice:
    def __init__(self, message: _Message) -> None:
        self.message = message


class TestSignatureExtraction:
    """The parser must capture signatures regardless of which shape
    LiteLLM produces (flat attribute, dict, or nested thinking_blocks)."""

    def test_flat_signature_attribute(self) -> None:
        msg = _Message(
            content="final",
            reasoning_content="cot",
            signature="flat_sig",
        )
        parsed = OpenAICompatibleLLM._parse_choice_message(_Choice(msg))
        assert parsed.signature == "flat_sig"
        assert parsed.reasoning_content == "cot"

    def test_thinking_signature_alias(self) -> None:
        msg = _Message(content="x", thinking_signature="alt_sig")
        parsed = OpenAICompatibleLLM._parse_choice_message(_Choice(msg))
        assert parsed.signature == "alt_sig"

    def test_thinking_blocks_nested(self) -> None:
        msg = _Message(
            content="x",
            thinking_blocks=[
                {"type": "thinking", "thinking": "a", "signature": "sig_a"},
                {"type": "thinking", "thinking": "b", "signature": "sig_b"},
            ],
        )
        parsed = OpenAICompatibleLLM._parse_choice_message(_Choice(msg))
        # Most recent (last) block wins.
        assert parsed.signature == "sig_b"

    def test_missing_signature_returns_none(self) -> None:
        msg = _Message(content="x", reasoning_content="cot")
        parsed = OpenAICompatibleLLM._parse_choice_message(_Choice(msg))
        assert parsed.signature is None

    def test_empty_signature_treated_as_none(self) -> None:
        msg = _Message(content="x", signature="")
        parsed = OpenAICompatibleLLM._parse_choice_message(_Choice(msg))
        assert parsed.signature is None


# ---------------------------------------------------------------------------
# StreamChunk carries signature through the accumulator
# ---------------------------------------------------------------------------


class TestStreamChunkSignature:
    """StreamChunk should be able to carry a signature between the
    streaming parser and the accumulator in ``_stream_tool_decision``."""

    def test_stream_chunk_signature_field(self) -> None:
        chunk = StreamChunk(signature="sig_streamed")
        assert chunk.signature == "sig_streamed"
        # Other fields default to None.
        assert chunk.delta_content is None
        assert chunk.delta_reasoning is None


# ---------------------------------------------------------------------------
# DbMemory round-trip
# ---------------------------------------------------------------------------


class _FakeRow:
    def __init__(
        self,
        row_id: str,
        role: str,
        content: str,
        metadata_: dict[str, Any] | None = None,
    ) -> None:
        self.id = row_id
        self.role = role
        self.content = content
        self.metadata_ = metadata_


class _FakeScalars:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    def all(self) -> list[_FakeRow]:
        return self._rows


class _FakeResult:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self._rows)

    async def close(self) -> None:  # pragma: no cover — unused
        pass


@pytest.mark.asyncio
class TestDbMemoryRoundTrip:
    """Verify thinking content + signature survive DB read path."""

    async def test_thinking_round_trips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            _FakeRow("1", "user", "Hello"),
            _FakeRow(
                "2",
                "assistant",
                "Answer",
                metadata_={
                    "thinking": {
                        "content": "deliberate reasoning",
                        "signature": "sig_persist_123",
                    },
                },
            ),
            _FakeRow("3", "user", "follow-up"),  # trailing — dropped
        ]

        def _fake_create_session() -> _FakeSession:
            return _FakeSession(rows)

        monkeypatch.setattr(
            "fim_one.db.create_session",
            _fake_create_session,
            raising=False,
        )

        mem = DbMemory(conversation_id="conv-xyz", max_tokens=64_000)
        messages = await mem.get_messages()

        # trailing user dropped → [user("Hello"), assistant("Answer")]
        assert len(messages) == 2
        user_msg, assistant_msg = messages
        assert user_msg.role == "user"
        assert assistant_msg.role == "assistant"
        assert assistant_msg.reasoning_content == "deliberate reasoning"
        assert assistant_msg.signature == "sig_persist_123"

    async def test_reasoning_items_round_trip(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GPT-5.x replay state survives a conversation reload."""
        items = [{"type": "reasoning", "encrypted_content": "gAAAAA-persisted"}]
        rows = [
            _FakeRow("1", "user", "Hello"),
            _FakeRow(
                "2",
                "assistant",
                "Answer",
                metadata_={
                    "thinking": {
                        "content": "summary",
                        "signature": "",
                        "encrypted_items": items,
                    },
                },
            ),
        ]

        monkeypatch.setattr(
            "fim_one.db.create_session",
            lambda: _FakeSession(rows),
            raising=False,
        )

        mem = DbMemory(conversation_id="conv-gpt5", max_tokens=64_000)
        messages = await mem.get_messages()

        assistant_msg = messages[-1]
        assert assistant_msg.reasoning_items == items
        assert assistant_msg.signature is None

    async def test_malformed_items_are_ignored(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A hand-edited or legacy row must not crash the reload."""
        rows = [
            _FakeRow("1", "user", "Hello"),
            _FakeRow(
                "2",
                "assistant",
                "Answer",
                metadata_={"thinking": {"encrypted_items": "not-a-list"}},
            ),
        ]

        monkeypatch.setattr(
            "fim_one.db.create_session",
            lambda: _FakeSession(rows),
            raising=False,
        )

        mem = DbMemory(conversation_id="conv-bad", max_tokens=64_000)
        messages = await mem.get_messages()
        assert messages[-1].reasoning_items is None

    async def test_missing_thinking_leaves_message_clean(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rows = [
            _FakeRow("1", "user", "hi"),
            _FakeRow("2", "assistant", "ok", metadata_=None),
            _FakeRow("3", "user", "later"),
        ]

        def _fake_create_session() -> _FakeSession:
            return _FakeSession(rows)

        monkeypatch.setattr(
            "fim_one.db.create_session",
            _fake_create_session,
            raising=False,
        )

        mem = DbMemory(conversation_id="conv-xyz", max_tokens=64_000)
        messages = await mem.get_messages()
        assert len(messages) == 2
        assert messages[1].reasoning_content is None
        assert messages[1].signature is None

    async def test_partial_thinking_content_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rows = [
            _FakeRow("1", "user", "hi"),
            _FakeRow(
                "2",
                "assistant",
                "ok",
                metadata_={"thinking": {"content": "just cot"}},
            ),
            _FakeRow("3", "user", "x"),
        ]

        def _fake_create_session() -> _FakeSession:
            return _FakeSession(rows)

        monkeypatch.setattr(
            "fim_one.db.create_session",
            _fake_create_session,
            raising=False,
        )

        mem = DbMemory(conversation_id="conv-xyz", max_tokens=64_000)
        messages = await mem.get_messages()
        assert messages[1].reasoning_content == "just cot"
        assert messages[1].signature is None


# ---------------------------------------------------------------------------
# _extract_final_thinking helper (chat.py)
# ---------------------------------------------------------------------------


class TestExtractFinalThinking:
    """Helper used by chat.py to find the most recent assistant thinking."""

    def test_returns_none_for_empty(self) -> None:
        assert _extract_final_thinking([]) is None
        assert _extract_final_thinking(None) is None

    def test_returns_none_without_thinking(self) -> None:
        msgs: list[Any] = [
            ChatMessage(role="user", content="q"),
            ChatMessage(role="assistant", content="a"),
        ]
        assert _extract_final_thinking(msgs) is None

    def test_picks_last_assistant_with_thinking(self) -> None:
        msgs: list[Any] = [
            ChatMessage(
                role="assistant",
                content="earlier",
                reasoning_content="old",
                signature="sig_old",
            ),
            ChatMessage(role="tool", content="obs", tool_call_id="t1"),
            ChatMessage(
                role="assistant",
                content="later",
                reasoning_content="new",
                signature="sig_new",
            ),
        ]
        result = _extract_final_thinking(msgs)
        assert result is not None
        assert result["content"] == "new"
        assert result["signature"] == "sig_new"

    def test_picks_signature_only(self) -> None:
        msgs: list[Any] = [
            ChatMessage(
                role="assistant",
                content="x",
                signature="sig_alone",
            ),
        ]
        result = _extract_final_thinking(msgs)
        assert result is not None
        assert result["signature"] == "sig_alone"
        assert result["content"] == ""

    def test_captures_reasoning_items(self) -> None:
        """GPT-5.x encrypted items ride along under their own key."""
        items = [{"type": "reasoning", "encrypted_content": "gAAAAA"}]
        msgs: list[Any] = [
            ChatMessage(
                role="assistant",
                content="x",
                reasoning_content="summary",
                reasoning_items=items,
            ),
        ]
        result = _extract_final_thinking(msgs)
        assert result is not None
        assert result["encrypted_items"] == items
        assert result["content"] == "summary"

    def test_items_alone_are_enough_to_persist(self) -> None:
        """A turn with items but no summary text must still be stored."""
        msgs: list[Any] = [
            ChatMessage(
                role="assistant",
                content="x",
                reasoning_items=[{"type": "reasoning", "encrypted_content": "g"}],
            ),
        ]
        assert _extract_final_thinking(msgs) is not None

    def test_key_absent_when_no_items(self) -> None:
        msgs: list[Any] = [
            ChatMessage(role="assistant", content="x", reasoning_content="r"),
        ]
        result = _extract_final_thinking(msgs)
        assert result is not None
        assert "encrypted_items" not in result


# ---------------------------------------------------------------------------
# Reasoning items survive the rebuild paths
# ---------------------------------------------------------------------------


class TestReasoningItemsSurviveRebuilds:
    """Compaction rebuilds must not silently drop the replay state.

    Losing an item mid-conversation is worse than never having had it:
    the model re-derives its chain of thought from a history that claims
    it already reasoned.
    """

    @staticmethod
    def _items() -> list[dict[str, Any]]:
        return [{"type": "reasoning", "encrypted_content": "gAAAAA-1"}]

    def test_context_guard_truncation_preserves_items(self) -> None:
        from fim_one.core.memory.context_guard import ContextGuard

        guard = ContextGuard(default_budget=50_000, max_message_chars=100)
        messages = [
            ChatMessage(
                role="assistant",
                content="x" * 5000,
                reasoning_items=self._items(),
            ),
        ]
        out = guard._truncate_oversized(messages)
        assert out[0].content is not None
        assert len(out[0].content) < 5000  # truncation actually happened
        assert out[0].reasoning_items == self._items()

    def test_microcompact_placeholder_preserves_items(self) -> None:
        from fim_one.core.memory.microcompact import micro_compact

        messages = [
            ChatMessage(role="user", content="q"),
            ChatMessage(role="assistant", content="a", reasoning_items=self._items()),
            ChatMessage(role="tool", content="obs" * 500, tool_call_id="t1"),
            ChatMessage(role="tool", content="obs2" * 500, tool_call_id="t2"),
        ]
        out = micro_compact(messages, keep_recent=0)
        assistant = [m for m in out if m.role == "assistant"][0]
        assert assistant.reasoning_items == self._items()

    def test_normalize_merge_takes_the_tail_not_the_concatenation(self) -> None:
        """Items are an ordered per-turn sequence; splicing invents history."""
        from fim_one.core.model.normalize import normalize_alternating_messages

        first = [{"type": "reasoning", "encrypted_content": "first"}]
        second = [{"type": "reasoning", "encrypted_content": "second"}]
        out = normalize_alternating_messages(
            [
                ChatMessage(role="user", content="q"),
                ChatMessage(role="assistant", content="a", reasoning_items=first),
                ChatMessage(role="assistant", content="b", reasoning_items=second),
            ]
        )
        merged = [m for m in out if m.role == "assistant"][0]
        assert merged.reasoning_items == second


# ---------------------------------------------------------------------------
# Smoke test that AsyncMock still works against the ChatMessage API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_mock_llm_returns_thinking() -> None:
    """Sanity check: a mocked LLM can return a ChatMessage with thinking."""
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = type(
        "R",
        (),
        {
            "message": ChatMessage(
                role="assistant",
                content="hi",
                reasoning_content="cot",
                signature="sig",
            ),
            "usage": {},
        },
    )()

    res = await mock_llm.chat([])
    assert res.message.reasoning_content == "cot"
    assert res.message.signature == "sig"
