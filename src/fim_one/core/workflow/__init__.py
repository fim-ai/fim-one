"""Workflow blueprint execution engine.

This package implements a visual workflow system where users define static
execution blueprints (nodes + edges).  The ``WorkflowEngine`` executes the
blueprint as a DAG, running nodes concurrently where possible and supporting
conditional branching.
"""

from .engine import WorkflowEngine
from .parser import BlueprintValidationError, parse_blueprint, topological_sort
from .types import (
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
    "ExecutionContext",
    "NodeResult",
    "NodeStatus",
    "NodeType",
    "VariableStore",
    "WorkflowBlueprint",
    "WorkflowEdgeDef",
    "WorkflowEngine",
    "WorkflowNodeDef",
    "parse_blueprint",
    "topological_sort",
]
