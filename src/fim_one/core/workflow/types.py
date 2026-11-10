"""Workflow blueprint type definitions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from fim_one.core.assembly.principal import CallContext


class NodeType(str, Enum):
    """All supported workflow node types."""

    START = "START"
    END = "END"
    LLM = "LLM"
    CONDITION_BRANCH = "CONDITION_BRANCH"
    AGENT = "AGENT"
    KNOWLEDGE_RETRIEVAL = "KNOWLEDGE_RETRIEVAL"
    CONNECTOR = "CONNECTOR"
    HUMAN_INTERVENTION = "HUMAN_INTERVENTION"
    MCP = "MCP"


class NodeStatus(str, Enum):
    """Execution status of a single node."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    # Asked a human for a decision; the run goes on once it is given.
    WAITING = "waiting"


class ErrorStrategy(str, Enum):
    """Per-node error handling strategy (inspired by Dify's workflow engine).

    - STOP_WORKFLOW: default — a failure stops the entire workflow.
    - CONTINUE: the node is marked FAILED but treated as "completed" for
      dependency resolution so downstream nodes still run.
    - FAIL_BRANCH: the node is marked FAILED and all downstream nodes
      reachable *only* through this node are SKIPPED.
    """

    STOP_WORKFLOW = "stop_workflow"
    CONTINUE = "continue"
    FAIL_BRANCH = "fail_branch"


# Default per-node timeout when a node sets no ``timeout_ms`` of its own.  An
# Agent step runs a multi-iteration ReAct loop with tool calls and an LLM step
# can stream a long completion, so a flat 30s cut most real runs short.
DEFAULT_NODE_TIMEOUT_MS = 30_000
_NODE_TIMEOUT_DEFAULTS_MS: dict[NodeType, int] = {
    NodeType.AGENT: 300_000,
    NodeType.LLM: 120_000,
    NodeType.MCP: 60_000,
}


def default_timeout_ms(node_type: NodeType) -> int:
    """Return the timeout a node of *node_type* gets when it sets none."""
    return _NODE_TIMEOUT_DEFAULTS_MS.get(node_type, DEFAULT_NODE_TIMEOUT_MS)


@dataclass
class WorkflowNodeDef:
    """Definition of a single node in the blueprint."""

    id: str
    type: NodeType
    data: dict[str, Any] = field(default_factory=dict)
    position: dict[str, float] = field(default_factory=dict)
    error_strategy: ErrorStrategy = ErrorStrategy.STOP_WORKFLOW
    timeout_ms: int = 30000  # Default 30 seconds per node


@dataclass
class WorkflowEdgeDef:
    """Definition of a connection between two nodes."""

    id: str
    source: str
    target: str
    source_handle: str | None = None
    target_handle: str | None = None


@dataclass
class WorkflowBlueprint:
    """Parsed blueprint ready for execution."""

    nodes: list[WorkflowNodeDef] = field(default_factory=list)
    edges: list[WorkflowEdgeDef] = field(default_factory=list)
    viewport: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeResult:
    """Result from executing a single node."""

    node_id: str
    status: NodeStatus
    output: Any = None
    error: str | None = None
    active_handles: list[str] | None = None  # For condition/classifier nodes
    duration_ms: int = 0
    input_preview: str | None = None  # JSON snapshot of inputs (truncated to 500 chars)
    # With status WAITING: what the step is waiting on, JSON-serialisable
    # (``confirmation_id``, ``kind``, ``expires_at``). The engine hands it
    # back in ``ExecutionContext.suspensions`` when the step is run again.
    suspension: dict[str, Any] | None = None
    # With status FAILED: the failure is a person's answer (a rejection, or
    # no answer in time), so the step's retries do not apply: asking again
    # is not retrying.
    no_retry: bool = False


@dataclass
class ExecutionContext:
    """Shared context available to all node executors during a run."""

    run_id: str
    user_id: str
    workflow_id: str
    env_vars: dict[str, str] = field(default_factory=dict)
    db_session_factory: Callable[[], Any] | None = None
    depth: int = 0
    max_run_duration: int | None = None  # seconds; None = use engine default
    cancel_event: asyncio.Event | None = None  # shared cancel signal
    # Shared across all nodes of a run: accumulates LLM/Agent token usage so the
    # run total can be billed to the workflow owner (see WorkflowRun.total_tokens).
    usage_tracker: Any = None  # core.model.usage.UsageTracker | None
    # Who the run executes as and the workflow as the binding its steps use
    # resources through (``core.assembly.principal.CallContext``); set by the
    # run service. ``None`` for a context built by hand: steps then act as
    # ``user_id`` directly, with no binding.
    call_context: CallContext | None = None
    # The run's own sandbox directory (``core.assembly.sandbox``): where the
    # shell, code-execution and file tools of its Agent steps work. Set by
    # the run service, which removes the directory when the run ends.
    # ``None`` for a context built by hand: its Agent steps get none of
    # those tools.
    sandbox_id: str | None = None
    # Steps that asked for a decision earlier in this run, by node id: the
    # ``NodeResult.suspension`` each returned. A step found here is being
    # run again to pick up the decision instead of asking a second time.
    suspensions: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Whether a step may stop the run to wait for a decision: only a run
    # with a record the run service can continue. Otherwise a step that
    # needs a decision fails (or, for an approval step with no database,
    # approves itself).
    can_suspend: bool = False
    # Asked before every step: is this run still this process's to
    # execute? False once someone else closed its record (cancelled from
    # another process, or taken for dead). The step is then not run.
    # ``None`` for a context built by hand: always ours.
    still_owned: Callable[[], Any] | None = None
