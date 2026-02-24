"""Built-in tool for executing Python code in a sandboxed namespace."""

from __future__ import annotations

import asyncio
import io
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from ..base import BaseTool

_DEFAULT_TIMEOUT_SECONDS: int = 120

# Temp directory for code execution outputs (plots, data files, etc.)
_TMP_DIR = Path(__file__).resolve().parents[4] / "tmp"
_TMP_DIR.mkdir(exist_ok=True)


class PythonExecTool(BaseTool):
    """Execute Python code and capture its printed output.

    The code runs inside ``exec()`` with a restricted global namespace.
    Standard output is redirected to a ``StringIO`` buffer so that
    anything written via ``print()`` is returned as the tool result.

    A configurable timeout (default 120 s) guards against runaway
    execution.
    """

    def __init__(self, *, timeout: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Tool protocol properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "python_exec"

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

    async def run(self, **kwargs: Any) -> str:
        """Execute the provided Python *code* and return captured stdout.

        Args:
            **kwargs: Must contain ``code`` (str).

        Returns:
            The captured standard output, or the traceback string on error.
        """
        code: str = kwargs.get("code", "")
        if not code.strip():
            return ""

        try:
            result: str = await asyncio.wait_for(
                asyncio.to_thread(self._execute_sync, code),
                timeout=self._timeout,
            )
        except TimeoutError:
            return f"[Timeout] Execution exceeded {self._timeout} seconds."
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _execute_sync(code: str) -> str:
        """Run *code* synchronously in a restricted namespace.

        Stdout is captured via a ``StringIO`` buffer that temporarily
        replaces ``sys.stdout``.
        """
        # Pre-configure matplotlib CJK fonts if available.
        try:
            import matplotlib
            matplotlib.rcParams["font.sans-serif"] = [
                "PingFang SC", "STHeiti", "SimHei", "Helvetica",
            ]
            matplotlib.rcParams["axes.unicode_minus"] = False
        except ImportError:
            pass

        capture = io.StringIO()
        namespace: dict[str, Any] = {"__builtins__": __builtins__}
        old_stdout = sys.stdout
        old_cwd = os.getcwd()
        try:
            os.chdir(_TMP_DIR)
            sys.stdout = capture
            exec(code, namespace)
        except Exception:
            return traceback.format_exc()
        finally:
            sys.stdout = old_stdout
            os.chdir(old_cwd)
        return capture.getvalue()
