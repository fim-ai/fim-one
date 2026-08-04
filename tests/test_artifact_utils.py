"""Tests for tool artifact scanning (``scan_new_files``)."""

from __future__ import annotations

from pathlib import Path

from fim_one.core.tool.artifact_utils import scan_new_files


class TestScanNewFiles:
    def test_new_file_becomes_artifact(self, tmp_path: Path) -> None:
        exec_dir = tmp_path / "workspace"
        exec_dir.mkdir()
        artifacts_dir = tmp_path / "uploads" / "conversations" / "c1" / "artifacts"

        before = {f.name for f in exec_dir.iterdir() if f.is_file()}
        (exec_dir / "report.html").write_text("<html>hi</html>")

        artifacts = scan_new_files(exec_dir, before, artifacts_dir)

        assert [a.name for a in artifacts] == ["report.html"]
        copies = list(artifacts_dir.iterdir())
        assert len(copies) == 1

    def test_preexisting_files_ignored(self, tmp_path: Path) -> None:
        exec_dir = tmp_path / "workspace"
        exec_dir.mkdir()
        (exec_dir / "old.txt").write_text("was here")
        artifacts_dir = tmp_path / "artifacts"

        before = {f.name for f in exec_dir.iterdir() if f.is_file()}

        assert scan_new_files(exec_dir, before, artifacts_dir) == []

    def test_workspace_bookkeeping_files_never_become_artifacts(
        self, tmp_path: Path,
    ) -> None:
        # The AgentWorkspace shares the exec directory since the filesystem
        # merge.  An offload landing mid-exec-run must not surface as a
        # download card.
        exec_dir = tmp_path / "workspace"
        exec_dir.mkdir()
        artifacts_dir = tmp_path / "artifacts"

        before = {f.name for f in exec_dir.iterdir() if f.is_file()}
        (exec_dir / "tool_result_http_request_ab12cd34.txt").write_text("x" * 100)
        (exec_dir / "transcript_20260804T120000_ff00.jsonl").write_text("{}")
        (exec_dir / "HANDOFF_20260804_120000.md").write_text("# notes")
        (exec_dir / "real_output.csv").write_text("a,b\n1,2")

        artifacts = scan_new_files(exec_dir, before, artifacts_dir)

        assert [a.name for a in artifacts] == ["real_output.csv"]
        copied = {f.name for f in artifacts_dir.iterdir()}
        assert not any("tool_result_" in n for n in copied)
