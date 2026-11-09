"""Node types an instance can switch off.

Human Intervention (approval) nodes wait by polling inside the server
process: a restart loses the wait, and the wait counts against the run's
time limit. Until approvals are reworked into a durable pause, an instance
accepts the node only when ``WORKFLOW_HUMAN_INTERVENTION_ENABLED`` is set.

With the switch off:

- a blueprint containing the node is refused wherever one is written
  (:func:`disabled_node_errors` gives the field-level errors);
- workflows stored earlier still load, so their author can remove the node;
- a run of such a workflow is refused before it starts
  (:func:`has_disabled_node`).
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from .blueprint_v2 import node_type_of
from .types import NodeType, WorkflowNodeDef

ENV_HUMAN_INTERVENTION = "WORKFLOW_HUMAN_INTERVENTION_ENABLED"

#: Error code of both the save-time and the run-start refusal.
HUMAN_INTERVENTION_DISABLED = "human_intervention_disabled"

HUMAN_INTERVENTION_DISABLED_DETAIL = (
    "This workflow contains a Human Intervention step, which is switched off "
    "on this instance. Remove the step, or ask an administrator to enable it."
)


def human_intervention_enabled() -> bool:
    """Whether this instance accepts Human Intervention nodes (default: no)."""
    return os.environ.get(ENV_HUMAN_INTERVENTION, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def disabled_node_errors(blueprint: Any) -> list[dict[str, Any]]:
    """Field-level errors for switched-off nodes in a v2 blueprint dict.

    Same entry shape as ``BlueprintSchemaError.errors`` (``loc``,
    ``node_id``, ``message``) plus the node's ``label``. Empty when every
    node type in the blueprint is enabled.
    """
    if human_intervention_enabled() or not isinstance(blueprint, dict):
        return []
    nodes = blueprint.get("nodes")
    errors: list[dict[str, Any]] = []
    for index, node in enumerate(nodes if isinstance(nodes, list) else []):
        if node_type_of(node) != NodeType.HUMAN_INTERVENTION:
            continue
        data = node.get("data")
        label = (data.get("label") if isinstance(data, dict) else None) or node.get("id")
        errors.append(
            {
                "loc": f"nodes.{index}.type",
                "node_id": node.get("id"),
                "label": label,
                "message": (
                    f"Human Intervention step '{label}' is switched off on this instance"
                ),
            }
        )
    return errors


def has_disabled_node(nodes: Iterable[WorkflowNodeDef]) -> bool:
    """Whether a parsed blueprint contains a switched-off node."""
    if human_intervention_enabled():
        return False
    return any(node.type == NodeType.HUMAN_INTERVENTION for node in nodes)
