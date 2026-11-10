"""Workflow blueprint v2: the one contract between the canvas and the engine.

A v2 blueprint is ``{schema_version: 2, nodes, edges, viewport, variables?}``.
Each node is ``{id, type, position, data}`` where ``type`` is the editor's
React Flow key (``start``, ``llm``, ``conditionBranch`` ...) and ``data`` is
validated by that type's model below. The engine (``parser.py``,
``nodes.py``) reads only these names.

Older blueprints spelled node types three ways (``conditionBranch``,
``CONDITION_BRANCH``, a ``data.type`` with ``custom`` on the node) and used
field aliases (``prompt``, ``query``, ``kb_id``, ``knowledge_base_id``,
``action``). :func:`upgrade_blueprint` rewrites those into v2. It is used
by the one-time data migration and at the API boundary (save, import,
templates); nothing downstream accepts the old spellings.

The TypeScript types in ``frontend/src/types/workflow-blueprint.generated.ts``
are generated from these models by ``scripts/gen_workflow_types.py``.
"""

from __future__ import annotations

import copy
import re
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .types import NodeType

SCHEMA_VERSION: Final = 2

# v2 node type name (the editor's React Flow key) for every engine node type.
NODE_TYPE_NAMES: dict[NodeType, str] = {
    NodeType.START: "start",
    NodeType.END: "end",
    NodeType.LLM: "llm",
    NodeType.CONDITION_BRANCH: "conditionBranch",
    NodeType.AGENT: "agent",
    NodeType.KNOWLEDGE_RETRIEVAL: "knowledgeRetrieval",
    NodeType.CONNECTOR: "connector",
    NodeType.HUMAN_INTERVENTION: "humanIntervention",
    NodeType.MCP: "mcp",
}
NODE_TYPES_BY_NAME: dict[str, NodeType] = {v: k for k, v in NODE_TYPE_NAMES.items()}


def node_type_of(raw_node: Any) -> NodeType | None:
    """The engine type of a v2 node dict, or ``None`` if it is not one."""
    if not isinstance(raw_node, dict):
        return None
    name = raw_node.get("type")
    return NODE_TYPES_BY_NAME.get(name) if isinstance(name, str) else None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class NodeDataBase(BaseModel):
    """Fields every node may carry. Unknown keys (editor-only state such as
    ``note`` or ``collapsed``) are kept as they are."""

    model_config = ConfigDict(extra="allow")

    label: str | None = None
    output_variable: str | None = None
    error_strategy: Literal["stop_workflow", "continue", "fail_branch"] = "stop_workflow"
    timeout_ms: int | None = Field(default=None, gt=0)
    retry_count: int = Field(default=0, ge=0)
    retry_delay_ms: int = Field(default=1000, ge=0)


class StartVariable(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    type: str = "string"
    default_value: Any = None
    required: bool = False
    description: str | None = None


class StartNodeData(NodeDataBase):
    variables: list[StartVariable] = Field(default_factory=list)
    input_schema: dict[str, Any] | None = None


class EndNodeData(NodeDataBase):
    output_mapping: dict[str, str] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None


class LLMNodeData(NodeDataBase):
    prompt_template: str = ""
    system_prompt: str = ""
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, gt=0)


class BranchCondition(BaseModel):
    """One branch. Its edge leaves the node on handle ``condition-{id}``."""

    model_config = ConfigDict(extra="allow")

    id: str
    label: str = ""
    expression: str | None = None
    variable: str | None = None
    operator: str | None = None
    value: Any = None
    llm_prompt: str | None = None


class ConditionBranchNodeData(NodeDataBase):
    mode: Literal["expression", "llm"] = "expression"
    conditions: list[BranchCondition] = Field(default_factory=list)
    default_handle: str = "source-default"
    default_branch_label: str | None = None
    llm_prompt: str | None = None


class AgentNodeData(NodeDataBase):
    agent_id: str = ""
    prompt_template: str = ""


class KnowledgeRetrievalNodeData(NodeDataBase):
    kb_ids: list[str] = Field(default_factory=list)
    query_template: str = ""
    top_k: int = Field(default=5, gt=0)


class ConnectorNodeData(NodeDataBase):
    """``action_id`` is the id of one of an API connector's actions. For a
    database connector it names the operation: ``list_tables``,
    ``describe_table`` (``parameters.table_name``) or ``query``
    (``parameters.sql``)."""

    connector_id: str = ""
    action_id: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class HumanInterventionNodeData(NodeDataBase):
    title: str = "Approval required"
    description: str = ""
    prompt_message: str = ""
    assignee: str = ""
    timeout_hours: float = Field(default=24.0, gt=0)
    poll_interval_seconds: float = Field(default=5.0, gt=0)


class MCPNodeData(NodeDataBase):
    server_id: str = ""
    tool_name: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class Position(BaseModel):
    model_config = ConfigDict(extra="allow")

    x: float = 0.0
    y: float = 0.0


class _NodeBase(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    position: Position = Field(default_factory=Position)


class StartNode(_NodeBase):
    type: Literal["start"]
    data: StartNodeData = Field(default_factory=StartNodeData)


class EndNode(_NodeBase):
    type: Literal["end"]
    data: EndNodeData = Field(default_factory=EndNodeData)


class LLMNode(_NodeBase):
    type: Literal["llm"]
    data: LLMNodeData = Field(default_factory=LLMNodeData)


class ConditionBranchNode(_NodeBase):
    type: Literal["conditionBranch"]
    data: ConditionBranchNodeData = Field(default_factory=ConditionBranchNodeData)


class AgentNode(_NodeBase):
    type: Literal["agent"]
    data: AgentNodeData = Field(default_factory=AgentNodeData)


class KnowledgeRetrievalNode(_NodeBase):
    type: Literal["knowledgeRetrieval"]
    data: KnowledgeRetrievalNodeData = Field(default_factory=KnowledgeRetrievalNodeData)


class ConnectorNode(_NodeBase):
    type: Literal["connector"]
    data: ConnectorNodeData = Field(default_factory=ConnectorNodeData)


class HumanInterventionNode(_NodeBase):
    type: Literal["humanIntervention"]
    data: HumanInterventionNodeData = Field(default_factory=HumanInterventionNodeData)


class MCPNode(_NodeBase):
    type: Literal["mcp"]
    data: MCPNodeData = Field(default_factory=MCPNodeData)


WorkflowNodeV2 = Annotated[
    StartNode
    | EndNode
    | LLMNode
    | ConditionBranchNode
    | AgentNode
    | KnowledgeRetrievalNode
    | ConnectorNode
    | HumanInterventionNode
    | MCPNode,
    Field(discriminator="type"),
]

# Node model for every v2 type name; used by tests and the type generator.
NODE_MODELS: dict[str, type[_NodeBase]] = {
    "start": StartNode,
    "end": EndNode,
    "llm": LLMNode,
    "conditionBranch": ConditionBranchNode,
    "agent": AgentNode,
    "knowledgeRetrieval": KnowledgeRetrievalNode,
    "connector": ConnectorNode,
    "humanIntervention": HumanInterventionNode,
    "mcp": MCPNode,
}


class WorkflowEdgeV2(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    sourceHandle: str | None = None  # noqa: N815 — React Flow's key
    targetHandle: str | None = None  # noqa: N815 — React Flow's key


class Viewport(BaseModel):
    model_config = ConfigDict(extra="allow")

    x: float = 0.0
    y: float = 0.0
    zoom: float = 1.0


class BlueprintV2(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal[2] = SCHEMA_VERSION
    nodes: list[WorkflowNodeV2] = Field(default_factory=list)
    edges: list[WorkflowEdgeV2] = Field(default_factory=list)
    viewport: Viewport = Field(default_factory=Viewport)
    variables: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class BlueprintSchemaError(ValueError):
    """A blueprint does not match the v2 schema.

    ``errors`` lists ``{"loc": "nodes.2.data.top_k", "node_id": "kb_1",
    "message": ...}`` entries, one per invalid field.
    """

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("; ".join(f"{e['loc']}: {e['message']}" for e in errors))
        self.errors = errors


def validate_blueprint_v2(raw: dict[str, Any]) -> BlueprintV2:
    """Validate a v2 blueprint dict; raise :class:`BlueprintSchemaError`."""
    try:
        return BlueprintV2.model_validate(raw)
    except ValidationError as exc:
        raise BlueprintSchemaError(_field_errors(raw, exc)) from None


def _field_errors(raw: dict[str, Any], exc: ValidationError) -> list[dict[str, Any]]:
    nodes = raw.get("nodes") if isinstance(raw, dict) else None
    errors: list[dict[str, Any]] = []
    for err in exc.errors():
        loc = list(err["loc"])
        # Drop the discriminator tag pydantic inserts after the list index.
        if len(loc) > 2 and loc[0] == "nodes" and loc[2] in NODE_MODELS:
            loc.pop(2)
        node_id: str | None = None
        index = loc[1] if len(loc) > 1 and loc[0] == "nodes" else None
        if (
            isinstance(index, int)
            and isinstance(nodes, list)
            and index < len(nodes)
            and isinstance(nodes[index], dict)
        ):
            node_id = nodes[index].get("id")
        errors.append(
            {
                "loc": ".".join(str(part) for part in loc),
                "node_id": node_id,
                "message": err["msg"],
            }
        )
    return errors


def normalize_for_save(raw: dict[str, Any]) -> dict[str, Any]:
    """Upgrade *raw* to v2, validate it, return the dict to store.

    Raises :class:`BlueprintSchemaError` with field-level errors.
    """
    upgraded = upgrade_blueprint(raw)
    validate_blueprint_v2(upgraded)
    return upgraded


# ---------------------------------------------------------------------------
# Upgrade (v1 spellings -> v2). Frozen: the data migration depends on it.
# ---------------------------------------------------------------------------

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Field aliases older blueprints used, per node type: alias -> v2 name.
_ALIASES: dict[NodeType, dict[str, str]] = {
    NodeType.LLM: {"prompt": "prompt_template"},
    NodeType.AGENT: {"query": "prompt_template", "prompt": "prompt_template"},
    NodeType.KNOWLEDGE_RETRIEVAL: {"query": "query_template"},
    NodeType.CONNECTOR: {"action": "action_id"},
}
_ERROR_STRATEGIES = {"stop_workflow", "continue", "fail_branch"}


def _legacy_type(raw: Any) -> NodeType | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    if raw in NODE_TYPES_BY_NAME:
        return NODE_TYPES_BY_NAME[raw]
    normalized = (
        _CAMEL_BOUNDARY.sub("_", raw.strip()).upper().replace("-", "_").replace(" ", "_")
    )
    try:
        return NodeType(normalized)
    except ValueError:
        return None


def _upgrade_node(node: dict[str, Any]) -> dict[str, Any]:
    data = node.get("data")
    data = dict(data) if isinstance(data, dict) else {}
    node_type = _legacy_type(data.get("type")) or _legacy_type(node.get("type"))
    if node_type is None:
        return node  # left for validation to report
    data.pop("type", None)

    for alias, name in _ALIASES.get(node_type, {}).items():
        if alias in data:
            value = data.pop(alias)
            if not data.get(name):
                data[name] = value

    if node_type == NodeType.KNOWLEDGE_RETRIEVAL:
        kb_ids = data.get("kb_ids", [])
        # A non-list kb_ids is left as it is, for validation to report.
        if isinstance(kb_ids, list):
            kb_ids = list(kb_ids)
            for alias in ("kb_id", "knowledge_base_id"):
                single = data.pop(alias, None)
                if isinstance(single, str) and single and single not in kb_ids:
                    kb_ids.append(single)
            data["kb_ids"] = kb_ids

    strategy = data.get("error_strategy")
    if strategy is not None:
        norm = str(strategy).strip().lower().replace("-", "_")
        if norm in _ERROR_STRATEGIES:
            data["error_strategy"] = norm
        else:
            data.pop("error_strategy")

    if "timeout_ms" in data:
        try:
            timeout = int(data["timeout_ms"])
        except (TypeError, ValueError):
            timeout = 0
        if timeout > 0:
            data["timeout_ms"] = timeout
        else:
            data.pop("timeout_ms")

    out = {**node, "type": NODE_TYPE_NAMES[node_type], "data": data}
    out.setdefault("position", {"x": 0, "y": 0})
    return out


def _upgrade_edge(edge: dict[str, Any]) -> dict[str, Any]:
    out = dict(edge)
    for snake, camel in (("source_handle", "sourceHandle"), ("target_handle", "targetHandle")):
        if snake in out:
            value = out.pop(snake)
            if out.get(camel) is None:
                out[camel] = value
    if not out.get("id"):
        sh = out.get("sourceHandle") or ""
        th = out.get("targetHandle") or ""
        out["id"] = f"{out.get('source', '')}:{sh}->{out.get('target', '')}:{th}"
    return out


def upgrade_blueprint(raw: Any) -> dict[str, Any]:
    """Rewrite any older blueprint spelling into v2. Idempotent; never raises.

    Nodes whose type cannot be recognised are left untouched so that
    validation reports them.
    """
    if not isinstance(raw, dict):
        return {"schema_version": SCHEMA_VERSION, "nodes": [], "edges": [], "viewport": {}}
    bp = copy.deepcopy(raw)
    nodes = bp.get("nodes")
    edges = bp.get("edges")
    bp["nodes"] = [
        _upgrade_node(n) if isinstance(n, dict) else n
        for n in (nodes if isinstance(nodes, list) else [])
    ]
    bp["edges"] = [
        _upgrade_edge(e) if isinstance(e, dict) else e
        for e in (edges if isinstance(edges, list) else [])
    ]
    if not isinstance(bp.get("viewport"), dict):
        bp["viewport"] = {"x": 0, "y": 0, "zoom": 1}
    bp["schema_version"] = SCHEMA_VERSION
    return bp


def is_v2(raw: Any) -> bool:
    return isinstance(raw, dict) and raw.get("schema_version") == SCHEMA_VERSION
