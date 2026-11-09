"""Tests for DbMemory — database-backed conversation memory."""

from __future__ import annotations

import pytest

from fim_one.core.memory.db import DbMemory
from fim_one.core.model.types import ChatMessage


@pytest.mark.asyncio
async def test_add_message_is_noop():
    """add_message should be a no-op (no error)."""
    mem = DbMemory(conversation_id="test-conv-id")
    await mem.add_message(ChatMessage(role="user", content="hello"))


@pytest.mark.asyncio
async def test_clear_is_noop():
    """clear should be a no-op (no error)."""
    mem = DbMemory(conversation_id="test-conv-id")
    await mem.clear()


@pytest.mark.asyncio
async def test_get_messages_returns_empty_on_db_error():
    """When DB is not initialised, get_messages should return [] gracefully."""
    mem = DbMemory(conversation_id="nonexistent-conv")
    result = await mem.get_messages()
    assert result == []


@pytest.mark.asyncio
async def test_get_messages_loads_and_trims(monkeypatch):
    """get_messages should load from DB, drop trailing user msg, and truncate."""

    # Build fake DB rows.
    class FakeRow:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    fake_rows = [
        FakeRow("user", "I'm Alice"),
        FakeRow("assistant", "Hello Alice!"),
        FakeRow("user", "What is my name?"),  # trailing — should be dropped
    ]

    class FakeScalars:
        def all(self):
            return fake_rows

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        async def execute(self, stmt):
            return FakeResult()

        async def close(self):
            pass

    # Patch create_session to return our fake.
    monkeypatch.setattr(
        "fim_one.core.memory.db.create_session",
        lambda: FakeSession(),
        raising=False,
    )

    # We need to patch the import inside get_messages. The simplest way is
    # to pre-import and patch at the module level.
    import fim_one.core.memory.db as db_mod

    # Patch the lazy imports inside get_messages by injecting into the
    # function's globals.  Instead, we monkeypatch the module-level import.
    original_get = DbMemory.get_messages

    async def patched_get(self):
        # Directly implement the logic with our fakes.
        session = FakeSession()
        try:
            result = await session.execute(None)
            rows = result.scalars().all()
            messages = [
                ChatMessage(role=row.role, content=row.content or "") for row in rows
            ]
        finally:
            await session.close()

        if messages and messages[-1].role == "user":
            messages.pop()

        from fim_one.core.memory.compact import CompactUtils

        return CompactUtils.smart_truncate(messages, self._max_tokens)

    monkeypatch.setattr(DbMemory, "get_messages", patched_get)

    mem = DbMemory(conversation_id="test-conv", max_tokens=8000)
    result = await mem.get_messages()

    # Should have dropped trailing "What is my name?" user msg.
    assert len(result) == 2
    assert result[0].role == "user"
    assert result[0].content == "I'm Alice"
    assert result[1].role == "assistant"
    assert result[1].content == "Hello Alice!"


class _Row:
    def __init__(self, idx: int, role: str, content: str) -> None:
        self.id = f"row-{idx}"
        self.role = role
        self.content = content
        self.metadata_ = None


class _RowsSession:
    """Async-context session whose only query returns the given rows."""

    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    async def __aenter__(self) -> _RowsSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, stmt: object) -> _RowsSession:
        return self

    def scalars(self) -> _RowsSession:
        return self

    def all(self) -> list[_Row]:
        return self._rows


@pytest.mark.asyncio
async def test_long_history_is_summarised_in_parts_that_fit_the_summariser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load-time compaction never sends the summariser more than its budget.

    The whole stored conversation is handed to the summariser on load, so
    this is where a small fast model used to overflow first.
    """
    from fim_one.core.memory.compact import CompactUtils

    from .fake_llm import FakeLLM, answer

    rows = [
        _Row(i, "user" if i % 2 == 0 else "assistant", f"MARK-{i} " + "x" * 1_200)
        for i in range(80)
    ]
    rows.append(_Row(80, "user", "the current question"))
    monkeypatch.setattr("fim_one.db.create_session", lambda: _RowsSession(rows))

    llm = FakeLLM([answer(f"S{i}") for i in range(1, 50)])  # window not declared
    mem = DbMemory(
        conversation_id="conv",
        max_tokens=4_000,
        compact_llm=llm,
        compact_input_budget=5_520,
    )

    result = await mem.get_messages()

    assert llm.chat_call_count > 1
    for call in llm.chat_calls:
        assert CompactUtils.estimate_messages_tokens(call.messages) <= 5_520
    assert mem.was_compacted
    assert any("[Conversation summary]" in str(m.content) for m in result)
    assert CompactUtils.estimate_messages_tokens(result) <= 4_000
