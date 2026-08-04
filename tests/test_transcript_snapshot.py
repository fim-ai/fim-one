"""Tests for pre-compaction transcript snapshots and budget-truncation offload."""

from __future__ import annotations

import json

from fim_one.core.agent.workspace import AgentWorkspace
from fim_one.core.memory.context_guard import ContextGuard
from fim_one.core.model import ChatMessage
from fim_one.core.model.types import ToolCallRequest


class TestSaveTranscript:
    def test_writes_jsonl_with_roles_and_tools(self, tmp_path) -> None:
        ws = AgentWorkspace("conv1", base_dir=str(tmp_path))
        messages = [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="hello"),
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[ToolCallRequest(id="1", name="echo", arguments={})],
                reasoning_content="thinking...",
            ),
            ChatMessage(role="tool", content="result", tool_call_id="1"),
        ]

        uri = ws.save_transcript(messages)

        assert uri.startswith("workspace://transcript_")
        filename = uri[len("workspace://"):]
        # Transcripts are runtime-internal — they live in the hidden subdir
        # (kept out of the shared tool view) but stay readable by filename.
        assert ws.read_file(filename)
        lines = (
            (tmp_path / "conv1" / ".fim_internal" / filename)
            .read_text()
            .strip()
            .splitlines()
        )
        assert len(lines) == 4
        records = [json.loads(line) for line in lines]
        assert records[1] == {"role": "user", "content": "hello"}
        assert records[2]["tool_calls"] == ["echo"]
        assert records[2]["reasoning"] == "thinking..."
        assert records[2]["content"] == "[non-text content]"


class TestContextGuardTranscriptSink:
    async def test_sink_called_before_lossy_compact(self, tmp_path) -> None:
        # No compact_llm → smart_truncate path; tiny budget forces compaction.
        guard = ContextGuard(compact_llm=None, default_budget=10)
        captured: list[list[ChatMessage]] = []
        guard.set_transcript_sink(lambda msgs: captured.append(list(msgs)))

        messages = [
            ChatMessage(role="user", content="x" * 500),
            ChatMessage(role="assistant", content="y" * 500),
        ]
        await guard.check_and_compact(messages)

        assert len(captured) == 1
        assert len(captured[0]) == 2

    async def test_sink_not_called_under_budget(self) -> None:
        guard = ContextGuard(compact_llm=None, default_budget=100_000)
        captured: list[list[ChatMessage]] = []
        guard.set_transcript_sink(lambda msgs: captured.append(list(msgs)))

        await guard.check_and_compact([ChatMessage(role="user", content="hi")])

        assert captured == []

    async def test_sink_error_does_not_break_compact(self) -> None:
        guard = ContextGuard(compact_llm=None, default_budget=10)

        def broken_sink(msgs: list[ChatMessage]) -> None:
            raise RuntimeError("disk full")

        guard.set_transcript_sink(broken_sink)
        messages = [ChatMessage(role="user", content="x" * 500)]

        # Must not raise — the sink error is swallowed and compaction runs.
        result = await guard.check_and_compact(messages)

        assert isinstance(result, list)
