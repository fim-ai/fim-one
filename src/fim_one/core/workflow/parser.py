"""Blueprint parser — JSON to dataclasses, validation, topological sort."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from .types import ErrorStrategy, NodeType, WorkflowBlueprint, WorkflowEdgeDef, WorkflowNodeDef


class BlueprintValidationError(ValueError):
    """Raised when a blueprint fails structural validation."""


@dataclass
class BlueprintWarning:
    """Non-fatal validation issue (blueprint can still be saved/executed)."""

    node_id: str | None
    code: str
    message: str


def _resolve_node_type(raw: str) -> NodeType:
    """Map a raw type string to a NodeType enum, case-insensitive."""
    normalized = raw.upper().replace("-", "_").replace(" ", "_")
    try:
        return NodeType(normalized)
    except ValueError:
        raise BlueprintValidationError(
            f"Unknown node type: '{raw}'. "
            f"Valid types: {[t.value for t in NodeType]}"
        )


def parse_blueprint(raw: dict[str, Any]) -> WorkflowBlueprint:
    """Parse a raw blueprint dict into a validated ``WorkflowBlueprint``.

    Performs structural validation:
    - Exactly 1 Start node
    - At least 1 End node
    - All edge references point to valid node IDs
    - No cycles in the graph

    Parameters
    ----------
    raw:
        The raw blueprint JSON (``{nodes, edges, viewport}``).

    Returns
    -------
    WorkflowBlueprint
        Parsed and validated blueprint ready for execution.

    Raises
    ------
    BlueprintValidationError
        If the blueprint fails validation.
    """
    raw_nodes = raw.get("nodes", [])
    raw_edges = raw.get("edges", [])
    viewport = raw.get("viewport", {})

    if not raw_nodes:
        raise BlueprintValidationError("Blueprint has no nodes")

    # Parse nodes
    nodes: list[WorkflowNodeDef] = []
    node_ids: set[str] = set()
    start_count = 0
    end_count = 0

    for n in raw_nodes:
        node_id = n.get("id", "")
        if not node_id:
            raise BlueprintValidationError("Node is missing 'id' field")
        if node_id in node_ids:
            raise BlueprintValidationError(f"Duplicate node ID: '{node_id}'")
        node_ids.add(node_id)

        # Type can be in data.type or directly on the node
        node_data = n.get("data", {}) or {}
        raw_type = node_data.get("type", "") or n.get("type", "")
        if not raw_type:
            raise BlueprintValidationError(
                f"Node '{node_id}' is missing type"
            )

        node_type = _resolve_node_type(raw_type)

        if node_type == NodeType.START:
            start_count += 1
        elif node_type == NodeType.END:
            end_count += 1

        # Parse optional error_strategy from node data (case-insensitive)
        raw_error_strategy = node_data.get("error_strategy", "")
        error_strategy = ErrorStrategy.STOP_WORKFLOW
        if raw_error_strategy:
            normalized_strategy = raw_error_strategy.lower().replace("-", "_")
            try:
                error_strategy = ErrorStrategy(normalized_strategy)
            except ValueError:
                pass  # fallback to default

        # Parse optional per-node timeout
        raw_timeout = node_data.get("timeout_ms")
        timeout_ms = 30000  # default
        if raw_timeout is not None:
            try:
                timeout_ms = int(raw_timeout)
                if timeout_ms <= 0:
                    timeout_ms = 30000
            except (TypeError, ValueError):
                pass

        nodes.append(
            WorkflowNodeDef(
                id=node_id,
                type=node_type,
                data=node_data,
                position=n.get("position", {}),
                error_strategy=error_strategy,
                timeout_ms=timeout_ms,
            )
        )

    if start_count == 0:
        raise BlueprintValidationError("Blueprint must have exactly 1 Start node")
    if start_count > 1:
        raise BlueprintValidationError(
            f"Blueprint must have exactly 1 Start node, found {start_count}"
        )
    if end_count == 0:
        raise BlueprintValidationError("Blueprint must have at least 1 End node")

    # Parse edges
    edges: list[WorkflowEdgeDef] = []
    for e in raw_edges:
        source = e.get("source", "")
        target = e.get("target", "")
        if not source or not target:
            raise BlueprintValidationError("Edge is missing 'source' or 'target'")
        if source not in node_ids:
            raise BlueprintValidationError(
                f"Edge source '{source}' references unknown node"
            )
        if target not in node_ids:
            raise BlueprintValidationError(
                f"Edge target '{target}' references unknown node"
            )

        edges.append(
            WorkflowEdgeDef(
                id=e.get("id", f"{source}->{target}"),
                source=source,
                target=target,
                source_handle=e.get("sourceHandle"),
                target_handle=e.get("targetHandle"),
            )
        )

    # Cycle detection via topological sort
    _check_no_cycles(nodes, edges)

    return WorkflowBlueprint(nodes=nodes, edges=edges, viewport=viewport)


def _check_no_cycles(
    nodes: list[WorkflowNodeDef], edges: list[WorkflowEdgeDef]
) -> None:
    """Kahn's algorithm for cycle detection."""
    in_degree: dict[str, int] = {n.id: 0 for n in nodes}
    adjacency: dict[str, list[str]] = defaultdict(list)

    for edge in edges:
        adjacency[edge.source].append(edge.target)
        in_degree[edge.target] = in_degree.get(edge.target, 0) + 1

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    visited_count = 0

    while queue:
        nid = queue.popleft()
        visited_count += 1
        for neighbor in adjacency.get(nid, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited_count != len(nodes):
        raise BlueprintValidationError(
            "Blueprint contains a cycle — workflows must be acyclic (DAG)"
        )


def topological_sort(blueprint: WorkflowBlueprint) -> list[str]:
    """Return node IDs in topological execution order.

    Parameters
    ----------
    blueprint:
        A validated workflow blueprint.

    Returns
    -------
    list[str]
        Node IDs ordered so that every node appears after all its predecessors.
    """
    in_degree: dict[str, int] = {n.id: 0 for n in blueprint.nodes}
    adjacency: dict[str, list[str]] = defaultdict(list)

    for edge in blueprint.edges:
        adjacency[edge.source].append(edge.target)
        in_degree[edge.target] = in_degree.get(edge.target, 0) + 1

    queue = deque(
        sorted(nid for nid, deg in in_degree.items() if deg == 0)
    )
    result: list[str] = []

    while queue:
        nid = queue.popleft()
        result.append(nid)
        for neighbor in sorted(adjacency.get(nid, [])):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return result


def validate_blueprint(blueprint: WorkflowBlueprint) -> list[BlueprintWarning]:
    """Return non-fatal warnings about a parsed blueprint.

    These don't prevent saving or execution but indicate potential problems:
    - Disconnected nodes (no incoming or outgoing edges)
    - End node unreachable from Start
    - Condition/classifier nodes with no conditions defined
    - Nodes with empty required fields
    """
    warnings: list[BlueprintWarning] = []
    node_index = {n.id: n for n in blueprint.nodes}

    # Build in/out edge sets
    has_incoming: set[str] = set()
    has_outgoing: set[str] = set()
    for edge in blueprint.edges:
        has_outgoing.add(edge.source)
        has_incoming.add(edge.target)

    # Check disconnected nodes
    for node in blueprint.nodes:
        if node.type == NodeType.START:
            if node.id not in has_outgoing:
                warnings.append(BlueprintWarning(
                    node_id=node.id,
                    code="start_no_outgoing",
                    message="Start node has no outgoing connections",
                ))
        elif node.type == NodeType.END:
            if node.id not in has_incoming:
                warnings.append(BlueprintWarning(
                    node_id=node.id,
                    code="end_no_incoming",
                    message="End node has no incoming connections",
                ))
        else:
            if node.id not in has_incoming and node.id not in has_outgoing:
                warnings.append(BlueprintWarning(
                    node_id=node.id,
                    code="disconnected_node",
                    message=f"Node '{node.id}' has no connections",
                ))

    # Check reachability: Start → End via BFS
    start_node = next((n for n in blueprint.nodes if n.type == NodeType.START), None)
    end_nodes = {n.id for n in blueprint.nodes if n.type == NodeType.END}
    if start_node and end_nodes:
        adjacency: dict[str, list[str]] = defaultdict(list)
        for edge in blueprint.edges:
            adjacency[edge.source].append(edge.target)

        # BFS from start
        reachable: set[str] = set()
        queue = deque([start_node.id])
        while queue:
            nid = queue.popleft()
            if nid in reachable:
                continue
            reachable.add(nid)
            for neighbor in adjacency.get(nid, []):
                queue.append(neighbor)

        unreachable_ends = end_nodes - reachable
        for end_id in unreachable_ends:
            warnings.append(BlueprintWarning(
                node_id=end_id,
                code="end_unreachable",
                message=f"End node '{end_id}' is not reachable from Start",
            ))

    # Node-specific checks
    for node in blueprint.nodes:
        if node.type == NodeType.CONDITION_BRANCH:
            conditions = node.data.get("conditions", [])
            if not conditions:
                warnings.append(BlueprintWarning(
                    node_id=node.id,
                    code="empty_conditions",
                    message="Condition branch has no conditions defined",
                ))
        elif node.type == NodeType.QUESTION_CLASSIFIER:
            classes = node.data.get("classes", []) or node.data.get("categories", [])
            if not classes:
                warnings.append(BlueprintWarning(
                    node_id=node.id,
                    code="empty_classes",
                    message="Question classifier has no classes defined",
                ))
        elif node.type == NodeType.LLM:
            prompt = node.data.get("prompt_template", "") or node.data.get("prompt", "")
            if not prompt:
                warnings.append(BlueprintWarning(
                    node_id=node.id,
                    code="empty_prompt",
                    message="LLM node has no prompt template",
                ))
        elif node.type == NodeType.CODE_EXECUTION:
            code = node.data.get("code", "")
            if not code:
                warnings.append(BlueprintWarning(
                    node_id=node.id,
                    code="empty_code",
                    message="Code execution node has no code",
                ))

    return warnings
