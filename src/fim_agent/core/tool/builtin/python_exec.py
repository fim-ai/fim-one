"""Built-in tool for executing Python code in a sandboxed namespace."""

from __future__ import annotations

import asyncio
import importlib.abc
import importlib.machinery
import io
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from ..base import BaseTool

_DEFAULT_TIMEOUT_SECONDS: int = 120

# Default directory for code execution outputs (plots, data files, etc.)
_DEFAULT_EXEC_DIR = Path(__file__).resolve().parents[4] / "tmp"

# Maximum captured output size (bytes) before truncation.
_MAX_OUTPUT_BYTES: int = 100 * 1024  # 100 KB

# -----------------------------------------------------------------------
# Sandbox helpers
# -----------------------------------------------------------------------

# Whitelisted builtins exposed to executed code.  We keep __import__ and
# open because the agent regularly imports stdlib modules and does file I/O
# for data processing.
_SAFE_BUILTINS: dict[str, Any] = {
    name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
    for name in (
        "print", "len", "range", "enumerate", "zip", "map", "filter",
        "sorted", "reversed", "list", "dict", "set", "tuple", "frozenset",
        "str", "int", "float", "bool", "complex", "bytes", "bytearray",
        "memoryview", "abs", "round", "min", "max", "sum", "any", "all",
        "isinstance", "issubclass", "type", "hasattr", "getattr", "setattr",
        "delattr", "repr", "format", "iter", "next", "slice",
        "chr", "ord", "hex", "oct", "bin", "pow", "divmod",
        "hash", "id", "callable", "vars", "dir", "help", "input",
        "open", "__import__",
        # Needed for common patterns: exceptions, None/True/False, etc.
        "None", "True", "False",
        "Exception", "BaseException", "ValueError", "TypeError",
        "KeyError", "IndexError", "AttributeError", "RuntimeError",
        "StopIteration", "StopAsyncIteration", "ArithmeticError",
        "ZeroDivisionError", "OverflowError", "FloatingPointError",
        "LookupError", "FileNotFoundError", "FileExistsError",
        "PermissionError", "OSError", "IOError", "EOFError",
        "ImportError", "ModuleNotFoundError", "NameError",
        "UnboundLocalError", "NotImplementedError", "RecursionError",
        "UnicodeError", "UnicodeDecodeError", "UnicodeEncodeError",
        # Comprehension / generator support
        "property", "staticmethod", "classmethod", "super",
        "object", "enumerate",
        # String / number helpers the agent uses
        "ascii", "breakpoint",
        # Internal helpers required by the interpreter for class/with/etc.
        "__build_class__", "__name__",
    )
    if (isinstance(__builtins__, dict) and name in __builtins__)
    or (not isinstance(__builtins__, dict) and hasattr(__builtins__, name))
}

# Modules that should never be importable from user code.
_BLOCKED_MODULES: frozenset[str] = frozenset({
    "subprocess",
    "shutil",
    "ctypes",
    "multiprocessing",
    "signal",
    "resource",
})


class _BlockedImportFinder(importlib.abc.MetaPathFinder):
    """A sys.meta_path finder that raises ImportError for blocked modules."""

    def find_module(
        self, fullname: str, path: Any = None,
    ) -> None:
        # find_module is the legacy protocol; returning None lets the next
        # finder handle it.  We raise directly for blocked names.
        top_level = fullname.split(".")[0]
        if top_level in _BLOCKED_MODULES:
            raise ImportError(
                f"Import of '{fullname}' is blocked in the sandbox."
            )
        return None

    # Python >= 3.4 prefers find_spec over find_module.
    def find_spec(
        self,
        fullname: str,
        path: Any,
        target: Any = None,
    ) -> None:
        top_level = fullname.split(".")[0]
        if top_level in _BLOCKED_MODULES:
            raise ImportError(
                f"Import of '{fullname}' is blocked in the sandbox."
            )
        return None


def _truncate_output(text: str) -> str:
    """Truncate *text* if it exceeds ``_MAX_OUTPUT_BYTES``."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_OUTPUT_BYTES:
        return text
    truncated = encoded[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    return (
        truncated
        + f"\n\n[Output truncated — exceeded {_MAX_OUTPUT_BYTES // 1024} KB limit]"
    )


class PythonExecTool(BaseTool):
    """Execute Python code and capture its printed output.

    The code runs inside ``exec()`` with a restricted global namespace.
    Standard output is redirected to a ``StringIO`` buffer so that
    anything written via ``print()`` is returned as the tool result.

    A configurable timeout (default 120 s) guards against runaway
    execution.
    """

    def __init__(
        self,
        *,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
        exec_dir: Path | None = None,
    ) -> None:
        self._timeout = timeout
        self._exec_dir = exec_dir or _DEFAULT_EXEC_DIR

    # ------------------------------------------------------------------
    # Tool protocol properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "python_exec"

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

    def _execute_sync(self, code: str) -> str:
        """Run *code* synchronously in a restricted namespace.

        Stdout and stderr are captured via ``StringIO`` buffers that
        temporarily replace ``sys.stdout`` / ``sys.stderr``.  A custom
        ``sys.meta_path`` finder blocks dangerous modules, and only a
        whitelisted subset of builtins is exposed.
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

        # Lazily create exec directory on first use.
        exec_dir = self._exec_dir
        exec_dir.mkdir(parents=True, exist_ok=True)

        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        namespace: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        old_cwd = os.getcwd()

        # Install the blocked-import finder at the front of meta_path.
        import_blocker = _BlockedImportFinder()
        sys.meta_path.insert(0, import_blocker)

        # Temporarily evict blocked modules (and their sub-modules) from
        # sys.modules so the import machinery cannot short-circuit via the
        # module cache.  They are restored in the finally block.
        _saved_modules: dict[str, Any] = {}
        for mod_name in list(sys.modules):
            top = mod_name.split(".")[0]
            if top in _BLOCKED_MODULES:
                _saved_modules[mod_name] = sys.modules.pop(mod_name)

        try:
            os.chdir(str(exec_dir))
            sys.stdout = stdout_capture
            sys.stderr = stderr_capture
            exec(code, namespace)
        except Exception:
            return traceback.format_exc()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            os.chdir(old_cwd)
            # Restore evicted modules so the host process is unaffected.
            sys.modules.update(_saved_modules)
            # Remove the blocker — use try/except in case it was already
            # removed (defensive).
            try:
                sys.meta_path.remove(import_blocker)
            except ValueError:
                pass

        output = stdout_capture.getvalue()
        stderr_text = stderr_capture.getvalue()
        if stderr_text:
            output = output + "[stderr]\n" + stderr_text
        return _truncate_output(output)
