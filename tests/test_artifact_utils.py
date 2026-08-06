"""Tests for tool artifact scanning (``scan_new_files``) and dedup."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fim_one.core.tool.artifact_utils import save_content_artifact, scan_new_files
from fim_one.web.api.chat import _dedupe_artifacts


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
        expected_sha = hashlib.sha256(b"<html>hi</html>").hexdigest()
        assert artifacts[0].sha256 == expected_sha

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


class TestContentHashDedup:
    def test_save_content_artifact_sets_sha(self, tmp_path: Path) -> None:
        artifact = save_content_artifact("<html>x</html>", "rendered.html", tmp_path)
        assert artifact.sha256 == hashlib.sha256(b"<html>x</html>").hexdigest()

    def test_duplicate_content_collapses_to_real_filename(self) -> None:
        # template_render registers the generic "rendered.html" first, then
        # the agent saves the identical HTML under a real name via file_ops.
        # Only the real-named copy should survive.
        sha = "a" * 64
        artifacts = [
            {"name": "rendered.html", "path": "p1", "size": 5154, "sha256": sha},
            {"name": "framework_scorecard.html", "path": "p2", "size": 5154, "sha256": sha},
        ]

        deduped = _dedupe_artifacts(artifacts)

        assert [a["name"] for a in deduped] == ["framework_scorecard.html"]

    def test_generic_name_wins_only_against_itself(self) -> None:
        # Real name registered first stays; the later generic copy is dropped.
        sha = "b" * 64
        artifacts = [
            {"name": "scorecard.html", "path": "p1", "size": 100, "sha256": sha},
            {"name": "rendered.html", "path": "p2", "size": 100, "sha256": sha},
        ]

        deduped = _dedupe_artifacts(artifacts)

        assert [a["name"] for a in deduped] == ["scorecard.html"]

    def test_distinct_content_untouched(self) -> None:
        artifacts = [
            {"name": "a.html", "path": "p1", "size": 10, "sha256": "c" * 64},
            {"name": "b.html", "path": "p2", "size": 10, "sha256": "d" * 64},
        ]

        assert _dedupe_artifacts(artifacts) == artifacts

    def test_missing_sha_always_kept(self) -> None:
        # Pre-upgrade artifacts have no hash — never drop them.
        artifacts = [
            {"name": "old1.html", "path": "p1", "size": 10},
            {"name": "old2.html", "path": "p2", "size": 10, "sha256": ""},
        ]

        assert _dedupe_artifacts(artifacts) == artifacts
