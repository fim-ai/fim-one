"""Built-in tool for Jinja2 template rendering."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sys
import weakref
from pathlib import Path
from typing import Any

from ..base import BaseTool, ToolResult

try:
    from . import _template_worker as _worker

    _JINJA2_AVAILABLE = True
    # The sandbox itself; rendering for the tool happens in a child process.
    _build_environment = _worker.build_environment
except ImportError:
    _JINJA2_AVAILABLE = False

logger = logging.getLogger(__name__)

_MAX_FILENAME_LEN = 120
# Largest reply accepted from the render process (the worker caps its output
# in characters; this is the same cap in bytes, with room for JSON escaping).
_MAX_REPLY_BYTES = 16 * 1024 * 1024
_MAX_TEMPLATE_CHARS = 1_000_000
_MAX_CONTEXT_CHARS = 5_000_000


def _env_number(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return min(max(value, low), high)


def _render_timeout_seconds() -> float:
    return _env_number("TEMPLATE_RENDER_TIMEOUT_SECONDS", 10, 1, 120)


def _render_memory_bytes() -> int:
    return int(_env_number("TEMPLATE_RENDER_MEMORY_MB", 256, 64, 4096)) * 1024 * 1024


def _render_concurrency() -> int:
    return int(_env_number("TEMPLATE_RENDER_CONCURRENCY", 4, 1, 64))


# One semaphore per event loop: how many render processes run at once.
_SLOTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def _slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    slots = _SLOTS.get(loop)
    if slots is None:
        slots = asyncio.Semaphore(_render_concurrency())
        _SLOTS[loop] = slots
    return slots


async def _resident_bytes(pid: int) -> int | None:
    """Resident memory of *pid*, or ``None`` when it cannot be read."""
    try:
        with open(f"/proc/{pid}/statm", encoding="ascii") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        pass
    try:
        ps = await asyncio.create_subprocess_exec(
            "ps", "-o", "rss=", "-p", str(pid),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": os.defpath},
        )
        out, _ = await asyncio.wait_for(ps.communicate(), timeout=2)
        return int(out.split()[0]) * 1024
    except (OSError, ValueError, IndexError, TimeoutError):
        return None


def _kill(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the render process and everything in its process group."""
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def _render_isolated(template_str: str, ctx: dict[str, Any]) -> str:
    """Render in a child process that is killed when it runs out of time or memory.

    The child gets an empty environment (nothing of the application's
    configuration or secrets), an interpreter in isolated mode, and no
    inherited file descriptors beyond its pipes.  The limits are enforced
    from here: a template stuck in a loop, or inside one large allocation,
    cannot ignore a SIGKILL the way a thread ignores a timeout.
    """
    timeout = _render_timeout_seconds()
    memory = _render_memory_bytes()
    request = json.dumps({"template": template_str, "context": ctx}).encode("utf-8")
    deadline = asyncio.get_running_loop().time() + timeout

    slots = _slots()
    try:
        await asyncio.wait_for(slots.acquire(), timeout=timeout)
    except TimeoutError:
        return "[Error] Too many templates are being rendered; try again shortly."
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", _worker.__file__,
            str(int(timeout) + 2), str(memory * 4),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": os.defpath, "LC_ALL": "C.UTF-8"},
            cwd=os.path.dirname(_worker.__file__),
            start_new_session=True,
            close_fds=True,
        )
        child = proc
        verdict: list[str] = []

        async def _watch_memory() -> None:
            while child.returncode is None:
                used = await _resident_bytes(child.pid)
                if used is not None and used > memory:
                    verdict.append(
                        f"[Error] Rendering needs more than {memory // (1024 * 1024)} MB of memory."
                    )
                    _kill(child)
                    return
                await asyncio.sleep(0.05)

        async def _exchange() -> bytes:
            assert child.stdin is not None and child.stdout is not None
            try:
                child.stdin.write(request)
                await child.stdin.drain()
                child.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass
            reply = await child.stdout.read(_MAX_REPLY_BYTES + 1)
            if len(reply) > _MAX_REPLY_BYTES:
                verdict.append("[Error] Rendered output is too large.")
                _kill(child)
                return b""
            # ``read(n)`` returns early; keep reading until EOF or the cap.
            while True:
                more = await child.stdout.read(_MAX_REPLY_BYTES + 1 - len(reply))
                if not more:
                    break
                reply += more
                if len(reply) > _MAX_REPLY_BYTES:
                    verdict.append("[Error] Rendered output is too large.")
                    _kill(child)
                    return b""
            await child.wait()
            return reply

        watcher = asyncio.create_task(_watch_memory())
        try:
            reply = await asyncio.wait_for(
                _exchange(), timeout=max(0.1, deadline - asyncio.get_running_loop().time())
            )
        except TimeoutError:
            return f"[Error] Rendering took longer than {timeout:g} seconds and was stopped."
        finally:
            watcher.cancel()
            _kill(child)

        if verdict:
            return verdict[0]
        try:
            output = json.loads(reply.decode("utf-8"))["output"]
        except (ValueError, KeyError, TypeError):
            if child.returncode not in (0, None):
                return "[Error] Rendering was stopped: it ran out of time or memory."
            return "[Error] Rendering failed."
        return str(output)
    except OSError as exc:
        logger.warning("Could not start the template render process: %s", exc)
        return "[Error] The template renderer is unavailable."
    finally:
        if proc is not None:
            _kill(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:  # pragma: no cover - a SIGKILLed child exits
                pass
        slots.release()


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
        template_str = kwargs.get("template", "")
        raw_context = kwargs.get("context", "{}") or "{}"

        if not template_str:
            return "[Error] 'template' is required."
        if not isinstance(template_str, str) or not isinstance(raw_context, str):
            return "[Error] 'template' and 'context' must be strings."
        if len(template_str) > _MAX_TEMPLATE_CHARS or len(raw_context) > _MAX_CONTEXT_CHARS:
            return "[Error] 'template' or 'context' is too large."

        try:
            ctx = json.loads(raw_context)
        except (json.JSONDecodeError, RecursionError) as e:
            return f"[Error] 'context' is not valid JSON: {e}"
        if not isinstance(ctx, dict):
            return "[Error] 'context' must be a JSON object."

        if _JINJA2_AVAILABLE:
            # In a child process with a time and a memory limit: a template
            # is a program, and a thread cannot be stopped.
            result = await _render_isolated(template_str, ctx)
        else:
            result = self._render_stdlib(template_str, ctx)

        if result.startswith("[Error]"):
            return result
        return await asyncio.to_thread(self._deliver, result, kwargs.get("filename"))

    def _deliver(self, result: str, requested_filename: Any) -> str | ToolResult:
        # HTML → artifact + iframe preview
        if self._artifacts_dir and self._looks_like_html(result):
            from ..artifact_utils import save_content_artifact

            filename = self._sanitize_filename(requested_filename)
            # Also materialise the output in the shared workspace so the
            # agent can treat it as a real file (list/copy/deliver) instead
            # of regenerating the content by hand — the regenerated copy is
            # never byte-identical and used to surface as a duplicate
            # deliverable card.
            if self._workspace_dir is not None:
                # Best-effort; the artifact still exists if this fails.
                self._write_workspace_file(self._workspace_dir, filename, result)

            artifact = save_content_artifact(result, filename, self._artifacts_dir)
            return ToolResult(content=result, content_type="html", artifacts=[artifact])

        # Everything else → markdown (GFM is a superset of plain text,
        # so tables, JSON code blocks, lists, and raw text all render correctly)
        return ToolResult(content=result, content_type="markdown")

    @staticmethod
    def _write_workspace_file(workspace_dir: Path, filename: str, content: str) -> bool:
        """Write *content* to ``workspace_dir/filename`` without leaving the dir.

        The workspace is shared with sandboxed code, which can plant a
        symlink, a FIFO or a swapped directory under the names about to be
        used.  The write goes through ``safe_fs``: no symlink is followed
        below the workspace root, and only a regular file is written.
        """
        if filename != Path(filename).name or filename in ("", ".", ".."):
            return False
        from fim_one.core.security import safe_fs

        root, parts = safe_fs.anchor_of(workspace_dir)
        try:
            safe_fs.write_text(root, (*parts, filename), content)
            return True
        except (OSError, ValueError):
            return False

    @staticmethod
    def _sanitize_filename(raw: Any) -> str:
        """Reduce a model-supplied filename to a safe basename.

        Falls back to ``rendered.html`` for empty, hidden, or path-like
        values.  The extension is normalised to ``.html`` since this path
        only runs for HTML output.
        """
        if not isinstance(raw, str) or not raw.strip():
            return "rendered.html"
        # Treat both separators as separators regardless of platform, and
        # drop control characters (NUL makes every path call raise).
        cleaned = re.sub(r"[\x00-\x1f\x7f]", "", raw.strip()).replace("\\", "/")
        name = cleaned.rsplit("/", 1)[-1].strip()
        if not name or name.startswith("."):
            return "rendered.html"
        if not name.lower().endswith((".html", ".htm")):
            name = f"{name}.html"
        if len(name) > _MAX_FILENAME_LEN:
            stem, _, ext = name.rpartition(".")
            name = f"{stem[: _MAX_FILENAME_LEN - len(ext) - 1]}.{ext}"
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

    def _render_stdlib(self, template_str: str, ctx: dict[str, Any]) -> str:
        from string import Template

        try:
            return Template(template_str).safe_substitute(ctx)
        except Exception as exc:
            return f"[Error] {type(exc).__name__}: {exc}"
