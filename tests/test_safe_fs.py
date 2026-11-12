"""Workspace content is read without following symlinks.

With the ``docker`` backend a conversation's workspace is mounted into
sandbox containers, so its content is untrusted. Every place where the
application reads, lists or writes that directory (artifact scanning,
``read_workspace_file``, ``template_render``'s output, the file tool,
generated images, the script files) goes through
``core/security/safe_fs.py``: no symlink is followed, only regular files
below the workspace are acted on.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from fim_one.core.agent.workspace import AgentWorkspace
from fim_one.core.tool.artifact_utils import scan_new_files
from fim_one.core.tool.builtin.file_ops import FileOpsTool
from fim_one.core.tool.builtin.template_render import TemplateRenderTool

pytestmark = pytest.mark.skipif(os.name == "nt", reason="symlinks and openat")

CONTENT = "content of a file outside the workspace"


@pytest.fixture()
def outside(tmp_path: Path) -> Path:
    """A file outside every workspace."""
    path = tmp_path / "app" / ".env"
    path.parent.mkdir()
    path.write_text(CONTENT)
    return path


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "sandbox" / "conv-1" / "workspace"
    path.mkdir(parents=True)
    return path


class TestTheHelper:
    def test_a_regular_file_is_read_written_listed_and_copied(self, workspace: Path) -> None:
        from fim_one.core.security import safe_fs

        safe_fs.write_text(workspace, ("a", "b", "f.txt"), "one")
        safe_fs.write_text(workspace, ("a", "b", "f.txt"), "+two", append=True)

        assert safe_fs.read_text(workspace, ("a", "b", "f.txt")) == "one+two"
        assert [e.name for e in safe_fs.listdir(workspace, ("a", "b")) if e.is_file] == ["f.txt"]
        assert (
            safe_fs.copy_to(workspace, ("a", "b", "f.txt"), workspace / "copy", max_bytes=99) == 7
        )
        assert (
            safe_fs.copy_to(workspace, ("a", "b", "f.txt"), workspace / "copy2", max_bytes=3)
            is None
        )
        assert not (workspace / "copy2").exists()

    def test_a_symlinked_file_is_refused(self, workspace: Path, outside: Path) -> None:
        from fim_one.core.security import safe_fs

        (workspace / "link").symlink_to(outside)

        with pytest.raises(safe_fs.UnsafePathError):
            safe_fs.read_bytes(workspace, ("link",))
        with pytest.raises(safe_fs.UnsafePathError):
            safe_fs.write_text(workspace, ("link",), "overwritten")
        with pytest.raises(safe_fs.UnsafePathError):
            safe_fs.copy_to(workspace, ("link",), workspace / "out", max_bytes=99)
        assert outside.read_text() == CONTENT and not (workspace / "out").exists()

    def test_a_symlinked_directory_on_the_way_is_refused(
        self, workspace: Path, outside: Path
    ) -> None:
        from fim_one.core.security import safe_fs

        (workspace / "dir").symlink_to(outside.parent)

        with pytest.raises(safe_fs.UnsafePathError):
            safe_fs.read_bytes(workspace, ("dir", ".env"))
        with pytest.raises(safe_fs.UnsafePathError):
            safe_fs.write_text(workspace, ("dir", "new.py"), "x")
        with pytest.raises(safe_fs.UnsafePathError):
            safe_fs.listdir(workspace, ("dir",))
        with pytest.raises(safe_fs.UnsafePathError):
            safe_fs.mkdir(workspace, ("dir", "new"))
        assert sorted(p.name for p in outside.parent.iterdir()) == [".env"]

    @pytest.mark.parametrize("part", ["..", ".", "", "a/b", "a\x00b"])
    def test_a_component_that_is_not_a_plain_name_is_refused(
        self, workspace: Path, part: str
    ) -> None:
        from fim_one.core.security import safe_fs

        with pytest.raises(safe_fs.UnsafePathError):
            safe_fs.read_bytes(workspace, (part, "f"))

    def test_a_fifo_does_not_block_and_is_not_a_file(self, workspace: Path) -> None:
        from fim_one.core.security import safe_fs

        os.mkfifo(workspace / "pipe")

        with pytest.raises(OSError):
            safe_fs.write_text(workspace, ("pipe",), "x")
        with pytest.raises(safe_fs.UnsafePathError):
            safe_fs.read_bytes(workspace, ("pipe",))

    def test_listing_describes_and_never_follows(self, workspace: Path, outside: Path) -> None:
        from fim_one.core.security import safe_fs

        (workspace / "real.txt").write_text("x")
        (workspace / "link").symlink_to(outside)
        (workspace / "dirlink").symlink_to(outside.parent)

        entries = {e.name: e for e in safe_fs.listdir(workspace)}

        assert entries["real.txt"].is_file and not entries["real.txt"].is_symlink
        assert entries["link"].is_symlink and not entries["link"].is_file
        assert entries["dirlink"].is_symlink and not entries["dirlink"].is_dir

    def test_the_root_is_the_workspace_for_a_directory_below_it(self, tmp_path: Path) -> None:
        from fim_one.core.security import safe_fs

        base = tmp_path / "sandbox"

        assert safe_fs.anchor_of(base / "c1" / "workspace" / "a" / "b", base=base) == (
            base / "c1" / "workspace",
            ("a", "b"),
        )
        assert safe_fs.anchor_of(tmp_path / "elsewhere", base=base) == (tmp_path / "elsewhere", ())


class TestArtifactScanning:
    """Only regular files below the workspace become artifacts."""

    def test_a_symlink_is_not_copied(self, workspace: Path, outside: Path, tmp_path: Path) -> None:
        artifacts = tmp_path / "uploads" / "conversations" / "conv-1" / "artifacts"
        (workspace / "report.txt").symlink_to(outside)
        (workspace / "real.txt").write_text("fine")

        found = scan_new_files(workspace, set(), artifacts)

        assert [a.name for a in found] == ["real.txt"]
        assert all(CONTENT not in p.read_text() for p in artifacts.iterdir())

    def test_a_directory_swapped_for_a_symlink_lists_nothing(
        self, tmp_path: Path, outside: Path
    ) -> None:
        workspace = tmp_path / "ws"
        workspace.symlink_to(outside.parent)

        assert scan_new_files(workspace, set(), tmp_path / "artifacts") == []

    def test_the_before_listing_does_not_count_a_symlink_as_a_file(
        self, workspace: Path, outside: Path
    ) -> None:
        from fim_one.core.tool.artifact_utils import list_file_names

        (workspace / "link").symlink_to(outside)
        (workspace / "real").write_text("x")

        assert list_file_names(workspace) == {"real"}


class TestWorkspaceFiles:
    """A workspace file is a regular file reached without crossing a symlink."""

    def test_a_symlink_is_not_read(self, workspace: Path, outside: Path) -> None:
        (workspace / "notes.txt").symlink_to(outside)
        ws = AgentWorkspace("conv-1", directory=workspace)

        with pytest.raises(ValueError, match="not a regular file"):
            ws.read_file("notes.txt")

    def test_the_internal_directory_swapped_for_a_symlink_is_not_written_through(
        self, workspace: Path, outside: Path
    ) -> None:
        ws = AgentWorkspace("conv-1", directory=workspace)
        for child in workspace.iterdir():
            child.rmdir()
        from fim_one.core.agent import workspace as module

        internal = module._INTERNAL_SUBDIR
        (workspace / internal).symlink_to(outside.parent)

        with pytest.raises(OSError):
            ws.write_handoff("# handoff")
        with pytest.raises(OSError):
            ws.save_transcript([])
        assert ws.read_latest_handoff() is None
        assert sorted(p.name for p in outside.parent.iterdir()) == [".env"]

    def test_listing_and_cleanup_leave_a_symlinks_target_alone(
        self, workspace: Path, outside: Path
    ) -> None:
        os.utime(outside, (0, 0))
        (workspace / "old.txt").symlink_to(outside)
        ws = AgentWorkspace("conv-1", directory=workspace)

        assert [f["name"] for f in ws.list_files()] == []
        ws.cleanup(max_age_hours=0)
        assert outside.read_text() == CONTENT

    def test_an_offloaded_output_is_still_saved_and_read(self, workspace: Path) -> None:
        ws = AgentWorkspace("conv-1", directory=workspace)

        uri = ws.save_tool_output("web_fetch", "body")

        assert ws.read_file(uri.removeprefix("workspace://")) == "body"


class TestTemplateOutput:
    """The rendered file is written as a regular file in the workspace."""

    async def test_a_symlink_is_not_written_through(
        self, workspace: Path, outside: Path, tmp_path: Path
    ) -> None:
        (workspace / "page.html").symlink_to(outside)
        tool = TemplateRenderTool(workspace_dir=workspace, artifacts_dir=tmp_path / "artifacts")

        result: Any = await tool.run(
            template="<html><body>{{ x }}</body></html>", context='{"x": 1}', filename="page.html"
        )

        assert outside.read_text() == CONTENT
        # The artifact is still produced; only the workspace copy is skipped.
        assert result.artifacts and (workspace / "page.html").is_symlink()

    async def test_a_plain_name_is_written(self, workspace: Path, tmp_path: Path) -> None:
        tool = TemplateRenderTool(workspace_dir=workspace, artifacts_dir=tmp_path / "artifacts")

        await tool.run(template="<html><body>hi</body></html>", context="{}", filename="p.html")

        assert "hi" in (workspace / "p.html").read_text()


class TestFileTool:
    """Every operation opens its path without following symlinks."""

    @pytest.fixture()
    def tool(self, workspace: Path, tmp_path: Path) -> FileOpsTool:
        return FileOpsTool(workspace_dir=workspace, artifacts_dir=tmp_path / "artifacts")

    @pytest.mark.parametrize(
        "call",
        [
            {"operation": "read", "path": "link"},
            {"operation": "read_json", "path": "link"},
            {"operation": "write", "path": "link", "content": "overwritten"},
            {"operation": "append", "path": "link", "content": "appended"},
            {"operation": "find_replace", "path": "link", "old_text": "JWT", "new_text": "X"},
            {"operation": "read", "path": "dir/.env"},
            {"operation": "write", "path": "dir/new.py", "content": "x"},
            {"operation": "list", "path": "dir"},
            {"operation": "mkdir", "path": "dir/new"},
        ],
        ids=lambda call: f"{call['operation']}:{call['path']}",
    )
    async def test_nothing_crosses_a_symlink(
        self, tool: FileOpsTool, workspace: Path, outside: Path, call: dict[str, str]
    ) -> None:
        (workspace / "link").symlink_to(outside)
        (workspace / "dir").symlink_to(outside.parent)

        answer = await tool.run(**call)

        assert isinstance(answer, str) and answer.startswith("[Error]"), answer
        assert CONTENT not in answer
        assert outside.read_text() == CONTENT
        assert sorted(p.name for p in outside.parent.iterdir()) == [".env"]

    async def test_deleting_a_symlink_removes_the_link_only(
        self, tool: FileOpsTool, workspace: Path, outside: Path
    ) -> None:
        (workspace / "dir").symlink_to(outside.parent)

        await tool.run(operation="delete", path="dir")

        assert not (workspace / "dir").is_symlink() and outside.read_text() == CONTENT

    async def test_a_listing_names_a_symlink_without_following_it(
        self, tool: FileOpsTool, workspace: Path, outside: Path
    ) -> None:
        (workspace / "link").symlink_to(outside)

        answer = await tool.run(operation="list", path=".")

        assert "link  (symlink, not followed)" in str(answer)

    async def test_plain_files_work_as_before(self, tool: FileOpsTool, workspace: Path) -> None:
        await tool.run(operation="write", path="a/b.txt", content="hello")
        await tool.run(operation="append", path="a/b.txt", content=" world")

        assert await tool.run(operation="read", path="a/b.txt") == "hello world"
        assert "b.txt" in str(await tool.run(operation="list", path="a"))
        assert "true" in str(await tool.run(operation="exists", path="a"))
        assert str(await tool.run(operation="read", path="../../x")).startswith("[Error]")
        await tool.run(operation="delete", path="a")
        assert not (workspace / "a").exists()


class TestGeneratedImage:
    def test_the_image_is_not_written_through_a_symlink(
        self, workspace: Path, outside: Path
    ) -> None:
        from fim_one.core.security import safe_fs

        (workspace / "1700000000_cat.png").symlink_to(outside)
        root, parts = safe_fs.anchor_of(workspace)

        with pytest.raises(safe_fs.UnsafePathError):
            safe_fs.write_bytes(root, (*parts, "1700000000_cat.png"), b"\x89PNG")
        assert outside.read_text() == CONTENT


class TestLocalNodeEnvironment:
    """The ``node`` child of the local backend gets the cleaned environment."""

    async def test_node_is_started_with_the_cleaned_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from fim_one.core.tool.sandbox import LocalBackend, local_backend

        seen: dict[str, Any] = {}

        async def _spawn(*cmd: str, **kwargs: Any) -> Any:
            seen.update(kwargs, cmd=cmd)
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            return proc

        monkeypatch.setenv("JWT_CONTENT_KEY", "jwt-secret")
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "enc-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/x")
        monkeypatch.setenv("SANDBOX_PROXY_TOKEN", "s" * 64)
        monkeypatch.setattr(local_backend.shutil, "which", lambda _name: "/usr/bin/node")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)

        await LocalBackend().run_code(
            "console.log(1)", language="javascript", exec_dir=tmp_path, timeout=5
        )

        env = seen["env"]
        assert env is not None and seen["cmd"][0] == "/usr/bin/node"
        for name in (
            "JWT_CONTENT_KEY",
            "CREDENTIAL_ENCRYPTION_KEY",
            "DATABASE_URL",
            "SANDBOX_PROXY_TOKEN",
        ):
            assert name not in env


class TestShellWorkingDirectory:
    async def test_the_tool_leaves_creating_it_to_the_backend(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The backend creates it below the workspace, following nothing on the way."""
        from fim_one.core.tool.builtin import shell_exec
        from fim_one.core.tool.sandbox import SandboxResult

        asked: list[Path] = []

        class _Backend:
            async def run_shell(self, _command: str, *, sandbox_dir: Path, **_: Any) -> Any:
                asked.append(sandbox_dir)
                return SandboxResult(stdout="", stderr="", exit_code=0)

        monkeypatch.setattr(shell_exec, "get_sandbox_backend", lambda: _Backend())

        await shell_exec.ShellExecTool(sandbox_dir=workspace).run(
            command="ls", working_dir="made/by/the/backend"
        )

        assert asked == [workspace / "made" / "by" / "the" / "backend"]
        assert not (workspace / "made").exists()
