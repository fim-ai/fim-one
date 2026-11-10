"""The directory a run's shell, code-execution and file tools are confined to.

A chat turn's tools work in its conversation's directory. A run with no
conversation (a workflow run, a ``call_agent`` delegation made outside a
conversation, an evaluation run) gets a directory of its own, named by a
sandbox id from this module, and that directory is removed when the run
ends. A run that has neither gets none of :data:`SANDBOXED_TOOLS`: they
are never built on a directory two runs share.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from pathlib import Path

__all__ = [
    "SANDBOXED_TOOLS",
    "delegation_sandbox_id",
    "eval_run_sandbox_id",
    "is_run_sandbox",
    "release_sandbox",
    "sandbox_base",
    "sandbox_root",
    "workflow_run_sandbox_id",
]

# Built-in tools that read, write or execute inside a sandbox directory.
SANDBOXED_TOOLS = ("shell_exec", "python_exec", "node_exec", "file_ops")

# ``<repo>/data/sandbox``: the parent of every conversation's directory
# (``data/`` is the volume Docker-outside-of-Docker mounts into containers).
_BASE = Path(__file__).resolve().parents[4] / "data" / "sandbox"

_WORKFLOW_RUN = "wfrun-"
_DELEGATION = "subagent-"
_EVAL_RUN = "evalrun-"
_RUN_PREFIXES = (_WORKFLOW_RUN, _DELEGATION, _EVAL_RUN)

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def sandbox_base() -> Path:
    """The directory that holds one sandbox directory per conversation or run."""
    return _BASE


def workflow_run_sandbox_id(run_id: str) -> str:
    """The sandbox of one workflow run; every step of the run shares it."""
    return f"{_WORKFLOW_RUN}{run_id}"


def eval_run_sandbox_id(run_id: str) -> str:
    """The sandbox of one evaluation run."""
    return f"{_EVAL_RUN}{run_id}"


def delegation_sandbox_id() -> str:
    """A sandbox for one ``call_agent`` delegation made outside a conversation."""
    return f"{_DELEGATION}{uuid.uuid4()}"


def is_run_sandbox(sandbox_id: str | None) -> bool:
    """Whether *sandbox_id* names a run's own directory (not a conversation's)."""
    return bool(sandbox_id) and str(sandbox_id).startswith(_RUN_PREFIXES)


def sandbox_root(sandbox_id: str) -> Path:
    """The directory of *sandbox_id* (a conversation id or a run's sandbox id).

    Raises ``ValueError`` for an id that is not a single path segment.
    """
    if not _SEGMENT.fullmatch(sandbox_id):
        raise ValueError(f"Not a sandbox id: {sandbox_id!r}")
    return sandbox_base() / sandbox_id


async def release_sandbox(sandbox_id: str | None) -> None:
    """Remove a run's sandbox directory. Call it when the run ends, however it ends.

    Only a run's own directory is removed: a conversation's directory lives
    as long as the conversation. Never raises.
    """
    if not sandbox_id or not is_run_sandbox(sandbox_id):
        return
    try:
        root = sandbox_root(sandbox_id)
    except ValueError:
        return
    try:
        await asyncio.to_thread(shutil.rmtree, root, True)
    except asyncio.CancelledError:
        # Cancelled while removing: finish the removal, then let it through.
        shutil.rmtree(root, ignore_errors=True)
        raise
    except Exception:
        # The loop is shutting down, or the thread pool is gone.
        shutil.rmtree(root, ignore_errors=True)
