"""Workflow blueprint type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    """All supported workflow node types."""

    START = "START"
    END = "END"
    LLM = "LLM"
    CONDITION_BRANCH = "CONDITION_BRANCH"
    QUESTION_CLASSIFIER = "QUESTION_CLASSIFIER"
    AGENT = "AGENT"
    KNOWLEDGE_RETRIEVAL = "KNOWLEDGE_RETRIEVAL"
    CONNECTOR = "CONNECTOR"
    HTTP_REQUEST = "HTTP_REQUEST"
    VARIABLE_ASSIGN = "VARIABLE_ASSIGN"
    TEMPLATE_TRANSFORM = "TEMPLATE_TRANSFORM"
    CODE_EXECUTION = "CODE_EXECUTION"
    ITERATOR = "ITERATOR"
    VARIABLE_AGGREGATOR = "VARIABLE_AGGREGATOR"
    PARAMETER_EXTRACTOR = "PARAMETER_EXTRACTOR"
    LOOP = "LOOP"
    LIST_OPERATION = "LIST_OPERATION"
    TRANSFORM = "TRANSFORM"
    DOCUMENT_EXTRACTOR = "DOCUMENT_EXTRACTOR"
    QUESTION_UNDERSTANDING = "QUESTION_UNDERSTANDING"
    HUMAN_INTERVENTION = "HUMAN_INTERVENTION"
    MCP = "MCP"
    BUILTIN_TOOL = "BUILTIN_TOOL"
    SUB_WORKFLOW = "SUB_WORKFLOW"
    ENV = "ENV"


class NodeStatus(str, Enum):
    """Execution status of a single node."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


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


@dataclass
class ExecutionContext:
    """Shared context available to all node executors during a run."""

    run_id: str
    user_id: str
    workflow_id: str
    env_vars: dict[str, str] = field(default_factory=dict)
