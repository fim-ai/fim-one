"""Tests for per-conversation sandbox isolation.

Verifies that FileOpsTool, ShellExecTool, and PythonExecTool can be
configured with custom sandbox directories, and that the auto-discovery
function correctly wires per-conversation paths.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from fim_agent.core.tool.builtin.file_ops import FileOpsTool, _DEFAULT_WORKSPACE_DIR
from fim_agent.core.tool.builtin.shell_exec import ShellExecTool, _DEFAULT_SANDBOX_DIR
from fim_agent.core.tool.builtin.python_exec import PythonExecTool, _DEFAULT_EXEC_DIR
from fim_agent.core.tool.builtin import discover_builtin_tools


# ======================================================================
# FileOpsTool — per-conversation workspace
# ======================================================================


class TestFileOpsToolSandbox:
    """Verify FileOpsTool respects custom workspace_dir."""

    def test_default_workspace_dir(self) -> None:
        tool = FileOpsTool()
        assert tool._workspace_dir == _DEFAULT_WORKSPACE_DIR

    def test_custom_workspace_dir(self, tmp_path: Path) -> None:
        workspace = tmp_path / "my_workspace"
        tool = FileOpsTool(workspace_dir=workspace)
        assert tool._workspace_dir == workspace

    async def test_write_and_read_in_custom_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "conv_abc" / "workspace"
        tool = FileOpsTool(workspace_dir=workspace)

        # Write a file — workspace dir should be created lazily.
        result = await tool.run(operation="write", path="hello.txt", content="world")
        assert "Written" in result
        assert (workspace / "hello.txt").exists()
        assert (workspace / "hello.txt").read_text() == "world"

        # Read it back.
        result = await tool.run(operation="read", path="hello.txt")
        assert result == "world"

    async def test_list_custom_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "conv_list" / "workspace"
        tool = FileOpsTool(workspace_dir=workspace)

        await tool.run(operation="write", path="a.txt", content="aaa")
        await tool.run(operation="write", path="b.txt", content="bbb")

        result = await tool.run(operation="list", path=".")
        assert "a.txt" in result
        assert "b.txt" in result

    async def test_mkdir_custom_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "conv_mkdir" / "workspace"
        tool = FileOpsTool(workspace_dir=workspace)

        result = await tool.run(operation="mkdir", path="subdir/nested")
        assert "Directory created" in result
        assert (workspace / "subdir" / "nested").is_dir()

    async def test_path_traversal_blocked_in_custom_workspace(
        self, tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "conv_safe" / "workspace"
        tool = FileOpsTool(workspace_dir=workspace)

        result = await tool.run(operation="read", path="../../etc/passwd")
        assert "Path traversal detected" in result

    async def test_isolation_between_conversations(self, tmp_path: Path) -> None:
        ws_a = tmp_path / "conv_a" / "workspace"
        ws_b = tmp_path / "conv_b" / "workspace"
        tool_a = FileOpsTool(workspace_dir=ws_a)
        tool_b = FileOpsTool(workspace_dir=ws_b)

        await tool_a.run(operation="write", path="secret.txt", content="only_a")
        await tool_b.run(operation="write", path="secret.txt", content="only_b")

        result_a = await tool_a.run(operation="read", path="secret.txt")
        result_b = await tool_b.run(operation="read", path="secret.txt")

        assert result_a == "only_a"
        assert result_b == "only_b"


# ======================================================================
# ShellExecTool — per-conversation sandbox
# ======================================================================


class TestShellExecToolSandbox:
    """Verify ShellExecTool respects custom sandbox_dir."""

    def test_default_sandbox_dir(self) -> None:
        tool = ShellExecTool()
        assert tool._sandbox_dir == _DEFAULT_SANDBOX_DIR

    def test_custom_sandbox_dir(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "my_sandbox"
        tool = ShellExecTool(sandbox_dir=sandbox)
        assert tool._sandbox_dir == sandbox

    async def test_command_runs_in_custom_sandbox(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "conv_shell" / "sandbox"
        tool = ShellExecTool(sandbox_dir=sandbox)

        result = await tool.run(command="pwd")
        assert "Exit Code: 0" in result
        assert str(sandbox) in result

    async def test_file_created_in_custom_sandbox(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "conv_shell2" / "sandbox"
        tool = ShellExecTool(sandbox_dir=sandbox)

        await tool.run(command="echo hello > test.txt")
        assert (sandbox / "test.txt").exists()

    async def test_isolation_between_conversations(self, tmp_path: Path) -> None:
        sandbox_a = tmp_path / "conv_a" / "sandbox"
        sandbox_b = tmp_path / "conv_b" / "sandbox"
        tool_a = ShellExecTool(sandbox_dir=sandbox_a)
        tool_b = ShellExecTool(sandbox_dir=sandbox_b)

        await tool_a.run(command="echo A > marker.txt")
        await tool_b.run(command="echo B > marker.txt")

        assert (sandbox_a / "marker.txt").read_text().strip() == "A"
        assert (sandbox_b / "marker.txt").read_text().strip() == "B"


# ======================================================================
# PythonExecTool — per-conversation exec dir
# ======================================================================


class TestPythonExecToolSandbox:
    """Verify PythonExecTool respects custom exec_dir."""

    def test_default_exec_dir(self) -> None:
        tool = PythonExecTool()
        assert tool._exec_dir == _DEFAULT_EXEC_DIR

    def test_custom_exec_dir(self, tmp_path: Path) -> None:
        exec_dir = tmp_path / "my_exec"
        tool = PythonExecTool(exec_dir=exec_dir)
        assert tool._exec_dir == exec_dir

    async def test_cwd_is_custom_exec_dir(self, tmp_path: Path) -> None:
        exec_dir = tmp_path / "conv_py" / "exec"
        tool = PythonExecTool(exec_dir=exec_dir, timeout=10)

        result = await tool.run(code="import os; print(os.getcwd())")
        assert str(exec_dir) in result

    async def test_file_io_in_custom_exec_dir(self, tmp_path: Path) -> None:
        exec_dir = tmp_path / "conv_py2" / "exec"
        tool = PythonExecTool(exec_dir=exec_dir, timeout=10)

        code = (
            "with open('output.txt', 'w') as f:\n"
            "    f.write('from python')\n"
            "print('done')"
        )
        result = await tool.run(code=code)
        assert "done" in result
        assert (exec_dir / "output.txt").read_text() == "from python"


# ======================================================================
# discover_builtin_tools — sandbox_root wiring
# ======================================================================


class TestDiscoverBuiltinToolsSandbox:
    """Verify that discover_builtin_tools passes sandbox_root correctly."""

    def test_without_sandbox_root_uses_defaults(self) -> None:
        tools = discover_builtin_tools()
        tool_map = {t.name: t for t in tools}

        file_ops = tool_map.get("file_ops")
        shell_exec = tool_map.get("shell_exec")
        python_exec = tool_map.get("python_exec")

        assert file_ops is not None
        assert shell_exec is not None
        assert python_exec is not None

        assert file_ops._workspace_dir == _DEFAULT_WORKSPACE_DIR
        assert shell_exec._sandbox_dir == _DEFAULT_SANDBOX_DIR
        assert python_exec._exec_dir == _DEFAULT_EXEC_DIR

    def test_with_sandbox_root_configures_subdirs(self, tmp_path: Path) -> None:
        sandbox_root = tmp_path / "conversations" / "conv123"
        tools = discover_builtin_tools(sandbox_root=sandbox_root)
        tool_map = {t.name: t for t in tools}

        file_ops = tool_map.get("file_ops")
        shell_exec = tool_map.get("shell_exec")
        python_exec = tool_map.get("python_exec")

        assert file_ops is not None
        assert shell_exec is not None
        assert python_exec is not None

        assert file_ops._workspace_dir == sandbox_root / "workspace"
        assert shell_exec._sandbox_dir == sandbox_root / "sandbox"
        assert python_exec._exec_dir == sandbox_root / "exec"

    def test_non_sandboxed_tools_unaffected(self, tmp_path: Path) -> None:
        """Tools like calculator, web_fetch etc. should still be instantiated."""
        sandbox_root = tmp_path / "conversations" / "conv456"
        tools = discover_builtin_tools(sandbox_root=sandbox_root)
        tool_names = {t.name for t in tools}

        # These tools should still be present.
        assert "calculator" in tool_names
        assert "web_fetch" in tool_names
        assert "web_search" in tool_names
        assert "http_request" in tool_names
