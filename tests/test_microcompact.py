"""Tests for micro_compact rule-based tool result cleanup."""

from __future__ import annotations

from fim_one.core.memory.microcompact import micro_compact, _is_tool_result
from fim_one.core.model.types import ChatMessage


def _content_str(msg: ChatMessage) -> str:
    """Extract content as str, asserting it is a plain string."""
    assert isinstance(msg.content, str), f"Expected str content, got {type(msg.content)}"
    return msg.content


class TestIsToolResult:
    """Unit tests for the _is_tool_result helper."""

    def test_native_tool_message(self) -> None:
        msg = ChatMessage(role="tool", content="result", tool_call_id="tc_1")
        assert _is_tool_result(msg) is True

    def test_json_mode_observation(self) -> None:
        msg = ChatMessage(role="user", content="Observation: success")
        assert _is_tool_result(msg) is True

    def test_plain_user_message(self) -> None:
        msg = ChatMessage(role="user", content="Hello, please help")
        assert _is_tool_result(msg) is False

    def test_assistant_message(self) -> None:
        msg = ChatMessage(role="assistant", content="Sure thing")
        assert _is_tool_result(msg) is False

    def test_system_message(self) -> None:
        msg = ChatMessage(role="system", content="You are helpful")
        assert _is_tool_result(msg) is False

    def test_observation_prefix_exact(self) -> None:
        """Content must start with 'Observation: ' (with space after colon)."""
        msg = ChatMessage(role="user", content="Observation:no space")
        assert _is_tool_result(msg) is False

    def test_tool_role_no_content(self) -> None:
        msg = ChatMessage(role="tool", content=None, tool_call_id="tc_1")
        assert _is_tool_result(msg) is True


class TestMicroCompact:
    """Unit tests for the micro_compact function."""

    def test_no_tool_results(self) -> None:
        messages = [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content="hello"),
        ]
        result = micro_compact(messages)
        assert len(result) == 3
        assert result[0].content == "sys"
        assert result[1].content == "hi"
        assert result[2].content == "hello"

    def test_fewer_than_threshold(self) -> None:
        """When tool results <= keep_recent, nothing is cleared."""
        messages = [
            ChatMessage(role="user", content="query"),
            ChatMessage(role="tool", content="result1", tool_call_id="tc_1"),
            ChatMessage(role="tool", content="result2", tool_call_id="tc_2"),
        ]
        result = micro_compact(messages, keep_recent=6)
        assert result[1].content == "result1"
        assert result[2].content == "result2"

    def test_clears_old_native_tool_results(self) -> None:
        """Old native tool results are replaced with placeholder."""
        messages = [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="query"),
            # 4 tool results, keep 2 most recent
            ChatMessage(role="tool", content="old_result_1", tool_call_id="tc_1"),
            ChatMessage(role="tool", content="old_result_2", tool_call_id="tc_2"),
            ChatMessage(role="tool", content="new_result_3", tool_call_id="tc_3"),
            ChatMessage(role="tool", content="new_result_4", tool_call_id="tc_4"),
        ]
        result = micro_compact(messages, keep_recent=2)

        # First two tool results should be cleared
        assert "result cleared" in _content_str(result[2])
        assert "result cleared" in _content_str(result[3])
        # tool_call_id must be preserved
        assert result[2].tool_call_id == "tc_1"
        assert result[3].tool_call_id == "tc_2"
        # Last two should be intact
        assert result[4].content == "new_result_3"
        assert result[5].content == "new_result_4"

    def test_clears_old_json_mode_observations(self) -> None:
        """Old JSON-mode observation messages are replaced."""
        messages = [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="query"),
            ChatMessage(role="assistant", content="action1"),
            ChatMessage(role="user", content="Observation: old data"),
            ChatMessage(role="assistant", content="action2"),
            ChatMessage(role="user", content="Observation: new data"),
        ]
        result = micro_compact(messages, keep_recent=1)

        # Old observation cleared
        assert "result cleared" in _content_str(result[3])
        assert result[3].role == "user"
        # Recent observation kept
        assert result[5].content == "Observation: new data"

    def test_mixed_mode_tool_results(self) -> None:
        """Both native and JSON-mode tool results are counted together."""
        messages = [
            ChatMessage(role="system", content="sys"),
            # JSON-mode observation (oldest)
            ChatMessage(role="user", content="Observation: json_result_1"),
            # Native tool results
            ChatMessage(role="tool", content="native_result_2", tool_call_id="tc_1"),
            ChatMessage(role="tool", content="native_result_3", tool_call_id="tc_2"),
        ]
        result = micro_compact(messages, keep_recent=2)

        # Oldest (json observation) cleared
        assert "result cleared" in _content_str(result[1])
        # Two most recent kept
        assert result[2].content == "native_result_2"
        assert result[3].content == "native_result_3"

    def test_non_tool_messages_untouched(self) -> None:
        """System, user (non-observation), and assistant messages are never modified."""
        messages = [
            ChatMessage(role="system", content="important system"),
            ChatMessage(role="user", content="user question"),
            ChatMessage(role="assistant", content="thinking..."),
            ChatMessage(role="tool", content="old", tool_call_id="tc_1"),
            ChatMessage(role="tool", content="new", tool_call_id="tc_2"),
        ]
        result = micro_compact(messages, keep_recent=1)

        assert result[0].content == "important system"
        assert result[1].content == "user question"
        assert result[2].content == "thinking..."
        assert "result cleared" in _content_str(result[3])
        assert result[4].content == "new"

    def test_returns_new_list(self) -> None:
        """micro_compact returns a new list, original is not mutated."""
        messages = [
            ChatMessage(role="tool", content="old", tool_call_id="tc_1"),
            ChatMessage(role="tool", content="new", tool_call_id="tc_2"),
        ]
        original_content = messages[0].content
        result = micro_compact(messages, keep_recent=1)

        # Original message not mutated
        assert messages[0].content == original_content
        # Result has different list identity
        assert result is not messages

    def test_keep_recent_zero(self) -> None:
        """keep_recent=0 clears all tool results."""
        messages = [
            ChatMessage(role="tool", content="r1", tool_call_id="tc_1"),
            ChatMessage(role="tool", content="r2", tool_call_id="tc_2"),
        ]
        result = micro_compact(messages, keep_recent=0)
        assert "result cleared" in _content_str(result[0])
        assert "result cleared" in _content_str(result[1])

    def test_keep_recent_negative_treated_as_zero(self) -> None:
        """Negative keep_recent is clamped to 0."""
        messages = [
            ChatMessage(role="tool", content="r1", tool_call_id="tc_1"),
        ]
        result = micro_compact(messages, keep_recent=-1)
        assert "result cleared" in _content_str(result[0])

    def test_pinned_tool_results_still_cleared(self) -> None:
        """Pinned attribute is preserved but content is still cleared."""
        messages = [
            ChatMessage(role="tool", content="pinned_old", tool_call_id="tc_1", pinned=True),
            ChatMessage(role="tool", content="recent", tool_call_id="tc_2"),
        ]
        result = micro_compact(messages, keep_recent=1)
        assert "result cleared" in _content_str(result[0])
        assert result[0].pinned is True
        assert result[0].tool_call_id == "tc_1"

    def test_default_keep_recent_is_six(self) -> None:
        """The default keep_recent value is 6."""
        # Build 8 tool results
        messages = [
            ChatMessage(role="tool", content=f"r{i}", tool_call_id=f"tc_{i}")
            for i in range(8)
        ]
        result = micro_compact(messages)  # default keep_recent=6

        # First 2 cleared, last 6 kept
        assert "result cleared" in _content_str(result[0])
        assert "result cleared" in _content_str(result[1])
        for i in range(2, 8):
            assert result[i].content == f"r{i}"

    def test_exactly_keep_recent_no_change(self) -> None:
        """When count equals keep_recent exactly, nothing is cleared."""
        messages = [
            ChatMessage(role="tool", content=f"r{i}", tool_call_id=f"tc_{i}")
            for i in range(6)
        ]
        result = micro_compact(messages, keep_recent=6)
        for i in range(6):
            assert result[i].content == f"r{i}"

    def test_empty_messages(self) -> None:
        result = micro_compact([])
        assert result == []

    def test_placeholder_mentions_keep_count(self) -> None:
        """The placeholder text includes the keep_recent value."""
        messages = [
            ChatMessage(role="tool", content="old", tool_call_id="tc_1"),
            ChatMessage(role="tool", content="new", tool_call_id="tc_2"),
        ]
        result = micro_compact(messages, keep_recent=1)
        assert "1" in _content_str(result[0])
