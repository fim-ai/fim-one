"""Built-in tools with auto-discovery.

Any module in this package that defines a concrete ``BaseTool`` subclass will
be automatically discovered by :func:`discover_builtin_tools`.  No manual
registration is needed — just drop a new ``<name>.py`` file here.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING

from fim_agent.core.tool.base import BaseTool

if TYPE_CHECKING:
    from fim_agent.core.tool.base import Tool

logger = logging.getLogger(__name__)

# Explicit re-exports (for convenience — callers can still import directly)
from .calculator import CalculatorTool
from .file_ops import FileOpsTool
from .grounded_retrieve import GroundedRetrieveTool
from .http_request import HttpRequestTool
from .kb_retrieve import KBRetrieveTool
from .kb_list import KBListTool
from .python_exec import PythonExecTool
from .shell_exec import ShellExecTool
from .web_fetch import WebFetchTool
from .web_search import WebSearchTool

__all__ = [
    "CalculatorTool",
    "FileOpsTool",
    "GroundedRetrieveTool",
    "HttpRequestTool",
    "KBRetrieveTool",
    "KBListTool",
    "PythonExecTool",
    "ShellExecTool",
    "WebFetchTool",
    "WebSearchTool",
    "discover_builtin_tools",
]

# Mapping from tool class to the keyword argument name for sandbox paths.
_SANDBOX_KWARGS: dict[type, str] = {
    FileOpsTool: "workspace_dir",
    ShellExecTool: "sandbox_dir",
    PythonExecTool: "exec_dir",
}

# Tools that require explicit configuration and should NOT be auto-discovered.
# They are registered manually when the appropriate config is available.
_SKIP_AUTO_DISCOVER: set[type] = {
    GroundedRetrieveTool,  # requires kb_ids — registered by _resolve_tools()
}


def discover_builtin_tools(
    *,
    sandbox_root: Path | None = None,
) -> list[Tool]:
    """Auto-discover and instantiate all built-in tools.

    Scans every module in this package, finds concrete ``BaseTool`` subclasses,
    and returns a fresh instance of each.

    When *sandbox_root* is provided (a per-conversation directory), sandboxed
    tools (``FileOpsTool``, ``ShellExecTool``, ``PythonExecTool``) receive
    their respective sub-directory under that root::

        sandbox_root/
        ├── workspace/   → FileOpsTool(workspace_dir=...)
        ├── sandbox/     → ShellExecTool(sandbox_dir=...)
        └── exec/        → PythonExecTool(exec_dir=...)

    Tools that do not accept a sandbox parameter are instantiated with their
    zero-arg constructor as before.

    Tools that fail to import or instantiate are logged and skipped.
    """
    # Pre-compute per-tool sandbox paths when a root is provided.
    sandbox_paths: dict[str, Path] = {}
    if sandbox_root is not None:
        sandbox_paths = {
            "workspace_dir": sandbox_root / "workspace",
            "sandbox_dir": sandbox_root / "sandbox",
            "exec_dir": sandbox_root / "exec",
        }

    tools: list[Tool] = []
    package_path = __path__  # type: ignore[name-defined]
    package_name = __name__

    for finder, module_name, _is_pkg in pkgutil.iter_modules(package_path):
        fqn = f"{package_name}.{module_name}"
        try:
            mod = importlib.import_module(fqn)
        except Exception:
            logger.warning("Failed to import tool module %s", fqn, exc_info=True)
            continue

        for _attr_name, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(obj, BaseTool)
                and obj is not BaseTool
                and not inspect.isabstract(obj)
                and obj not in _SKIP_AUTO_DISCOVER
            ):
                try:
                    # Pass sandbox path to tools that support it.
                    kwarg_name = _SANDBOX_KWARGS.get(obj)
                    if kwarg_name and kwarg_name in sandbox_paths:
                        tools.append(obj(**{kwarg_name: sandbox_paths[kwarg_name]}))
                    else:
                        tools.append(obj())
                except Exception:
                    logger.warning(
                        "Failed to instantiate tool %s.%s",
                        fqn,
                        obj.__name__,
                        exc_info=True,
                    )
    return tools
