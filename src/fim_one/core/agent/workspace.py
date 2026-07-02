"""Per-conversation workspace for storing large tool outputs and handoff notes.

When a tool produces a response exceeding the configured offload threshold,
the full output is saved to a workspace file and replaced with a truncated
preview in the conversation context.  This prevents large payloads from
polluting the LLM's attention window while keeping the data accessible
via workspace tools (``read_workspace_file``, ``list_workspace_files``).

The workspace is file-based and ephemeral -- no database involvement.
Thread-safety is achieved via a threading lock around file operations.
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default offload threshold (characters).  Override via env var.
DEFAULT_OFFLOAD_THRESHOLD = int(
    os.environ.get("WORKSPACE_OFFLOAD_THRESHOLD", "8000")
)

# Number of preview characters to include in the truncated result.
_PREVIEW_CHARS = int(os.getenv("WORKSPACE_PREVIEW_CHARS", "2000"))

# Max age in hours before workspace cleanup deletes files.
_CLEANUP_MAX_HOURS = int(os.getenv("WORKSPACE_CLEANUP_MAX_HOURS", "72"))


class AgentWorkspace:
    """Per-conversation workspace for storing large tool outputs and handoff notes.

    All file I/O is protected by a threading lock so that concurrent tool
    executions (e.g. parallel native tool calls) do not race on writes.

    Args:
        conversation_id: Unique identifier for the conversation.
        base_dir: Root directory under which per-conversation workspaces live.
        offload_threshold: Character count above which tool output is offloaded.
    """

    def __init__(
        self,
        conversation_id: str,
        base_dir: str = "data/workspaces",
        offload_threshold: int | None = None,
    ) -> None:
        self._conversation_id = conversation_id
        self._dir = Path(base_dir) / conversation_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._offload_threshold = (
            offload_threshold
            if offload_threshold is not None
            else DEFAULT_OFFLOAD_THRESHOLD
        )
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def conversation_id(self) -> str:
        """The conversation this workspace belongs to."""
        return self._conversation_id

    @property
    def directory(self) -> Path:
        """The workspace directory path."""
        return self._dir

    @property
    def offload_threshold(self) -> int:
        """Character count above which tool output is offloaded."""
        return self._offload_threshold

    # ------------------------------------------------------------------
    # Tool output management
    # ------------------------------------------------------------------

    def save_tool_output(self, tool_name: str, output: str) -> str:
        """Save a large tool output to a workspace file.

        Args:
            tool_name: Name of the tool that produced the output.
            output: The full text output to persist.

        Returns:
            A ``workspace://`` URI pointing to the saved file.
        """
        safe_name = tool_name.replace("/", "_").replace("\\", "_")
        filename = f"tool_result_{safe_name}_{uuid.uuid4().hex[:8]}.txt"
        filepath = self._dir / filename
        with self._lock:
            filepath.write_text(output, encoding="utf-8")
        logger.debug(
            "Saved tool output for '%s' (%d chars) to %s",
            tool_name,
            len(output),
            filepath,
        )
        return f"workspace://{filename}"

    def maybe_offload(self, tool_name: str, output: str) -> str:
        """Offload *output* to workspace if it exceeds the threshold.

        When the output is short enough, it is returned unchanged.

        Args:
            tool_name: Name of the tool that produced the output.
            output: The raw text output.

        Returns:
            Either the original output (if short) or a preview with a
            ``workspace://`` URI reference.
        """
        if len(output) <= self._offload_threshold:
            return output

        uri = self.save_tool_output(tool_name, output)
        preview = output[:_PREVIEW_CHARS]
        return (
            f"{preview}\n\n"
            f"[Full output saved to {uri} ({len(output)} chars). "
            f"Use read_workspace_file to access specific sections.]"
        )

    # ------------------------------------------------------------------
    # Transcript snapshots
    # ------------------------------------------------------------------

    def save_transcript(self, messages: list[Any]) -> str:
        """Snapshot the full conversation to a JSONL file before compaction.

        LLM compaction replaces history with a lossy summary; this snapshot
        keeps the original recoverable (the agent can ``read_workspace_file``
        it back, and operators can inspect it).  Each line is one message
        with role, content, and tool-call names.

        Args:
            messages: The message list about to be compacted.  Items are
                duck-typed ``ChatMessage``-likes (role/content/tool_calls).

        Returns:
            A ``workspace://`` URI pointing to the snapshot file.
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        filename = f"transcript_{ts}_{uuid.uuid4().hex[:8]}.jsonl"
        lines: list[str] = []
        for msg in messages:
            content = getattr(msg, "content", None)
            record = {
                "role": getattr(msg, "role", "unknown"),
                "content": content if isinstance(content, str) else "[non-text content]",
            }
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                record["reasoning"] = reasoning
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                record["tool_calls"] = [
                    getattr(tc, "name", "unknown") for tc in tool_calls
                ]
            lines.append(json.dumps(record, ensure_ascii=False))
        filepath = self._dir / filename
        with self._lock:
            filepath.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info(
            "Saved pre-compaction transcript (%d messages) to %s",
            len(messages),
            filepath,
        )
        return f"workspace://{filename}"

    # ------------------------------------------------------------------
    # File access
    # ------------------------------------------------------------------

    def read_file(
        self,
        filename: str,
        start_line: int = 0,
        end_line: int | None = None,
    ) -> str:
        """Read a workspace file, optionally a specific line range.

        Args:
            filename: Name of the file within the workspace directory.
            start_line: Zero-based starting line (inclusive).
            end_line: Zero-based ending line (exclusive).  ``None`` means
                read to the end.

        Returns:
            The requested lines joined by newlines.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the filename tries to escape the workspace.
        """
        self._validate_filename(filename)
        filepath = self._dir / filename
        if not filepath.exists():
            raise FileNotFoundError(f"Workspace file not found: {filename}")
        with self._lock:
            lines = filepath.read_text(encoding="utf-8").splitlines()
        if end_line is not None:
            lines = lines[start_line:end_line]
        else:
            lines = lines[start_line:]
        return "\n".join(lines)

    def list_files(self) -> list[dict[str, str | int]]:
        """List all files in the workspace with metadata.

        Returns:
            A list of dicts with ``name``, ``size_bytes``, and
            ``created_at`` (ISO-8601 timestamp).
        """
        files: list[dict[str, str | int]] = []
        with self._lock:
            for f in self._dir.iterdir():
                if f.is_file():
                    stat = f.stat()
                    files.append({
                        "name": f.name,
                        "size_bytes": stat.st_size,
                        "created_at": datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ).isoformat(),
                    })
        return sorted(files, key=lambda x: str(x["created_at"]), reverse=True)

    # ------------------------------------------------------------------
    # Handoff notes
    # ------------------------------------------------------------------

    def write_handoff(self, summary: str) -> str:
        """Write a structured handoff note for context transitions.

        Handoff notes are timestamped markdown files that can be picked up
        when a conversation is resumed or context is compressed.

        Args:
            summary: Markdown-formatted handoff content.

        Returns:
            A ``workspace://`` URI pointing to the handoff file.
        """
        filename = f"HANDOFF_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.md"
        filepath = self._dir / filename
        with self._lock:
            filepath.write_text(summary, encoding="utf-8")
        logger.debug("Wrote handoff note: %s", filepath)
        return f"workspace://{filename}"

    def read_latest_handoff(self) -> str | None:
        """Read the most recent handoff note, if any.

        Returns:
            The handoff content as a string, or ``None`` if no handoff
            notes exist in this workspace.
        """
        with self._lock:
            handoffs = sorted(self._dir.glob("HANDOFF_*.md"), reverse=True)
            if not handoffs:
                return None
            return handoffs[0].read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self, max_age_hours: int = _CLEANUP_MAX_HOURS) -> int:
        """Delete workspace files older than *max_age_hours*.

        Args:
            max_age_hours: Maximum file age in hours before deletion.

        Returns:
            The number of files deleted.
        """
        import time

        cutoff = time.time() - max_age_hours * 3600
        deleted = 0
        with self._lock:
            for f in list(self._dir.iterdir()):
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted += 1
                    logger.debug("Cleaned up workspace file: %s", f.name)
        if deleted:
            logger.info(
                "Workspace cleanup: deleted %d file(s) older than %dh",
                deleted,
                max_age_hours,
            )
        return deleted

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_filename(filename: str) -> None:
        """Ensure a filename does not escape the workspace directory.

        Raises:
            ValueError: If the filename contains path traversal components.
        """
        if ".." in filename or "/" in filename or "\\" in filename:
            raise ValueError(
                f"Invalid workspace filename (path traversal blocked): {filename}"
            )
