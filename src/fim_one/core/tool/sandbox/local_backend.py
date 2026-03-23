"""Local (in-process / subprocess) sandbox backend.

Python code runs in the same process via ``exec()`` with a restricted
namespace.  Shell commands run via ``asyncio.create_subprocess_shell``.
JavaScript runs via ``node -e`` if Node.js is available on PATH.

Security model: blocklists + restricted builtins (Python only).
For proper OS-level isolation use :class:`DockerBackend` instead.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.abc
import importlib.machinery
import io
import logging
import os
import sys
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any

from .protocol import SandboxResult
from ._env_utils import build_safe_env

import os as _os

# Serialises global-state mutation in _execute_python_sync so that concurrent
# asyncio.to_thread calls don't race on os.chdir / sys.stdout / sys.stderr.
_SANDBOX_EXEC_LOCK = threading.Lock()

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Python sandbox helpers (local mode only — Docker mode doesn't need these)
# -----------------------------------------------------------------------


def _safe_getattr(obj: object, name: str, *args: object) -> object:
    """getattr wrapper that blocks dunder attribute access."""
    if isinstance(name, str) and name.startswith("__") and name.endswith("__"):
        raise AttributeError(f"Access to '{name}' is not allowed in sandbox")
    return getattr(obj, name, *args)


def _safe_setattr(obj: object, name: str, value: object) -> None:
    """setattr wrapper that blocks dunder attribute access."""
    if isinstance(name, str) and name.startswith("__") and name.endswith("__"):
        raise AttributeError(f"Access to '{name}' is not allowed in sandbox")
    setattr(obj, name, value)


def _safe_delattr(obj: object, name: str) -> None:
    """delattr wrapper that blocks dunder attribute access."""
    if isinstance(name, str) and name.startswith("__") and name.endswith("__"):
        raise AttributeError(f"Access to '{name}' is not allowed in sandbox")
    delattr(obj, name)


# Whitelisted builtins exposed to executed code.
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
        "hash", "id", "callable", "vars", "dir", "help",
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
        "ascii",
        # Internal helpers required by the interpreter for class/with/etc.
        "__build_class__", "__name__",
    )
    if (isinstance(__builtins__, dict) and name in __builtins__)
    or (not isinstance(__builtins__, dict) and hasattr(__builtins__, name))
}

# Override getattr/setattr/delattr with safe wrappers that block dunder access
# (prevents bypass of AST-level dunder checks via getattr(obj, '__class__') etc.)
_SAFE_BUILTINS["getattr"] = _safe_getattr
_SAFE_BUILTINS["setattr"] = _safe_setattr
_SAFE_BUILTINS["delattr"] = _safe_delattr

# Whitelist of modules allowed to be imported by user code.
_ALLOWED_MODULES: frozenset[str] = frozenset({
    "math", "cmath", "json", "re", "datetime", "time", "collections", "itertools",
    "functools", "string", "textwrap", "decimal", "fractions", "statistics", "random",
    "hashlib", "base64", "urllib.parse", "dataclasses", "enum", "typing", "copy", "operator",
    "pprint", "io", "csv", "struct", "array", "bisect", "heapq", "uuid", "html", "difflib",
    "unicodedata", "numpy", "pandas", "matplotlib", "matplotlib.pyplot", "scipy", "sklearn",
})

_ALLOWED_TOP_MODULES: frozenset[str] = frozenset({m.split(".")[0] for m in _ALLOWED_MODULES})


def _safe_import(
    name: str,
    globals: dict[str, Any] | None = None,
    locals: dict[str, Any] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> Any:
    """Whitelist-based import function for the sandbox."""
    if level != 0:
        raise ImportError("Relative imports are not allowed in the sandbox")
    top = name.split(".")[0]
    if name not in _ALLOWED_MODULES and top not in _ALLOWED_TOP_MODULES:
        raise ImportError(f"Import of '{name}' is not allowed in the sandbox")
    import builtins
    return builtins.__import__(name, globals, locals, fromlist, level)


def _make_safe_open(exec_dir: Path) -> Any:
    """Create a sandbox-restricted ``open()`` that only allows access within *exec_dir*."""
    def _safe_open(file: str | Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        file_path = (exec_dir / str(file)).resolve()
        if not file_path.is_relative_to(exec_dir.resolve()):
            raise PermissionError(
                f"File access outside sandbox directory is not allowed: {file}"
            )
        return open(file_path, mode, *args, **kwargs)
    return _safe_open


def _validate_python_ast(code: str) -> str | None:
    """Reject code that accesses dunder attributes (e.g. __class__, __bases__).

    Returns an error message if dangerous patterns are found, None if safe.
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return None  # Let exec() handle syntax errors with proper tracebacks

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            return (
                f"Access to dunder attribute '{node.attr}' is not allowed. "
                "Direct attribute access to double-underscore names is blocked for security."
            )
    return None


# Modules that should never be importable from user code (local mode only).
# Defense-in-depth: even though _safe_import uses a whitelist, the blocklist
# prevents importation if user code somehow bypasses the whitelist check.
_BLOCKED_MODULES: frozenset[str] = frozenset({
    "subprocess",
    "shutil",
    "ctypes",
    "multiprocessing",
    "signal",
    "resource",
    "os",
    "sys",
    "socket",
    "http",
    "urllib",
    "pathlib",
    "importlib",
    "pty",
    "pickle",
    "marshal",
    "code",
    "gc",
    "inspect",
    "threading",
    "asyncio",
    "webbrowser",
    "_thread",
    "select",
    "tempfile",
    "glob",
    "sqlite3",
    "smtplib",
    "ftplib",
    "telnetlib",
})


class _BlockedImportFinder(importlib.abc.MetaPathFinder):
    """A sys.meta_path finder that raises ImportError for blocked modules."""

    def find_module(self, fullname: str, path: Any = None) -> None:
        top_level = fullname.split(".")[0]
        if top_level in _BLOCKED_MODULES:
            raise ImportError(f"Import of '{fullname}' is blocked in the sandbox.")
        return None

    def find_spec(self, fullname: str, path: Any, target: Any = None) -> None:
        top_level = fullname.split(".")[0]
        if top_level in _BLOCKED_MODULES:
            raise ImportError(f"Import of '{fullname}' is blocked in the sandbox.")
        return None


# -----------------------------------------------------------------------
# LocalBackend
# -----------------------------------------------------------------------


class LocalBackend:
    """Sandbox backend that runs code locally without OS-level isolation.

    Python:     in-process ``exec()`` with restricted builtins + import blocklist
    JavaScript: spawns ``node -e <code>`` (requires Node.js on PATH)
    Shell:      spawns ``asyncio.create_subprocess_shell``
    """

    def __init__(self) -> None:
        logger.warning(
            "python_exec is using the LOCAL sandbox backend — no OS-level isolation. "
            "Use CODE_EXEC_BACKEND=docker in production multi-user deployments."
        )

    async def run_code(
        self,
        code: str,
        *,
        language: str,
        exec_dir: Path,
        timeout: int,
        memory: str | None = None,
        cpu: float | None = None,
    ) -> SandboxResult:
        if language == "python":
            return await self._run_python(code, exec_dir=exec_dir, timeout=timeout)
        elif language == "javascript":
            return await self._run_node(code, exec_dir=exec_dir, timeout=timeout)
        else:
            return SandboxResult(
                stdout="",
                stderr="",
                exit_code=-1,
                error=f"Unsupported language '{language}' in local backend.",
            )

    async def run_shell(
        self,
        command: str,
        *,
        sandbox_dir: Path,
        timeout: int,
        memory: str | None = None,
        cpu: float | None = None,
    ) -> SandboxResult:
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("local run_shell: sandbox_dir=%s timeout=%ds", sandbox_dir, timeout)

        # Build a restricted env (local mode only — docker has its own isolation)
        env = build_safe_env(str(sandbox_dir))

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(sandbox_dir),
                env=env,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
                return SandboxResult(
                    stdout="",
                    stderr="",
                    exit_code=124,
                    timed_out=True,
                )
        except OSError as exc:
            return SandboxResult(
                stdout="", stderr="", exit_code=-1, error=str(exc)
            )

        return SandboxResult(
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            exit_code=proc.returncode or 0,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_python(
        self, code: str, *, exec_dir: Path, timeout: int
    ) -> SandboxResult:
        """Run Python code in-process with restricted sandbox."""
        logger.debug("local run_python: exec_dir=%s timeout=%ds pid=%d", exec_dir, timeout, _os.getpid())
        exec_dir.mkdir(parents=True, exist_ok=True)
        script = exec_dir / f"script_{uuid.uuid4().hex[:8]}.py"
        script.write_text(code, encoding="utf-8")

        try:
            output: str = await asyncio.wait_for(
                asyncio.to_thread(self._execute_python_sync, code, exec_dir),
                timeout=timeout,
            )
        except TimeoutError:
            return SandboxResult(
                stdout="", stderr="", exit_code=124, timed_out=True
            )

        # _execute_python_sync returns either stdout or a traceback string
        # (stderr is mixed into stdout for legacy compat; exit_code derived)
        exit_code = 0 if not output.startswith("Traceback") else 1
        result = SandboxResult(stdout=output, stderr="", exit_code=exit_code)
        result.script_path = script
        return result

    def _execute_python_sync(self, code: str, exec_dir: Path) -> str:
        """Synchronous in-process Python execution (called from thread pool)."""
        # Pre-configure matplotlib CJK fonts if available.
        try:
            import matplotlib  # type: ignore[import-not-found]
            matplotlib.rcParams["font.sans-serif"] = [
                "PingFang SC", "STHeiti", "SimHei", "Helvetica",
            ]
            matplotlib.rcParams["axes.unicode_minus"] = False
        except ImportError:
            pass

        exec_dir.mkdir(parents=True, exist_ok=True)

        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        # Build a restricted builtins dict with safe import and sandboxed open
        _sandbox_builtins = {**_SAFE_BUILTINS, "__import__": _safe_import, "open": _make_safe_open(exec_dir)}
        namespace: dict[str, Any] = {"__builtins__": _sandbox_builtins}

        # AST validation — block dunder attribute access patterns
        ast_error = _validate_python_ast(code)
        if ast_error is not None:
            return f"[Sandbox] {ast_error}"

        import_blocker = _BlockedImportFinder()
        sys.meta_path.insert(0, import_blocker)

        _saved_modules: dict[str, Any] = {}
        for mod_name in list(sys.modules):
            top = mod_name.split(".")[0]
            if top in _BLOCKED_MODULES:
                _saved_modules[mod_name] = sys.modules.pop(mod_name)

        try:
            with _SANDBOX_EXEC_LOCK:
                old_stdout = sys.stdout
                old_stderr = sys.stderr
                old_cwd = os.getcwd()
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
        finally:
            sys.modules.update(_saved_modules)
            try:
                sys.meta_path.remove(import_blocker)
            except ValueError:
                pass

        output = stdout_capture.getvalue()
        stderr_text = stderr_capture.getvalue()
        if stderr_text:
            output = output + "[stderr]\n" + stderr_text
        return output

    async def _run_node(
        self, code: str, *, exec_dir: Path, timeout: int
    ) -> SandboxResult:
        """Run JavaScript code via ``node <script_file>``."""
        logger.debug("local run_node: exec_dir=%s timeout=%ds", exec_dir, timeout)
        exec_dir.mkdir(parents=True, exist_ok=True)
        script = exec_dir / f"script_{uuid.uuid4().hex[:8]}.js"
        script.write_text(code, encoding="utf-8")

        try:
            proc = await asyncio.create_subprocess_exec(
                "node", str(script),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(exec_dir),
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
                return SandboxResult(
                    stdout="", stderr="", exit_code=124, timed_out=True
                )
        except FileNotFoundError:
            return SandboxResult(
                stdout="",
                stderr="",
                exit_code=-1,
                error=(
                    "Node.js is not installed or not on PATH. "
                    "To run JavaScript, set CODE_EXEC_BACKEND=docker."
                ),
            )

        result = SandboxResult(
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            exit_code=proc.returncode or 0,
        )
        result.script_path = script
        return result
