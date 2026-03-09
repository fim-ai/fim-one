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
from .datetime_tool import DateTimeTool
from .email_send import EmailSendTool
from .file_ops import FileOpsTool
from .generate_image import GenerateImageTool
from .grounded_retrieve import GroundedRetrieveTool
from .http_request import HttpRequestTool
from .json_transform import JsonTransformTool
from .kb_retrieve import KBRetrieveTool
from .kb_list import KBListTool
from .node_exec import NodeExecTool
from .python_exec import PythonExecTool
from .shell_exec import ShellExecTool
from .template_render import TemplateRenderTool
from .text_utils import TextUtilsTool
from .web_fetch import WebFetchTool
from .web_search import WebSearchTool
from .connector_builder import (
    ConnectorListActionsTool,
    ConnectorCreateActionTool,
    ConnectorUpdateActionTool,
    ConnectorDeleteActionTool,
    ConnectorUpdateSettingsTool,
    ConnectorTestActionTool,
    ConnectorGetSettingsTool,
    ConnectorTestConnectionTool,
    ConnectorImportOpenAPITool,
)
from .agent_builder import (
    AgentGetSettingsTool,
    AgentUpdateSettingsTool,
    AgentListConnectorsTool,
    AgentAddConnectorTool,
    AgentRemoveConnectorTool,
    AgentSetModelTool,
)

__all__ = [
    "CalculatorTool",
    "DateTimeTool",
    "EmailSendTool",
    "FileOpsTool",
    "GenerateImageTool",
    "GroundedRetrieveTool",
    "HttpRequestTool",
    "JsonTransformTool",
    "KBRetrieveTool",
    "KBListTool",
    "NodeExecTool",
    "PythonExecTool",
    "ShellExecTool",
    "TemplateRenderTool",
    "TextUtilsTool",
    "WebFetchTool",
    "WebSearchTool",
    "ConnectorListActionsTool",
    "ConnectorCreateActionTool",
    "ConnectorUpdateActionTool",
    "ConnectorDeleteActionTool",
    "ConnectorUpdateSettingsTool",
    "ConnectorTestActionTool",
    "ConnectorGetSettingsTool",
    "ConnectorTestConnectionTool",
    "ConnectorImportOpenAPITool",
    "AgentGetSettingsTool",
    "AgentUpdateSettingsTool",
    "AgentListConnectorsTool",
    "AgentAddConnectorTool",
    "AgentRemoveConnectorTool",
    "AgentSetModelTool",
    "discover_builtin_tools",
]

# Mapping from tool class to the keyword argument name for sandbox paths.
# All tools share a single "workspace" directory so files created by one
# tool (e.g. python_exec) are visible to others (e.g. file_ops, shell_exec).
_SANDBOX_KWARGS: dict[type, str] = {
    FileOpsTool: "workspace_dir",
    NodeExecTool: "exec_dir",
    ShellExecTool: "sandbox_dir",
    PythonExecTool: "exec_dir",
}

# Mapping from tool class to the keyword argument name for uploads paths.
# When uploads_root is provided, these tools receive a per-conversation
# output directory so their files are isolated and cleaned up with the conversation.
_UPLOADS_KWARGS: dict[type, str] = {
    GenerateImageTool: "output_dir",
}

# Tools that receive an artifacts_dir for storing rich output files.
_ARTIFACTS_KWARGS: dict[type, str] = {
    TemplateRenderTool: "artifacts_dir",
    PythonExecTool: "artifacts_dir",
    NodeExecTool: "artifacts_dir",
    ShellExecTool: "artifacts_dir",
    GenerateImageTool: "artifacts_dir",
    FileOpsTool: "artifacts_dir",
}

# Tools that require explicit configuration and should NOT be auto-discovered.
# They are registered manually when the appropriate config is available.
_SKIP_AUTO_DISCOVER: set[type] = {
    GroundedRetrieveTool,  # requires kb_ids — registered by _resolve_tools()
    EmailSendTool,         # requires SMTP_HOST/SMTP_USER/SMTP_PASS — registered below
    ConnectorListActionsTool,   # builder tools — injected by chat.py for builder agents
    ConnectorCreateActionTool,
    ConnectorUpdateActionTool,
    ConnectorDeleteActionTool,
    ConnectorUpdateSettingsTool,
    ConnectorTestActionTool,
    ConnectorGetSettingsTool,
    ConnectorTestConnectionTool,
    ConnectorImportOpenAPITool,
    AgentGetSettingsTool,    # agent builder tools — injected by chat.py for agent builder agents
    AgentUpdateSettingsTool,
    AgentListConnectorsTool,
    AgentAddConnectorTool,
    AgentRemoveConnectorTool,
    AgentSetModelTool,
}


_SANDBOX_EXEC_TOOLS: set[type] = {PythonExecTool, NodeExecTool, ShellExecTool}


def discover_builtin_tools(
    *,
    sandbox_root: Path | None = None,
    sandbox_config: dict | None = None,
    uploads_root: Path | None = None,
) -> list[Tool]:
    """Auto-discover and instantiate all built-in tools.

    Scans every module in this package, finds concrete ``BaseTool`` subclasses,
    and returns a fresh instance of each.

    When *sandbox_root* is provided (a per-conversation directory), all
    sandboxed tools share a single ``workspace/`` directory so that files
    created by one tool (e.g. python_exec) are visible to others
    (e.g. file_ops, shell_exec)::

        sandbox_root/
        └── workspace/   → FileOpsTool + ShellExecTool + PythonExecTool + NodeExecTool

    When *sandbox_config* is provided (from the agent's ``sandbox_config``
    JSON column), resource limits are passed to exec tools::

        {"memory": "512m", "cpu": 1.0, "timeout": 60}

    Tools that do not accept a sandbox parameter are instantiated with their
    zero-arg constructor as before.

    Tools that fail to import or instantiate are logged and skipped.
    """
    # Extract per-agent resource overrides from sandbox_config.
    _memory: str | None = sandbox_config.get("memory") if sandbox_config else None
    _cpu: float | None = sandbox_config.get("cpu") if sandbox_config else None
    _timeout_override: int | None = sandbox_config.get("timeout") if sandbox_config else None

    # All sandboxed tools share a single workspace directory so files are
    # visible across tools (e.g. python_exec output readable by file_ops).
    sandbox_paths: dict[str, Path] = {}
    if sandbox_root is not None:
        shared = sandbox_root / "workspace"
        sandbox_paths = {
            "workspace_dir": shared,
            "sandbox_dir": shared,
            "exec_dir": shared,
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
                and not obj.__name__.startswith("_")
                and not inspect.isabstract(obj)
                and obj not in _SKIP_AUTO_DISCOVER
            ):
                try:
                    kwargs: dict = {}
                    kwarg_name = _SANDBOX_KWARGS.get(obj)
                    if kwarg_name and kwarg_name in sandbox_paths:
                        kwargs[kwarg_name] = sandbox_paths[kwarg_name]
                    uploads_kwarg = _UPLOADS_KWARGS.get(obj)
                    if uploads_kwarg and uploads_root is not None:
                        kwargs[uploads_kwarg] = uploads_root
                    artifacts_kwarg = _ARTIFACTS_KWARGS.get(obj)
                    if artifacts_kwarg and uploads_root is not None:
                        kwargs[artifacts_kwarg] = uploads_root / "artifacts"
                    if obj in _SANDBOX_EXEC_TOOLS:
                        if _memory is not None:
                            kwargs["memory"] = _memory
                        if _cpu is not None:
                            kwargs["cpu"] = _cpu
                        if _timeout_override is not None:
                            kwargs["timeout"] = _timeout_override
                    tools.append(obj(**kwargs))
                except Exception:
                    logger.warning(
                        "Failed to instantiate tool %s.%s",
                        fqn,
                        obj.__name__,
                        exc_info=True,
                    )
    # Conditionally register tools that require external configuration.
    # Insert at position 0 so they appear first within their category in the UI.
    import os
    if os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASS"):
        tools.insert(0, EmailSendTool())

    return tools
