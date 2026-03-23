"""Built-in tool for executing JavaScript (Node.js) code in a sandbox."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..base import BaseTool, ToolResult
from ..sandbox import get_sandbox_backend
from ..truncation import truncate_bytes

_DEFAULT_TIMEOUT_SECONDS: int = int(os.environ.get("SANDBOX_TIMEOUT", "120"))

# Default directory for code execution outputs.
_DEFAULT_EXEC_DIR = Path(__file__).resolve().parents[4] / "tmp" / "default" / "exec"


class NodeExecTool(BaseTool):
    """Execute JavaScript (Node.js) code and capture its output.

    Local mode:  requires Node.js on PATH (``node -e <code>``).
                 If Node.js is not installed, returns a helpful error message.
    Docker mode: runs in ``node:20-slim`` container with full isolation.
                 Switch via ``CODE_EXEC_BACKEND=docker``.

    Files written to the execution directory are accessible to FileOpsTool
    via the shared per-conversation workspace.
    """

    def __init__(
        self,
        *,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
        exec_dir: Path | None = None,
        memory: str | None = None,
        cpu: float | None = None,
        artifacts_dir: Path | None = None,
    ) -> None:
        self._timeout = timeout
        self._exec_dir = exec_dir or _DEFAULT_EXEC_DIR
        self._memory = memory
        self._cpu = cpu
        self._artifacts_dir = artifacts_dir

    @property
    def name(self) -> str:
        return "node_exec"

    @property
    def display_name(self) -> str:
        return "JavaScript"

    @property
    def category(self) -> str:
        return "computation"

    def availability(self) -> tuple[bool, str | None]:
        # Docker backend pulls its own Node image — always available.
        if os.environ.get("CODE_EXEC_BACKEND", "local").lower() == "docker":
            return True, None
        import shutil
        if shutil.which("node") is None:
            return (
                False,
                "Node.js is not installed on this server. "
                "Install Node.js or set CODE_EXEC_BACKEND=docker.",
            )
        return True, None

    @property
    def description(self) -> str:
        return (
            "Execute JavaScript (Node.js) code and return the output. "
            "Use console.log() to produce output. "
            "Only Node.js built-in modules are guaranteed to be available. "
            "Do NOT attempt to install packages (npm install) — "
            "it will likely fail or timeout. "
            "In local mode, Node.js must be installed on the host. "
            "Switch to docker mode (CODE_EXEC_BACKEND=docker) for a fully "
            "isolated Node.js environment."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "JavaScript code to execute.",
                },
            },
            "required": ["code"],
        }

    async def run(self, **kwargs: Any) -> str | ToolResult:  # type: ignore[override]
        code: str = kwargs.get("code", "")
        if not code.strip():
            return ""

        # Lazily create exec directory on first use.
        self._exec_dir.mkdir(parents=True, exist_ok=True)

        # Snapshot files before execution for artifact detection.
        before: set[str] = set()
        if self._artifacts_dir:
            before = {f.name for f in self._exec_dir.iterdir() if f.is_file()}

        backend = get_sandbox_backend()
        result = await backend.run_code(
            code,
            language="javascript",
            exec_dir=self._exec_dir,
            timeout=self._timeout,
            memory=self._memory,
            cpu=self._cpu,
        )

        if result.timed_out:
            return f"[Timeout] Execution exceeded {self._timeout} seconds."
        if result.error:
            return f"[Error] {result.error}"

        output = result.stdout
        if result.stderr:
            output = output + "[stderr]\n" + result.stderr
        if result.script_path is not None:
            output = f"[Script: {result.script_path.name}]\n" + output
        output = truncate_bytes(output)

        # Scan for new files after execution.
        if self._artifacts_dir:
            from ..artifact_utils import scan_new_files
            from ..base import ToolResult

            artifacts = scan_new_files(self._exec_dir, before, self._artifacts_dir)
            if artifacts:
                return ToolResult(content=output, artifacts=artifacts)

        return output
