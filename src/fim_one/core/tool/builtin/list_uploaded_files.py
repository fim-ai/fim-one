"""List all files uploaded by the user."""

from __future__ import annotations

from typing import Any

from fim_one.core.tool.base import BaseTool


class ListUploadedFilesTool(BaseTool):
    """List all files uploaded by the user.

    Returns file IDs, names, sizes, and content availability so the agent
    can discover which files are available for reading or searching.
    """

    def __init__(self, user_id: str) -> None:
        self._user_id = user_id

    @property
    def name(self) -> str:
        return "list_uploaded_files"

    @property
    def cacheable(self) -> bool:
        return False

    @property
    def display_name(self) -> str:
        return "List Uploaded Files"

    @property
    def description(self) -> str:
        return (
            "List all files uploaded by the user. Returns file IDs, names, "
            "sizes, and content availability. Use this to discover which "
            "files are available for reading or searching."
        )

    @property
    def category(self) -> str:
        return "files"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "include_preview": {
                    "type": "boolean",
                    "description": (
                        "If true, include first 200 chars of content preview "
                        "for each file."
                    ),
                    "default": False,
                },
            },
            "required": [],
        }

    async def run(self, **kwargs: Any) -> str:
        include_preview: bool = bool(kwargs.get("include_preview", False))

        try:
            from fim_one.web.api.files import _load_index

            index = _load_index(self._user_id)

            if not index:
                return "No files uploaded."

            count = len(index)
            lines: list[str] = [
                f"{count} file{'s' if count != 1 else ''} uploaded:",
                "",
            ]

            for i, (file_id, meta) in enumerate(index.items(), start=1):
                filename = meta.get("filename", "unknown")
                mime_type = meta.get("mime_type", "unknown")
                content_length = meta.get("content_length")

                if content_length is not None:
                    size_info = f"{content_length} chars"
                else:
                    size_info = "[no text content]"

                lines.append(f"{i}. {filename} (file_id: {file_id})")
                lines.append(f"   {size_info} | {mime_type}")

                if include_preview and content_length is not None:
                    preview: str = str(meta.get("content_preview", ""))
                    if preview:
                        truncated = preview[:200]
                        if len(preview) > 200:
                            truncated += "..."
                        lines.append(f"   Preview: {truncated}")

                lines.append("")

            return "\n".join(lines).rstrip()

        except Exception as exc:
            return f"[Error] {type(exc).__name__}: {exc}"
