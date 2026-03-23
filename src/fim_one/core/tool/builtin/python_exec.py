"""Built-in tool for executing Python code in a sandboxed namespace."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..base import BaseTool, ToolResult
from ..sandbox import get_sandbox_backend
from ..truncation import truncate_bytes

_DEFAULT_TIMEOUT_SECONDS: int = int(os.environ.get("SANDBOX_TIMEOUT", "120"))

# Default directory for code execution outputs (plots, data files, etc.)
_DEFAULT_EXEC_DIR = Path(__file__).resolve().parents[4] / "tmp" / "default" / "exec"


class PythonExecTool(BaseTool):
    """Execute Python code and capture its printed output.

    The code runs inside the configured sandbox backend (local or docker).
    Standard output is captured so that anything written via ``print()`` is
    returned as the tool result.

    A configurable timeout (default 120 s) guards against runaway
    execution.
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

    # ------------------------------------------------------------------
    # Tool protocol properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "python_exec"

    @property
    def display_name(self) -> str:
        return "Python"

    @property
    def category(self) -> str:
        return "computation"

    @property
    def description(self) -> str:
        return (
            "Execute Python code and return the output. "
            "Use print() to produce output. "
            "Only standard-library modules are guaranteed to be available. "
            "Do NOT attempt to install packages (pip install / uv add) — "
            "it will likely fail or timeout. If a library is unavailable, "
            "fall back to a standard-library-only solution."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute.",
                },
            },
            "required": ["code"],
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run(self, **kwargs: Any) -> str | ToolResult:  # type: ignore[override]
        """Execute the provided Python *code* and return captured stdout.

        Args:
            **kwargs: Must contain ``code`` (str).

        Returns:
            The captured standard output, or an error/timeout message on failure.
        """
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
            language="python",
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
