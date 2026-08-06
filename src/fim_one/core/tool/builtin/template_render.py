"""Built-in tool for Jinja2 template rendering."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from ..base import BaseTool, ToolResult

try:
    from jinja2 import Environment, StrictUndefined, TemplateError

    _JINJA2_AVAILABLE = True
except ImportError:
    _JINJA2_AVAILABLE = False


class TemplateRenderTool(BaseTool):
    """Render Jinja2 templates with a provided context of variables."""

    def __init__(
        self,
        *,
        artifacts_dir: Path | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self._artifacts_dir = artifacts_dir
        self._workspace_dir = workspace_dir

    @property
    def name(self) -> str:
        return "template_render"

    @property
    def cacheable(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        return "Template Render"

    @property
    def category(self) -> str:
        return "general"

    @property
    def description(self) -> str:
        if _JINJA2_AVAILABLE:
            return (
                "Render a Jinja2 template with provided context variables. "
                "Supports {{ variable }}, {% if %}...{% endif %}, "
                "{% for item in list %}...{% endfor %}, and Jinja2 filters ({{ text | upper }}). "
                "Parameters: template (Jinja2 template string), "
                "context (a JSON object whose keys become template variables), "
                "filename (optional output filename, e.g. 'report.html'). "
                "HTML output is saved to the workspace under that filename and "
                "offered for download — do NOT re-save or rewrite it with other tools."
            )
        return (
            "Render a template with $variable substitution (Python string.Template). "
            "Use $key or ${key} syntax. "
            "Parameters: template (template string), "
            "context (a JSON object whose keys become template variables), "
            "filename (optional output filename, e.g. 'report.html'). "
            "HTML output is saved to the workspace under that filename and "
            "offered for download — do NOT re-save or rewrite it with other tools."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "template": {
                    "type": "string",
                    "description": "The Jinja2 template string (or $variable template if Jinja2 unavailable).",
                },
                "context": {
                    "type": "string",
                    "description": (
                        "A JSON object of variables to inject into the template. "
                        'Example: {"name": "Alice", "order_id": "ORD-123"}'
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Output filename for the rendered result (e.g. "
                        "'report.html'). HTML output is saved to the workspace "
                        "under this name and offered for download. "
                        "Defaults to 'rendered.html'."
                    ),
                },
            },
            "required": ["template"],
        }

    async def run(self, **kwargs: Any) -> str | ToolResult:  # type: ignore[override]
        return await asyncio.to_thread(self._run_sync, **kwargs)

    def _run_sync(self, **kwargs: Any) -> str | ToolResult:
        template_str: str = kwargs.get("template", "")
        raw_context: str = kwargs.get("context", "{}") or "{}"

        if not template_str:
            return "[Error] 'template' is required."

        try:
            ctx = json.loads(raw_context)
        except json.JSONDecodeError as e:
            return f"[Error] 'context' is not valid JSON: {e}"
        if not isinstance(ctx, dict):
            return "[Error] 'context' must be a JSON object."

        if _JINJA2_AVAILABLE:
            result = self._render_jinja2(template_str, ctx)
        else:
            result = self._render_stdlib(template_str, ctx)

        if result.startswith("[Error]"):
            return result

        # HTML → artifact + iframe preview
        if self._artifacts_dir and self._looks_like_html(result):
            from ..artifact_utils import save_content_artifact

            filename = self._sanitize_filename(kwargs.get("filename"))
            # Also materialise the output in the shared workspace so the
            # agent can treat it as a real file (list/copy/deliver) instead
            # of regenerating the content by hand — the regenerated copy is
            # never byte-identical and used to surface as a duplicate
            # deliverable card.
            if self._workspace_dir is not None:
                try:
                    self._workspace_dir.mkdir(parents=True, exist_ok=True)
                    (self._workspace_dir / filename).write_text(
                        result, encoding="utf-8",
                    )
                except OSError:
                    pass  # workspace write is best-effort; the artifact still exists

            artifact = save_content_artifact(result, filename, self._artifacts_dir)
            return ToolResult(content=result, content_type="html", artifacts=[artifact])

        # Everything else → markdown (GFM is a superset of plain text,
        # so tables, JSON code blocks, lists, and raw text all render correctly)
        return ToolResult(content=result, content_type="markdown")

    @staticmethod
    def _sanitize_filename(raw: Any) -> str:
        """Reduce a model-supplied filename to a safe basename.

        Falls back to ``rendered.html`` for empty, hidden, or path-like
        values.  The extension is normalised to ``.html`` since this path
        only runs for HTML output.
        """
        if not isinstance(raw, str) or not raw.strip():
            return "rendered.html"
        name = Path(raw.strip()).name
        if not name or name.startswith("."):
            return "rendered.html"
        if not name.lower().endswith((".html", ".htm")):
            name = f"{name}.html"
        return name

    # Block-level HTML tags that strongly signal renderable HTML content
    _BLOCK_TAG_RE = re.compile(
        r"<(?:div|table|section|article|header|footer|main|nav|ul|ol|form|svg|canvas)\b",
        re.IGNORECASE,
    )

    @staticmethod
    def _looks_like_html(text: str) -> bool:
        """Detect whether rendered output is HTML (full document or fragment).

        Handles three cases:
        1. Full document: ``<html`` or ``<!doctype``
        2. Fragment with embedded CSS: ``<style>...</style>``
        3. Fragment with multiple block-level tags (e.g. ``<div>...<table>...``)
        """
        lower = text.lower()
        # Full HTML document
        if "<html" in lower or "<!doctype" in lower:
            return True
        # Embedded <style> block is a very strong signal
        if "<style" in lower and "</style>" in lower:
            return True
        # Two or more block-level HTML tags → likely an HTML fragment
        if len(TemplateRenderTool._BLOCK_TAG_RE.findall(text)) >= 2:
            return True
        return False

    def _render_jinja2(self, template_str: str, ctx: dict[str, Any]) -> str:
        try:
            env = Environment(undefined=StrictUndefined, autoescape=False)
            return env.from_string(template_str).render(**ctx)
        except TemplateError as exc:
            available = ", ".join(sorted(ctx.keys())) if ctx else "(none)"
            return (
                f"[Error] Template error: {exc}. "
                f"Available context variables: {available}"
            )
        except Exception as exc:
            return f"[Error] {type(exc).__name__}: {exc}"

    def _render_stdlib(self, template_str: str, ctx: dict[str, Any]) -> str:
        from string import Template

        try:
            return Template(template_str).safe_substitute(ctx)
        except Exception as exc:
            return f"[Error] {type(exc).__name__}: {exc}"
