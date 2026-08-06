"""Tests for the AgentWorkspace system and workspace tools."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from fim_one.core.agent.workspace import AgentWorkspace, DEFAULT_OFFLOAD_THRESHOLD
from fim_one.core.agent.workspace_tools import (
    ListWorkspaceFilesTool,
    ReadWorkspaceFileTool,
    WriteHandoffTool,
)
from fim_one.core.agent import ReActAgent
from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.tool import BaseTool, ToolRegistry

from .conftest import FakeLLM


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture()
def workspace(tmp_path: Path) -> AgentWorkspace:
    """Create a workspace in a temporary directory."""
    return AgentWorkspace(
        conversation_id="test-conv-001",
        base_dir=str(tmp_path / "workspaces"),
    )


@pytest.fixture()
def workspace_small_threshold(tmp_path: Path) -> AgentWorkspace:
    """Create a workspace with a small offload threshold for testing."""
    return AgentWorkspace(
        conversation_id="test-conv-002",
        base_dir=str(tmp_path / "workspaces"),
        offload_threshold=100,
    )


# ======================================================================
# AgentWorkspace unit tests
# ======================================================================


class TestSaveToolOutput:
    """Tests for save_tool_output."""

    def test_creates_file_and_returns_uri(self, workspace: AgentWorkspace) -> None:
        uri = workspace.save_tool_output("my_tool", "hello world")
        assert uri.startswith("workspace://")
        assert "tool_result_my_tool_" in uri
        # File should exist on disk.
        filename = uri.replace("workspace://", "")
        filepath = workspace.directory / filename
        assert filepath.exists()
        assert filepath.read_text() == "hello world"

    def test_sanitizes_tool_name(self, workspace: AgentWorkspace) -> None:
        uri = workspace.save_tool_output("path/to\\tool", "data")
        filename = uri.replace("workspace://", "")
        assert "/" not in filename.replace("workspace://", "")
        assert "\\" not in filename

    def test_unique_filenames(self, workspace: AgentWorkspace) -> None:
        uri1 = workspace.save_tool_output("tool", "data1")
        uri2 = workspace.save_tool_output("tool", "data2")
        assert uri1 != uri2


class TestMaybeOffload:
    """Tests for maybe_offload."""

    def test_short_output_unchanged(self, workspace: AgentWorkspace) -> None:
        short = "x" * 100
        result = workspace.maybe_offload("tool", short)
        assert result == short

    def test_long_output_offloaded(self, workspace_small_threshold: AgentWorkspace) -> None:
        ws = workspace_small_threshold
        long_output = "x" * 200
        result = ws.maybe_offload("tool", long_output)
        assert "workspace://" in result
        assert "read_workspace_file" in result
        assert "200 chars" in result

    def test_offload_notice_leads_and_is_imperative(
        self, workspace_small_threshold: AgentWorkspace,
    ) -> None:
        """The pointer banner must come BEFORE the preview and forbid re-fetching.

        A trailing "was saved" note got skimmed past: an observed run
        re-fetched the same endpoint ~50 times instead of reading the file.
        """
        ws = workspace_small_threshold
        result = ws.maybe_offload("tool", "x" * 200)
        banner, _, preview = result.partition("\n\n")
        assert "workspace://" in banner
        assert "Do NOT" in banner
        assert "read_workspace_file" in banner
        assert preview.startswith("x")

    def test_exactly_at_threshold_not_offloaded(
        self, workspace_small_threshold: AgentWorkspace,
    ) -> None:
        ws = workspace_small_threshold
        exact = "x" * 100  # == threshold
        result = ws.maybe_offload("tool", exact)
        assert result == exact

    def test_one_over_threshold_offloaded(
        self, workspace_small_threshold: AgentWorkspace,
    ) -> None:
        ws = workspace_small_threshold
        over = "x" * 101
        result = ws.maybe_offload("tool", over)
        assert "workspace://" in result


class TestReadFile:
    """Tests for read_file."""

    def test_read_full_file(self, workspace: AgentWorkspace) -> None:
        workspace.save_tool_output("tool", "line0\nline1\nline2")
        files = workspace.list_files()
        filename = files[0]["name"]
        content = workspace.read_file(str(filename))
        assert content == "line0\nline1\nline2"

    def test_read_line_range(self, workspace: AgentWorkspace) -> None:
        workspace.save_tool_output("tool", "line0\nline1\nline2\nline3")
        files = workspace.list_files()
        filename = files[0]["name"]
        content = workspace.read_file(str(filename), start_line=1, end_line=3)
        assert content == "line1\nline2"

    def test_read_from_offset_to_end(self, workspace: AgentWorkspace) -> None:
        workspace.save_tool_output("tool", "line0\nline1\nline2")
        files = workspace.list_files()
        filename = files[0]["name"]
        content = workspace.read_file(str(filename), start_line=2)
        assert content == "line2"

    def test_file_not_found(self, workspace: AgentWorkspace) -> None:
        with pytest.raises(FileNotFoundError, match="Workspace file not found"):
            workspace.read_file("nonexistent.txt")

    def test_path_traversal_blocked(self, workspace: AgentWorkspace) -> None:
        with pytest.raises(ValueError, match="path traversal"):
            workspace.read_file("../../../etc/passwd")

    def test_slash_in_filename_blocked(self, workspace: AgentWorkspace) -> None:
        with pytest.raises(ValueError, match="path traversal"):
            workspace.read_file("sub/file.txt")


class TestListFiles:
    """Tests for list_files."""

    def test_empty_workspace(self, workspace: AgentWorkspace) -> None:
        assert workspace.list_files() == []

    def test_lists_files_with_metadata(self, workspace: AgentWorkspace) -> None:
        workspace.save_tool_output("tool_a", "data_a")
        workspace.save_tool_output("tool_b", "data_b")
        files = workspace.list_files()
        assert len(files) == 2
        for f in files:
            assert "name" in f
            assert "size_bytes" in f
            assert "created_at" in f
            assert f["size_bytes"] > 0

    def test_sorted_by_creation_desc(self, workspace: AgentWorkspace) -> None:
        workspace.save_tool_output("tool_a", "data_a")
        # Ensure different mtime.
        time.sleep(0.05)
        workspace.save_tool_output("tool_b", "data_b")
        files = workspace.list_files()
        # Most recent first.
        assert "tool_b" in str(files[0]["name"])
        assert "tool_a" in str(files[1]["name"])


class TestHandoff:
    """Tests for handoff note management."""

    def test_write_handoff_creates_file(self, workspace: AgentWorkspace) -> None:
        uri = workspace.write_handoff("# Summary\nKey findings here.")
        assert uri.startswith("workspace://HANDOFF_")
        assert uri.endswith(".md")

    def test_read_latest_handoff(self, workspace: AgentWorkspace) -> None:
        workspace.write_handoff("first handoff")
        time.sleep(1.1)  # Ensure different timestamps (1-second resolution in filename).
        workspace.write_handoff("second handoff")
        latest = workspace.read_latest_handoff()
        assert latest == "second handoff"

    def test_read_latest_handoff_empty_workspace(self, workspace: AgentWorkspace) -> None:
        assert workspace.read_latest_handoff() is None


class TestCleanup:
    """Tests for workspace cleanup."""

    def test_deletes_old_files(self, workspace: AgentWorkspace) -> None:
        # Create a file and backdate its mtime.
        workspace.save_tool_output("old_tool", "old data")
        files = workspace.list_files()
        old_file = workspace.directory / str(files[0]["name"])
        # Set mtime to 100 hours ago.
        old_time = time.time() - 100 * 3600
        os.utime(old_file, (old_time, old_time))

        # Create a recent file.
        workspace.save_tool_output("new_tool", "new data")

        deleted = workspace.cleanup(max_age_hours=72)
        assert deleted == 1
        remaining = workspace.list_files()
        assert len(remaining) == 1
        assert "new_tool" in str(remaining[0]["name"])

    def test_no_files_deleted_when_all_recent(self, workspace: AgentWorkspace) -> None:
        workspace.save_tool_output("tool", "data")
        deleted = workspace.cleanup(max_age_hours=72)
        assert deleted == 0

    def test_cleanup_empty_workspace(self, workspace: AgentWorkspace) -> None:
        deleted = workspace.cleanup()
        assert deleted == 0


class TestProperties:
    """Tests for workspace properties."""

    def test_conversation_id(self, workspace: AgentWorkspace) -> None:
        assert workspace.conversation_id == "test-conv-001"

    def test_directory_created(self, workspace: AgentWorkspace) -> None:
        assert workspace.directory.exists()
        assert workspace.directory.is_dir()

    def test_offload_threshold_default(self, workspace: AgentWorkspace) -> None:
        assert workspace.offload_threshold == DEFAULT_OFFLOAD_THRESHOLD

    def test_offload_threshold_custom(
        self, workspace_small_threshold: AgentWorkspace,
    ) -> None:
        assert workspace_small_threshold.offload_threshold == 100


# ======================================================================
# Workspace Tools tests
# ======================================================================


class TestReadWorkspaceFileTool:
    """Tests for the read_workspace_file builtin tool."""

    @pytest.mark.asyncio
    async def test_reads_file(self, workspace: AgentWorkspace) -> None:
        workspace.save_tool_output("tool", "content here")
        files = workspace.list_files()
        filename = str(files[0]["name"])

        tool = ReadWorkspaceFileTool(workspace)
        result = await tool.run(filename=filename)
        assert result == "content here"

    @pytest.mark.asyncio
    async def test_strips_workspace_prefix(self, workspace: AgentWorkspace) -> None:
        uri = workspace.save_tool_output("tool", "data")
        tool = ReadWorkspaceFileTool(workspace)
        result = await tool.run(filename=uri)
        assert result == "data"

    @pytest.mark.asyncio
    async def test_line_range(self, workspace: AgentWorkspace) -> None:
        workspace.save_tool_output("tool", "a\nb\nc\nd")
        files = workspace.list_files()
        filename = str(files[0]["name"])

        tool = ReadWorkspaceFileTool(workspace)
        result = await tool.run(filename=filename, start_line=1, end_line=3)
        assert result == "b\nc"

    @pytest.mark.asyncio
    async def test_file_not_found(self, workspace: AgentWorkspace) -> None:
        tool = ReadWorkspaceFileTool(workspace)
        result = await tool.run(filename="nonexistent.txt")
        assert "[Error]" in result

    @pytest.mark.asyncio
    async def test_empty_filename(self, workspace: AgentWorkspace) -> None:
        tool = ReadWorkspaceFileTool(workspace)
        result = await tool.run(filename="")
        assert "[Error]" in result

    @pytest.mark.asyncio
    async def test_path_traversal(self, workspace: AgentWorkspace) -> None:
        tool = ReadWorkspaceFileTool(workspace)
        result = await tool.run(filename="../../../etc/passwd")
        assert "[Error]" in result

    def test_tool_properties(self, workspace: AgentWorkspace) -> None:
        tool = ReadWorkspaceFileTool(workspace)
        assert tool.name == "read_workspace_file"
        assert tool.category == "workspace"
        assert "filename" in tool.parameters_schema["properties"]
        assert "filename" in tool.parameters_schema["required"]


class TestListWorkspaceFilesTool:
    """Tests for the list_workspace_files builtin tool."""

    @pytest.mark.asyncio
    async def test_empty_workspace(self, workspace: AgentWorkspace) -> None:
        tool = ListWorkspaceFilesTool(workspace)
        result = await tool.run()
        assert result == "No files in workspace."

    @pytest.mark.asyncio
    async def test_lists_files_json(self, workspace: AgentWorkspace) -> None:
        workspace.save_tool_output("tool_a", "data_a")
        workspace.save_tool_output("tool_b", "data_b")

        tool = ListWorkspaceFilesTool(workspace)
        result = await tool.run()
        parsed = json.loads(result)
        assert len(parsed) == 2
        assert all("name" in f for f in parsed)

    def test_tool_properties(self, workspace: AgentWorkspace) -> None:
        tool = ListWorkspaceFilesTool(workspace)
        assert tool.name == "list_workspace_files"
        assert tool.category == "workspace"


class TestWriteHandoffTool:
    """Tests for the write_handoff builtin tool."""

    @pytest.mark.asyncio
    async def test_writes_handoff(self, workspace: AgentWorkspace) -> None:
        tool = WriteHandoffTool(workspace)
        result = await tool.run(summary="# Key findings\n- Item 1\n- Item 2")
        assert "workspace://HANDOFF_" in result
        # Verify the content was written.
        latest = workspace.read_latest_handoff()
        assert latest is not None
        assert "Key findings" in latest

    @pytest.mark.asyncio
    async def test_empty_summary(self, workspace: AgentWorkspace) -> None:
        tool = WriteHandoffTool(workspace)
        result = await tool.run(summary="")
        assert "[Error]" in result

    def test_tool_properties(self, workspace: AgentWorkspace) -> None:
        tool = WriteHandoffTool(workspace)
        assert tool.name == "write_handoff"
        assert tool.category == "workspace"
        assert "summary" in tool.parameters_schema["properties"]
        assert "summary" in tool.parameters_schema["required"]


# ======================================================================
# Integration with ReActAgent
# ======================================================================


class LargeOutputTool(BaseTool):
    """A tool that produces output exceeding a given length."""

    def __init__(self, output_size: int = 200) -> None:
        self._output_size = output_size

    @property
    def name(self) -> str:
        return "large_output"

    @property
    def description(self) -> str:
        return "Produces a large output."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        return "x" * self._output_size


def _tool_call_response(
    tool_name: str,
    tool_args: dict[str, Any],
    reasoning: str = "calling tool",
) -> LLMResult:
    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps({
                "type": "tool_call",
                "reasoning": reasoning,
                "tool_name": tool_name,
                "tool_args": tool_args,
            }),
        ),
    )


def _final_answer_response(answer: str) -> LLMResult:
    return LLMResult(
        message=ChatMessage(
            role="assistant",
            content=json.dumps({
                "type": "final_answer",
                "reasoning": "done",
                "answer": answer,
            }),
        ),
    )


class TestAgentWorkspaceIntegration:
    """Tests for workspace integration in ReActAgent."""

    def test_workspace_tools_registered(self, tmp_path: Path) -> None:
        workspace = AgentWorkspace(
            "conv-int-001", base_dir=str(tmp_path / "ws"),
        )
        registry = ToolRegistry()
        llm = FakeLLM(responses=[_final_answer_response("done")])
        agent = ReActAgent(llm=llm, tools=registry, workspace=workspace)

        tool_names = [t.name for t in agent.tools.list_tools()]
        assert "read_workspace_file" in tool_names
        assert "list_workspace_files" in tool_names
        assert "write_handoff" in tool_names

    def test_no_workspace_no_tools(self) -> None:
        registry = ToolRegistry()
        llm = FakeLLM(responses=[_final_answer_response("done")])
        agent = ReActAgent(llm=llm, tools=registry, workspace=None)

        tool_names = [t.name for t in agent.tools.list_tools()]
        assert "read_workspace_file" not in tool_names

    @pytest.mark.asyncio
    async def test_auto_offload_json_mode(self, tmp_path: Path) -> None:
        """In JSON mode, large tool outputs should be offloaded."""
        workspace = AgentWorkspace(
            "conv-offload-001",
            base_dir=str(tmp_path / "ws"),
            offload_threshold=100,
        )
        registry = ToolRegistry()
        large_tool = LargeOutputTool(output_size=200)
        registry.register(large_tool)

        llm = FakeLLM(responses=[
            _tool_call_response("large_output", {}),
            _final_answer_response("done"),
        ])
        agent = ReActAgent(
            llm=llm,
            tools=registry,
            workspace=workspace,
            max_iterations=5,
        )
        result = await agent.run("test query")

        # The workspace should have a saved file.
        files = workspace.list_files()
        tool_files = [f for f in files if str(f["name"]).startswith("tool_result_")]
        assert len(tool_files) >= 1

        # The observation in the step should contain the full output
        # (StepResult stores original), but the message to the LLM
        # should have been truncated.
        assert result.steps[0].observation == "x" * 200

    @pytest.mark.asyncio
    async def test_no_offload_below_threshold(self, tmp_path: Path) -> None:
        """Small tool outputs should NOT be offloaded."""
        workspace = AgentWorkspace(
            "conv-no-offload",
            base_dir=str(tmp_path / "ws"),
            offload_threshold=1000,
        )
        registry = ToolRegistry()
        small_tool = LargeOutputTool(output_size=50)
        registry.register(small_tool)

        llm = FakeLLM(responses=[
            _tool_call_response("large_output", {}),
            _final_answer_response("done"),
        ])
        agent = ReActAgent(
            llm=llm,
            tools=registry,
            workspace=workspace,
            max_iterations=5,
        )
        await agent.run("test query")

        # No files should have been offloaded.
        files = workspace.list_files()
        tool_files = [f for f in files if str(f["name"]).startswith("tool_result_")]
        assert len(tool_files) == 0

    @pytest.mark.asyncio
    async def test_handoff_injected_into_system_prompt(self, tmp_path: Path) -> None:
        """Handoff notes should appear in the system prompt."""
        workspace = AgentWorkspace(
            "conv-handoff-001", base_dir=str(tmp_path / "ws"),
        )
        workspace.write_handoff("# Previous context\n- Found key insight X")

        registry = ToolRegistry()
        llm = FakeLLM(responses=[_final_answer_response("done")])
        agent = ReActAgent(llm=llm, tools=registry, workspace=workspace)

        # The system prompt builder is private, but we can verify via
        # _build_system_prompt (JSON mode) or _build_system_prompt_native.
        prompt = agent._build_system_prompt()
        assert "Previous Session Context" in prompt
        assert "Found key insight X" in prompt

    @pytest.mark.asyncio
    async def test_no_handoff_when_empty(self, tmp_path: Path) -> None:
        """No handoff section when there are no handoff notes."""
        workspace = AgentWorkspace(
            "conv-no-handoff", base_dir=str(tmp_path / "ws"),
        )
        registry = ToolRegistry()
        llm = FakeLLM(responses=[_final_answer_response("done")])
        agent = ReActAgent(llm=llm, tools=registry, workspace=workspace)

        prompt = agent._build_system_prompt()
        assert "Previous Session Context" not in prompt

    def test_workspace_property(self, tmp_path: Path) -> None:
        workspace = AgentWorkspace(
            "conv-prop", base_dir=str(tmp_path / "ws"),
        )
        registry = ToolRegistry()
        llm = FakeLLM(responses=[_final_answer_response("done")])
        agent = ReActAgent(llm=llm, tools=registry, workspace=workspace)
        assert agent.workspace is workspace

    def test_workspace_property_none(self) -> None:
        registry = ToolRegistry()
        llm = FakeLLM(responses=[_final_answer_response("done")])
        agent = ReActAgent(llm=llm, tools=registry)
        assert agent.workspace is None


# ======================================================================
# Shared-directory merge (workspace == sandbox workspace dir)
# ======================================================================


class TestSharedDirectory:
    """The workspace can sit directly on the sandbox's shared directory."""

    def test_explicit_directory_wins_over_base_dir(self, tmp_path: Path) -> None:
        shared = tmp_path / "sandbox" / "conv-1" / "workspace"
        ws = AgentWorkspace(
            "conv-1", base_dir=str(tmp_path / "workspaces"), directory=shared,
        )

        uri = ws.save_tool_output("http_request", "payload")
        filename = uri[len("workspace://"):]

        # The file lands where file_ops / shell_exec / python_exec look.
        assert (shared / filename).exists()
        assert not (tmp_path / "workspaces" / "conv-1" / filename).exists()

    def test_legacy_files_stay_readable(self, tmp_path: Path) -> None:
        # A conversation that offloaded files before the merge keeps access
        # to them through the old dedicated directory.
        legacy = tmp_path / "workspaces" / "conv-2"
        legacy.mkdir(parents=True)
        (legacy / "old_result.txt").write_text("from before the merge")

        ws = AgentWorkspace(
            "conv-2",
            base_dir=str(tmp_path / "workspaces"),
            directory=tmp_path / "sandbox" / "conv-2" / "workspace",
        )

        assert ws.read_file("old_result.txt") == "from before the merge"

    def test_internal_files_hidden_from_listing_but_readable(
        self, tmp_path: Path,
    ) -> None:
        ws = AgentWorkspace(
            "conv-3",
            base_dir=str(tmp_path / "workspaces"),
            directory=tmp_path / "sandbox" / "conv-3" / "workspace",
        )
        handoff_uri = ws.write_handoff("# Findings\nkey point")
        transcript_uri = ws.save_transcript(
            [ChatMessage(role="user", content="hello")],
        )

        # Neither runtime file clutters the shared tool view...
        names = {f["name"] for f in ws.list_files()}
        assert names == set()

        # ...but both stay readable through their workspace:// URIs.
        for uri in (handoff_uri, transcript_uri):
            filename = uri[len("workspace://"):]
            assert ws.read_file(filename)
        assert ws.read_latest_handoff() == "# Findings\nkey point"


class TestNoOffloadLoop:
    """Workspace reads must never be re-offloaded into fresh pointer files."""

    def test_read_tool_output_is_clamped_not_offloaded(
        self, workspace_small_threshold: AgentWorkspace,
    ) -> None:
        ws = workspace_small_threshold
        big = "x" * 500

        result = ws.maybe_offload("read_workspace_file", big)

        # Clamped with guidance, and — critically — no new file appeared.
        assert "Output clamped" in result
        assert "workspace://" not in result
        assert ws.list_files() == []

    def test_list_tool_output_is_clamped_not_offloaded(
        self, workspace_small_threshold: AgentWorkspace,
    ) -> None:
        ws = workspace_small_threshold

        result = ws.maybe_offload("list_workspace_files", "y" * 500)

        assert "Output clamped" in result
        assert ws.list_files() == []

    def test_other_tools_still_offload(
        self, workspace_small_threshold: AgentWorkspace,
    ) -> None:
        ws = workspace_small_threshold

        result = ws.maybe_offload("http_request", "z" * 500)

        assert "workspace://" in result
        assert len(ws.list_files()) == 1


class TestReadToolSelfClamp:
    """ReadWorkspaceFileTool pages instead of returning unbounded content."""

    async def test_oversized_read_reports_resume_point(
        self, tmp_path: Path,
    ) -> None:
        ws = AgentWorkspace(
            "conv-4",
            base_dir=str(tmp_path / "workspaces"),
            offload_threshold=100,
        )
        content = "\n".join(f"line {i:03d} " + "-" * 20 for i in range(50))
        uri = ws.save_tool_output("http_request", content)
        filename = uri[len("workspace://"):]

        tool = ReadWorkspaceFileTool(ws)
        result = await tool.run(filename=filename)

        assert "Read clamped" in result
        assert "start_line=" in result
        # The clamped body respects the budget (plus the guidance note).
        body = result.split("\n\n[Read clamped")[0]
        assert len(body) <= 100

    async def test_small_read_returned_whole(self, tmp_path: Path) -> None:
        ws = AgentWorkspace(
            "conv-5",
            base_dir=str(tmp_path / "workspaces"),
            offload_threshold=100,
        )
        uri = ws.save_tool_output("http_request", "short content")
        filename = uri[len("workspace://"):]

        tool = ReadWorkspaceFileTool(ws)
        assert await tool.run(filename=filename) == "short content"
