"""Read and search uploaded file content tool for agent use."""

from __future__ import annotations

import re
from typing import Any

from fim_one.core.tool.base import BaseTool


class ReadUploadedFileTool(BaseTool):
    """Read or search the content of an uploaded file.

    Two modes:
    - **Read mode** (default): paginated reading via ``offset`` / ``limit``.
    - **Search mode**: when ``query`` is provided, returns matching lines with
      context and line numbers (like grep).
    """

    def __init__(self, user_id: str) -> None:
        self._user_id = user_id

    @property
    def name(self) -> str:
        return "read_uploaded_file"

    @property
    def cacheable(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        return "Read Uploaded File"

    @property
    def description(self) -> str:
        return (
            "Read or search the content of an uploaded file. "
            "Without a query, reads a paginated chunk (offset/limit). "
            "With a query, searches for matching lines and returns them "
            "with surrounding context and line numbers (like grep)."
        )

    @property
    def category(self) -> str:
        return "files"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "The file ID to read.",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Search pattern (regex supported). When provided, "
                        "returns matching lines with context instead of "
                        "paginated reading."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": "Character offset to start reading from (read mode only).",
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of characters to read (read mode only).",
                    "default": 20000,
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Lines of context around each match (search mode only).",
                    "default": 3,
                },
            },
            "required": ["file_id"],
        }

    async def run(self, **kwargs: Any) -> str:
        file_id: str = kwargs.get("file_id", "")
        if not file_id:
            return "[Error] file_id is required"

        try:
            from fim_one.web.api.files import _load_index, _user_dir

            index = _load_index(self._user_id)
            meta = index.get(file_id)
            if meta is None:
                return "File not found."

            user_dir = _user_dir(self._user_id)
            stored_name = meta["stored_name"]
            content_path = user_dir / f"{stored_name}.content"

            # Security: ensure the resolved path is within the user directory
            if not content_path.resolve().is_relative_to(user_dir.resolve()):
                return "File not found."

            if not content_path.exists():
                return "No text content available for this file."

            text = content_path.read_text(encoding="utf-8")
            filename: str = str(meta.get("filename", "unknown"))
            query: str | None = kwargs.get("query")

            if query:
                return self._search_mode(text, filename, file_id, query, kwargs)
            else:
                return self._read_mode(text, filename, file_id, kwargs)

        except Exception as exc:
            return f"[Error] {type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------
    # Read mode — paginated character-level reading
    # ------------------------------------------------------------------

    def _read_mode(
        self, text: str, filename: str, file_id: str, kwargs: dict[str, Any]
    ) -> str:
        offset: int = int(kwargs.get("offset", 0))
        limit: int = int(kwargs.get("limit", 20000))
        total = len(text)

        chunk = text[offset : offset + limit]
        end = offset + len(chunk)
        remaining = max(0, total - end)

        header = (
            f"{filename} | {total} chars total "
            f"| Reading chars {offset}-{end} | {remaining} remaining"
        )
        if remaining > 0:
            header += (
                f"\nTo read more: "
                f'read_uploaded_file(file_id="{file_id}", offset={end})'
            )

        return f"{header}\n---\n{chunk}"

    # ------------------------------------------------------------------
    # Search mode — grep-like line matching with context
    # ------------------------------------------------------------------

    _MAX_MATCHES = 30

    def _search_mode(
        self,
        text: str,
        filename: str,
        file_id: str,
        query: str,
        kwargs: dict[str, Any],
    ) -> str:
        context_n: int = int(kwargs.get("context_lines", 3))
        lines = text.splitlines()
        total_lines = len(lines)

        # Compile regex (case-insensitive); fall back to literal on error
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(query), re.IGNORECASE)

        # Find matching line indices
        match_indices: list[int] = []
        for i, line in enumerate(lines):
            if pattern.search(line):
                match_indices.append(i)
            if len(match_indices) >= self._MAX_MATCHES:
                break

        if not match_indices:
            return (
                f'{filename} | No matches for "{query}" '
                f"in {total_lines} lines.\n"
                f"Tip: try a different query, or use read mode to browse the file."
            )

        # Build result blocks with context
        blocks: list[str] = []
        shown: set[int] = set()
        truncated = len(match_indices) >= self._MAX_MATCHES

        for match_idx in match_indices:
            start = max(0, match_idx - context_n)
            end = min(total_lines, match_idx + context_n + 1)

            block_lines: list[str] = []
            for i in range(start, end):
                if i in shown:
                    continue
                shown.add(i)
                marker = ">>> " if i == match_idx else "    "
                block_lines.append(f"{marker}{i + 1:>6} | {lines[i]}")

            if block_lines:
                blocks.append("\n".join(block_lines))

        header = (
            f'{filename} | {len(match_indices)} match{"es" if len(match_indices) != 1 else ""} '
            f'for "{query}" in {total_lines} lines ({len(text)} chars)'
        )
        if truncated:
            header += f" [showing first {self._MAX_MATCHES}, more matches exist]"

        result = header + "\n\n" + "\n...\n".join(blocks)

        # Hint for reading around a match
        first = match_indices[0]
        char_offset = sum(len(lines[i]) + 1 for i in range(first))
        result += (
            f"\n\nTo read full context around a match: "
            f'read_uploaded_file(file_id="{file_id}", offset={char_offset}, limit=5000)'
        )

        return result
