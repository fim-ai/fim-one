"""Workflow blueprint execution engine.

This package implements a visual workflow system where users define static
execution blueprints (nodes + edges).  The ``WorkflowEngine`` executes the
blueprint as a DAG, running nodes concurrently where possible and supporting
conditional branching.
"""

from .engine import WorkflowEngine
from .import_resolver import ImportResolution, resolve_blueprint_references
from .parser import BlueprintValidationError, parse_blueprint, topological_sort
from .scheduler import WorkflowScheduler
from .types import (
    ErrorStrategy,
    ExecutionContext,
    NodeResult,
    NodeStatus,
    NodeType,
    WorkflowBlueprint,
    WorkflowEdgeDef,
    WorkflowNodeDef,
)
from .variable_store import VariableStore

__all__ = [
    "BlueprintValidationError",
    "ErrorStrategy",
    "ExecutionContext",
    "ImportResolution",
    "NodeResult",
    "NodeStatus",
    "NodeType",
    "VariableStore",
    "WorkflowBlueprint",
    "WorkflowEdgeDef",
    "WorkflowEngine",
    "WorkflowNodeDef",
    "WorkflowScheduler",
    "parse_blueprint",
    "resolve_blueprint_references",
    "topological_sort",
]
