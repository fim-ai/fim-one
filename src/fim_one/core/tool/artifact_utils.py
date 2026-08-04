"""Utility functions for tool artifact management."""

from __future__ import annotations

import mimetypes
import os
import shutil
import uuid
from pathlib import Path

from .base import Artifact

# Per-session size limits (configurable via env).
MAX_ARTIFACT_SIZE = int(os.environ.get("MAX_ARTIFACT_SIZE", str(10 * 1024 * 1024)))  # 10 MB
MAX_ARTIFACTS_TOTAL = int(os.environ.get("MAX_ARTIFACTS_TOTAL", str(50 * 1024 * 1024)))  # 50 MB

# Runtime bookkeeping files that share the execution directory but are never
# deliverables.  The AgentWorkspace offloads large tool outputs into the same
# shared sandbox dir the exec tools scan (``tool_result_*`` — see
# ``AgentWorkspace.save_tool_output``); a concurrent offload landing during an
# exec run would otherwise be diff-detected as a "new file" and surface as a
# bogus download card.
_NON_ARTIFACT_PREFIXES = ("tool_result_", "transcript_", "HANDOFF_")


_FALLBACK_MIMES: dict[str, str] = {
    ".md": "text/markdown",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".toml": "application/toml",
    ".csv": "text/csv",
}


def _guess_mime(path: Path) -> str:
    """Guess MIME type from file extension."""
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        return mime
    return _FALLBACK_MIMES.get(path.suffix.lower(), "application/octet-stream")


def scan_new_files(directory: Path, before: set[str], artifacts_dir: Path) -> list[Artifact]:
    """Compare directory contents before/after execution, copy new files to *artifacts_dir*.

    Args:
        directory: The execution directory to scan.
        before: Set of filenames that existed before execution.
        artifacts_dir: Destination directory for artifact copies.

    Returns:
        List of Artifact objects for newly created files.
    """
    if not directory.exists():
        return []

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[Artifact] = []

    after = {f.name for f in directory.iterdir() if f.is_file()}
    new_files = after - before

    for filename in sorted(new_files):
        if filename.startswith(_NON_ARTIFACT_PREFIXES):
            continue
        src = directory / filename
        if not src.is_file():
            continue
        size = src.stat().st_size
        if size > MAX_ARTIFACT_SIZE:
            continue

        artifact_id = uuid.uuid4().hex[:12]
        stored_name = f"{artifact_id}_{filename}"
        dest = artifacts_dir / stored_name
        shutil.copy2(src, dest)

        # Build path relative to the uploads root (3 levels up from artifacts_dir:
        # uploads / conversations / {id} / artifacts).
        try:
            rel_path = str(dest.relative_to(artifacts_dir.parent.parent.parent))
        except ValueError:
            rel_path = stored_name

        artifacts.append(Artifact(
            name=filename,
            path=rel_path,
            mime_type=_guess_mime(src),
            size=size,
        ))

    return artifacts


def save_content_artifact(
    content: str,
    name: str,
    artifacts_dir: Path,
    mime_type: str = "text/html",
) -> Artifact:
    """Save string content as an artifact file.

    Args:
        content: The text content to save.
        name: Filename for the artifact.
        artifacts_dir: Destination directory.
        mime_type: MIME type of the content.

    Returns:
        An Artifact object describing the saved file.
    """
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifact_id = uuid.uuid4().hex[:12]
    stored_name = f"{artifact_id}_{name}"
    dest = artifacts_dir / stored_name
    dest.write_text(content, encoding="utf-8")
    size = dest.stat().st_size

    try:
        rel_path = str(dest.relative_to(artifacts_dir.parent.parent.parent))
    except ValueError:
        rel_path = stored_name

    return Artifact(
        name=name,
        path=rel_path,
        mime_type=mime_type,
        size=size,
    )
