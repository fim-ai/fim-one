"""Built-in tool for safe file operations within a sandboxed workspace."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from ..base import Artifact, BaseTool, ToolResult

_MAX_READ_BYTES: int = 50 * 1024  # 50 KB

# Default workspace directory — used when no per-conversation sandbox is provided.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_WORKSPACE_DIR = _PROJECT_ROOT / "tmp" / "workspace"


class FileOpsTool(BaseTool):
    """Perform file operations in a sandboxed workspace.

    Supports: read, write, append, delete, list, mkdir, exists, get_info,
    read_json, write_json, read_csv, write_csv, find_replace.

    All paths are resolved relative to a workspace directory.  When
    *workspace_dir* is provided (e.g. per-conversation sandbox), operations
    are confined to that directory.  Path traversal outside the workspace is
    rejected.
    """

    def __init__(
        self,
        *,
        workspace_dir: Path | None = None,
        artifacts_dir: Path | None = None,
    ) -> None:
        self._workspace_dir = workspace_dir or _DEFAULT_WORKSPACE_DIR
        self._artifacts_dir = artifacts_dir

    # ------------------------------------------------------------------
    # Tool protocol properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "file_ops"

    @property
    def display_name(self) -> str:
        return "File Operations"

    @property
    def category(self) -> str:
        return "filesystem"

    @property
    def description(self) -> str:
        return (
            "Perform file operations inside a sandboxed workspace directory. "
            "Supported operations: "
            '"read" — read file content (max 50 KB); '
            '"write" — write/overwrite content to a file (creates parent dirs); '
            '"append" — append content to a file (creates if absent); '
            '"delete" — delete a file or directory; '
            '"list" — list directory contents with sizes; '
            '"mkdir" — create a directory (with parents); '
            '"exists" — check whether a path exists and return its type; '
            '"get_info" — return file/directory metadata (size, type, modified time); '
            '"read_json" — read and validate a JSON file, return pretty-printed; '
            '"write_json" — validate and write content as a pretty-printed JSON file; '
            '"read_csv" — read a CSV file and return as a formatted Markdown table; '
            '"write_csv" — write a CSV file from a JSON array of row arrays; '
            '"find_replace" — find and replace text in a file (requires old_text and new_text). '
            "All paths are relative to the workspace root."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "read", "write", "append", "delete", "list", "mkdir",
                        "exists", "get_info", "read_json", "write_json",
                        "read_csv", "write_csv", "find_replace",
                    ],
                    "description": "The file operation to perform.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Target file or directory path, relative to the workspace root."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Content to write or append. Required for: write, append, "
                        "write_json (a JSON string), write_csv (a JSON array of row arrays)."
                    ),
                },
                "old_text": {
                    "type": "string",
                    "description": "Exact text to search for. Required for find_replace.",
                },
                "new_text": {
                    "type": "string",
                    "description": "Replacement text. Required for find_replace.",
                },
            },
            "required": ["operation", "path"],
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run(self, **kwargs: Any) -> str | ToolResult:  # type: ignore[override]
        operation: str = kwargs.get("operation", "").strip()
        raw_path: str = kwargs.get("path", "").strip()
        content: str = kwargs.get("content", "")
        old_text: str = kwargs.get("old_text", "")
        new_text: str = kwargs.get("new_text", "")

        if not operation:
            return "[Error] No operation specified."
        if not raw_path:
            return "[Error] No path specified."

        # Lazily create workspace directory on first use.
        self._workspace_dir.mkdir(parents=True, exist_ok=True)

        # Resolve and validate the target path.
        resolved = self._resolve_safe_path(raw_path)
        if resolved is None:
            return "[Error] Path traversal detected — access denied."

        try:
            if operation == "read":
                return await asyncio.to_thread(self._read, resolved)
            elif operation == "write":
                result = await asyncio.to_thread(self._write, resolved, content)
                return self._maybe_register_artifact(result, resolved)
            elif operation == "append":
                return await asyncio.to_thread(self._append, resolved, content)
            elif operation == "delete":
                return await asyncio.to_thread(self._delete, resolved)
            elif operation == "list":
                return await asyncio.to_thread(self._list, resolved)
            elif operation == "mkdir":
                return await asyncio.to_thread(self._mkdir, resolved)
            elif operation == "exists":
                return await asyncio.to_thread(self._exists, resolved)
            elif operation == "get_info":
                return await asyncio.to_thread(self._get_info, resolved)
            elif operation == "read_json":
                return await asyncio.to_thread(self._read_json, resolved)
            elif operation == "write_json":
                result = await asyncio.to_thread(self._write_json, resolved, content)
                return self._maybe_register_artifact(result, resolved)
            elif operation == "read_csv":
                return await asyncio.to_thread(self._read_csv, resolved)
            elif operation == "write_csv":
                result = await asyncio.to_thread(self._write_csv, resolved, content)
                return self._maybe_register_artifact(result, resolved)
            elif operation == "find_replace":
                return await asyncio.to_thread(self._find_replace, resolved, old_text, new_text)
            else:
                return f"[Error] Unknown operation: {operation}"
        except Exception as exc:
            return f"[Error] {type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------
    # Artifact registration
    # ------------------------------------------------------------------

    def _maybe_register_artifact(self, result: str, file_path: Path) -> str | ToolResult:
        """If artifacts_dir is set and the file exists, register it as an artifact."""
        if (
            self._artifacts_dir is None
            or result.startswith("[Error]")
            or not file_path.exists()
            or not file_path.is_file()
        ):
            return result

        from ..artifact_utils import save_content_artifact, MAX_ARTIFACT_SIZE, _guess_mime

        size = file_path.stat().st_size
        if size > MAX_ARTIFACT_SIZE:
            return result

        import shutil
        import uuid

        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        artifact_id = uuid.uuid4().hex[:12]
        stored_name = f"{artifact_id}_{file_path.name}"
        dest = self._artifacts_dir / stored_name
        shutil.copy2(file_path, dest)

        try:
            rel_path = str(dest.relative_to(self._artifacts_dir.parent.parent.parent))
        except ValueError:
            rel_path = stored_name

        artifact = Artifact(
            name=file_path.name,
            path=rel_path,
            mime_type=_guess_mime(file_path),
            size=size,
        )
        return ToolResult(content=result, artifacts=[artifact])

    # ------------------------------------------------------------------
    # Path safety
    # ------------------------------------------------------------------

    def _resolve_safe_path(self, raw_path: str) -> Path | None:
        """Resolve *raw_path* within the workspace and validate containment."""
        workspace = self._workspace_dir
        target = (workspace / raw_path).resolve()
        if not (
            target == workspace
            or str(target).startswith(str(workspace) + "/")
        ):
            return None
        return target

    # ------------------------------------------------------------------
    # Operation implementations (synchronous — run via to_thread)
    # ------------------------------------------------------------------

    def _read(self, path: Path) -> str:
        """Read file contents, truncating at ``_MAX_READ_BYTES``."""
        workspace = self._workspace_dir
        if not path.exists():
            return f"[Error] File not found: {path.relative_to(workspace)}"
        if not path.is_file():
            return f"[Error] Not a file: {path.relative_to(workspace)}"

        size = path.stat().st_size
        if size > _MAX_READ_BYTES:
            with open(path, "rb") as f:
                data = f.read(_MAX_READ_BYTES)
        else:
            data = path.read_bytes()

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return "[Error] File is not valid UTF-8 text."

        if size > _MAX_READ_BYTES:
            text += (
                f"\n\n[Truncated — showing first {_MAX_READ_BYTES // 1024} KB "
                f"of {size:,} bytes total]"
            )
        return text

    def _write(self, path: Path, content: str) -> str:
        """Write *content* to file, creating parent directories as needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Written {len(content)} chars to {path.relative_to(self._workspace_dir)}"

    def _append(self, path: Path, content: str) -> str:
        """Append *content* to file, creating it if it does not exist."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
        return f"Appended {len(content)} chars to {path.relative_to(self._workspace_dir)}"

    def _delete(self, path: Path) -> str:
        """Delete a file or directory tree."""
        workspace = self._workspace_dir
        rel = path.relative_to(workspace)
        if not path.exists():
            return f"[Error] Path not found: {rel}"
        if path.is_dir():
            shutil.rmtree(path)
            return f"Deleted directory: {rel}"
        path.unlink()
        return f"Deleted file: {rel}"

    def _list(self, path: Path) -> str:
        """List directory contents with file sizes."""
        workspace = self._workspace_dir
        if not path.exists():
            return f"[Error] Directory not found: {path.relative_to(workspace)}"
        if not path.is_dir():
            return f"[Error] Not a directory: {path.relative_to(workspace)}"

        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        if not entries:
            return "(empty directory)"

        lines: list[str] = []
        for entry in entries:
            if entry.is_dir():
                lines.append(f"  {entry.name}/")
            else:
                size = entry.stat().st_size
                lines.append(f"  {entry.name}  ({_human_size(size)})")
        return "\n".join(lines)

    def _mkdir(self, path: Path) -> str:
        """Create directory with parents."""
        path.mkdir(parents=True, exist_ok=True)
        return f"Directory created: {path.relative_to(self._workspace_dir)}"

    def _exists(self, path: Path) -> str:
        """Return whether the path exists and its type."""
        rel = path.relative_to(self._workspace_dir)
        if not path.exists():
            return f"false — {rel} does not exist"
        kind = "directory" if path.is_dir() else "file"
        return f"true — {rel} exists ({kind})"

    def _get_info(self, path: Path) -> str:
        """Return file/directory metadata."""
        workspace = self._workspace_dir
        rel = path.relative_to(workspace)
        if not path.exists():
            return f"[Error] Path not found: {rel}"
        stat = path.stat()
        kind = "directory" if path.is_dir() else "file"
        mtime = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        return "\n".join([
            f"path: {rel}",
            f"type: {kind}",
            f"size: {_human_size(stat.st_size)}",
            f"modified: {mtime}",
        ])

    def _read_json(self, path: Path) -> str:
        """Read and pretty-print a JSON file."""
        raw = self._read(path)
        if raw.startswith("[Error]"):
            return raw
        try:
            parsed = json.loads(raw)
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        except json.JSONDecodeError as e:
            return f"[Error] Invalid JSON: {e}"

    def _write_json(self, path: Path, content: str) -> str:
        """Validate *content* as JSON then write it pretty-printed."""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            return f"[Error] Content is not valid JSON: {e}"
        pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
        return self._write(path, pretty)

    def _read_csv(self, path: Path) -> str:
        """Read a CSV file and return it as a Markdown table."""
        raw = self._read(path)
        if raw.startswith("[Error]"):
            return raw
        reader = csv.reader(io.StringIO(raw))
        rows = list(reader)
        if not rows:
            return "(empty CSV)"

        # Compute column widths.
        cols = len(rows[0])
        widths = [0] * cols
        for row in rows:
            for i, cell in enumerate(row):
                if i < cols:
                    widths[i] = max(widths[i], len(cell))

        def fmt_row(row: list[str]) -> str:
            return "| " + " | ".join(
                (row[i] if i < len(row) else "").ljust(widths[i])
                for i in range(cols)
            ) + " |"

        separator = "|-" + "-|-".join("-" * w for w in widths) + "-|"
        lines = [fmt_row(rows[0]), separator] + [fmt_row(r) for r in rows[1:]]
        return "\n".join(lines)

    def _write_csv(self, path: Path, content: str) -> str:
        """Write CSV from a JSON array of row arrays, or a raw CSV string."""
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            rows = json.loads(content)
            if not isinstance(rows, list):
                raise ValueError("Expected a JSON array")
            buf = io.StringIO()
            writer = csv.writer(buf)
            for row in rows:
                writer.writerow([str(cell) for cell in (row if isinstance(row, list) else [row])])
            csv_text = buf.getvalue()
        except (json.JSONDecodeError, ValueError):
            # Treat as raw CSV string.
            csv_text = content
        path.write_text(csv_text, encoding="utf-8")
        return f"Written CSV to {path.relative_to(self._workspace_dir)}"

    def _find_replace(self, path: Path, old_text: str, new_text: str) -> str:
        """Find and replace all occurrences of *old_text* with *new_text*."""
        if not old_text:
            return "[Error] old_text is required for find_replace"
        raw = self._read(path)
        if raw.startswith("[Error]"):
            return raw
        count = raw.count(old_text)
        if count == 0:
            return f"No occurrences of the search text found in {path.relative_to(self._workspace_dir)}"
        updated = raw.replace(old_text, new_text)
        self._write(path, updated)
        return f"Replaced {count} occurrence(s) in {path.relative_to(self._workspace_dir)}"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _human_size(nbytes: int) -> str:
    """Format a byte count as a human-readable string."""
    size: float = float(nbytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
