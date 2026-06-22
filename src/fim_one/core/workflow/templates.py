"""Built-in workflow templates.

These are hardcoded blueprint definitions that serve as starting points for
users creating new workflows.  They are NOT stored in the database -- the API
returns them from memory and allows creating a real ``Workflow`` row from any
template via ``POST /api/workflows/from-template``.
"""

from __future__ import annotations

import copy
from typing import Any

# ---------------------------------------------------------------------------
# Helper — generate deterministic node IDs for templates
# ---------------------------------------------------------------------------


def _node(
    node_id: str,
    node_type: str,
    data: dict[str, Any],
    *,
    x: float = 0.0,
    y: float = 0.0,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "custom",
        "position": {"x": x, "y": y},
        "data": {"type": node_type, **data},
    }


def _edge(
    edge_id: str,
    source: str,
    target: str,
    *,
    source_handle: str | None = None,
    target_handle: str | None = None,
) -> dict[str, Any]:
    edge: dict[str, Any] = {
        "id": edge_id,
        "source": source,
        "target": target,
    }
    if source_handle is not None:
        edge["sourceHandle"] = source_handle
    if target_handle is not None:
        edge["targetHandle"] = target_handle
    return edge


# ---------------------------------------------------------------------------
# Template 1 — Simple LLM Chain
# ---------------------------------------------------------------------------

_SIMPLE_LLM_CHAIN: dict[str, Any] = {
    "id": "simple-llm-chain",
    "name": "Simple LLM Chain",
    "description": "Basic single-LLM workflow with input and output",
    "icon": "MessageSquare",
    "category": "basic",
    "blueprint": {
        "nodes": [
            _node(
                "start_1",
                "START",
                {
                    "label": "Start",
                    "input_schema": {
                        "variables": [
                            {
                                "name": "query",
                                "type": "string",
                                "required": True,
                                "description": "User question or prompt",
                            }
                        ]
                    },
                },
                x=100,
                y=200,
            ),
            _node(
                "llm_1",
                "LLM",
                {
                    "label": "LLM",
                    "prompt_template": (
                        "You are a helpful assistant. Answer the following "
                        "question:\n\n{{input.query}}"
                    ),
                    "model": "",
                },
                x=400,
                y=200,
            ),
            _node(
                "end_1",
                "END",
                {
                    "label": "End",
                    "output_schema": {
                        "variables": [
                            {
                                "name": "result",
                                "type": "string",
                                "value": "{{llm_1.output}}",
                            }
                        ]
                    },
                },
                x=700,
                y=200,
            ),
        ],
        "edges": [
            _edge("e-start-llm", "start_1", "llm_1"),
            _edge("e-llm-end", "llm_1", "end_1"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}

# ---------------------------------------------------------------------------
# Template 2 — Conditional Router
# ---------------------------------------------------------------------------

_CONDITIONAL_ROUTER: dict[str, Any] = {
    "id": "conditional-router",
    "name": "Conditional Router",
    "description": (
        "Route queries to different LLMs based on a category condition"
    ),
    "icon": "GitBranch",
    "category": "intermediate",
    "blueprint": {
        "nodes": [
            _node(
                "start_1",
                "START",
                {
                    "label": "Start",
                    "input_schema": {
                        "variables": [
                            {
                                "name": "query",
                                "type": "string",
                                "required": True,
                                "description": "User question",
                            },
                            {
                                "name": "category",
                                "type": "string",
                                "required": True,
                                "description": 'Category of the query (e.g. "technical", "general")',
                            },
                        ]
                    },
                },
                x=100,
                y=250,
            ),
            _node(
                "condition_1",
                "CONDITION_BRANCH",
                {
                    "label": "Is Technical?",
                    "conditions": [
                        {
                            "id": "cond-yes",
                            "handle": "yes",
                            "operator": "==",
                            "variable": "{{input.category}}",
                            "value": "technical",
                        }
                    ],
                    "default_handle": "no",
                },
                x=400,
                y=250,
            ),
            _node(
                "llm_technical",
                "LLM",
                {
                    "label": "Technical Assistant",
                    "prompt_template": (
                        "You are an expert technical assistant specializing in "
                        "software engineering and IT. Provide a detailed, "
                        "accurate technical answer.\n\nQuestion: {{input.query}}"
                    ),
                    "model": "",
                },
                x=700,
                y=100,
            ),
            _node(
                "llm_general",
                "LLM",
                {
                    "label": "General Assistant",
                    "prompt_template": (
                        "You are a friendly general-purpose assistant. "
                        "Answer clearly and concisely.\n\nQuestion: {{input.query}}"
                    ),
                    "model": "",
                },
                x=700,
                y=400,
            ),
            _node(
                "end_1",
                "END",
                {
                    "label": "End",
                    "output_schema": {
                        "variables": [
                            {
                                "name": "result",
                                "type": "string",
                                "value": "{{llm_technical.output}}{{llm_general.output}}",
                            }
                        ]
                    },
                },
                x=1000,
                y=250,
            ),
        ],
        "edges": [
            _edge("e-start-cond", "start_1", "condition_1"),
            _edge(
                "e-cond-tech",
                "condition_1",
                "llm_technical",
                source_handle="yes",
            ),
            _edge(
                "e-cond-general",
                "condition_1",
                "llm_general",
                source_handle="no",
            ),
            _edge("e-tech-end", "llm_technical", "end_1"),
            _edge("e-general-end", "llm_general", "end_1"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}

# ---------------------------------------------------------------------------
# Template 3 — Knowledge-Augmented QA
# ---------------------------------------------------------------------------

_KNOWLEDGE_QA: dict[str, Any] = {
    "id": "knowledge-augmented-qa",
    "name": "Knowledge-Augmented QA",
    "description": (
        "Retrieve context from a knowledge base and answer with an LLM"
    ),
    "icon": "BookOpen",
    "category": "advanced",
    "blueprint": {
        "nodes": [
            _node(
                "start_1",
                "START",
                {
                    "label": "Start",
                    "input_schema": {
                        "variables": [
                            {
                                "name": "query",
                                "type": "string",
                                "required": True,
                                "description": "User question to answer",
                            }
                        ]
                    },
                },
                x=100,
                y=200,
            ),
            _node(
                "kb_1",
                "KNOWLEDGE_RETRIEVAL",
                {
                    "label": "Knowledge Retrieval",
                    "knowledge_base_id": "",
                    "query": "{{input.query}}",
                    "top_k": 5,
                },
                x=400,
                y=200,
            ),
            _node(
                "llm_1",
                "LLM",
                {
                    "label": "Answer with Context",
                    "prompt_template": (
                        "You are a knowledgeable assistant. Use the following "
                        "context to answer the user's question. If the context "
                        "does not contain enough information, say so.\n\n"
                        "Context:\n{{kb_1.output}}\n\n"
                        "Question: {{input.query}}"
                    ),
                    "model": "",
                },
                x=700,
                y=200,
            ),
            _node(
                "end_1",
                "END",
                {
                    "label": "End",
                    "output_schema": {
                        "variables": [
                            {
                                "name": "result",
                                "type": "string",
                                "value": "{{llm_1.output}}",
                            }
                        ]
                    },
                },
                x=1000,
                y=200,
            ),
        ],
        "edges": [
            _edge("e-start-kb", "start_1", "kb_1"),
            _edge("e-kb-llm", "kb_1", "llm_1"),
            _edge("e-llm-end", "llm_1", "end_1"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}
_AGENT_WITH_KB: dict[str, Any] = {
    "id": "agent-with-knowledge",
    "name": "Agent with Knowledge Retrieval",
    "description": "Retrieve relevant context from a knowledge base, then delegate to an AI agent for intelligent processing.",
    "icon": "Bot",
    "category": "ai",
    "blueprint": {
        "nodes": [
            _node(
                "start_1",
                "START",
                {
                    "label": "Start",
                    "variables": [
                        {"name": "question", "type": "string", "required": True},
                    ],
                },
                x=0,
                y=200,
            ),
            _node(
                "kb_1",
                "KNOWLEDGE_RETRIEVAL",
                {
                    "label": "Retrieve Context",
                    "query_template": "{{start_1.question}}",
                    "top_k": 5,
                },
                x=300,
                y=200,
            ),
            _node(
                "agent_1",
                "AGENT",
                {
                    "label": "AI Agent",
                    "prompt": (
                        "Answer the following question using the provided context.\n\n"
                        "Context:\n{{kb_1.output}}\n\n"
                        "Question: {{start_1.question}}"
                    ),
                    "output_variable": "answer",
                },
                x=600,
                y=200,
            ),
            _node(
                "end_1",
                "END",
                {
                    "label": "End",
                    "output_mapping": {
                        "answer": "{{agent_1.output}}",
                    },
                },
                x=900,
                y=200,
            ),
        ],
        "edges": [
            _edge("e-start-kb", "start_1", "kb_1"),
            _edge("e-kb-agent", "kb_1", "agent_1"),
            _edge("e-agent-end", "agent_1", "end_1"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}
_HUMAN_APPROVAL_PIPELINE: dict[str, Any] = {
    "id": "human-approval-pipeline",
    "name": "Human Approval Pipeline",
    "description": (
        "LLM summarizes a request, then pauses for human review before "
        "routing based on approval."
    ),
    "icon": "UserCheck",
    "category": "advanced",
    "blueprint": {
        "nodes": [
            _node(
                "start_1",
                "START",
                {
                    "label": "Start",
                    "input_schema": {
                        "variables": [
                            {
                                "name": "request_text",
                                "type": "string",
                                "required": True,
                                "description": "The request to review and approve",
                            }
                        ]
                    },
                },
                x=0,
                y=250,
            ),
            _node(
                "llm_1",
                "LLM",
                {
                    "label": "Summarize Request",
                    "prompt_template": (
                        "Summarize the following request in 2-3 sentences, "
                        "highlighting key points that a reviewer should consider:"
                        "\n\n{{input.request_text}}"
                    ),
                    "model": "",
                },
                x=300,
                y=250,
            ),
            _node(
                "human_1",
                "HUMAN_INTERVENTION",
                {
                    "label": "Human Review",
                    "prompt_message": (
                        "Please review the following summarized request and approve or reject:"
                        "\n\n{{llm_1.output}}"
                    ),
                    "assignee": "",
                    "timeout_hours": 24,
                    "output_variable": "approval_result",
                },
                x=600,
                y=250,
            ),
            _node(
                "cond_1",
                "CONDITION_BRANCH",
                {
                    "label": "Check Approval",
                    "conditions": [
                        {
                            "id": "approved",
                            "handle": "approved",
                            "variable": "approval_result.status",
                            "operator": "==",
                            "value": "approved",
                        }
                    ],
                    "default_handle": "rejected",
                },
                x=900,
                y=250,
            ),
            _node(
                "end_approved",
                "END",
                {
                    "label": "Approved",
                    "output_schema": {
                        "variables": [
                            {
                                "name": "result",
                                "type": "string",
                                "value": "Request approved: {{llm_1.output}}",
                            }
                        ]
                    },
                },
                x=1200,
                y=100,
            ),
            _node(
                "end_rejected",
                "END",
                {
                    "label": "Rejected",
                    "output_schema": {
                        "variables": [
                            {
                                "name": "result",
                                "type": "string",
                                "value": "Request rejected.",
                            }
                        ]
                    },
                },
                x=1200,
                y=400,
            ),
        ],
        "edges": [
            _edge("e-start-llm", "start_1", "llm_1"),
            _edge("e-llm-human", "llm_1", "human_1"),
            _edge("e-human-cond", "human_1", "cond_1"),
            _edge(
                "e-cond-approved",
                "cond_1",
                "end_approved",
                source_handle="approved",
            ),
            _edge(
                "e-cond-rejected",
                "cond_1",
                "end_rejected",
                source_handle="rejected",
            ),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}


# ---------------------------------------------------------------------------
# Template 6 — Connector Action
# ---------------------------------------------------------------------------

_CONNECTOR_ACTION: dict[str, Any] = {
    "id": "connector-action",
    "name": "Connector Action",
    "description": (
        "Call an external system through a connector, then summarize the "
        "result with an LLM. Pick the connector and action after creating."
    ),
    "icon": "Plug",
    "category": "integration",
    "blueprint": {
        "nodes": [
            _node(
                "start_1",
                "START",
                {
                    "label": "Start",
                    "input_schema": {
                        "variables": [
                            {
                                "name": "query",
                                "type": "string",
                                "required": True,
                                "description": "Input passed to the connector action",
                            }
                        ]
                    },
                },
                x=100,
                y=200,
            ),
            _node(
                "connector_1",
                "CONNECTOR",
                {
                    "label": "Connector Action",
                    "connector_id": "",
                    "action": "",
                    "parameters": {},
                    "output_variable": "connector_result",
                },
                x=400,
                y=200,
            ),
            _node(
                "llm_1",
                "LLM",
                {
                    "label": "Summarize Result",
                    "prompt_template": (
                        "Summarize the following result from an external system "
                        "in clear, concise language for the user.\n\n"
                        "Result:\n{{connector_1.output}}"
                    ),
                },
                x=700,
                y=200,
            ),
            _node(
                "end_1",
                "END",
                {
                    "label": "End",
                    "output_schema": {
                        "variables": [
                            {
                                "name": "result",
                                "type": "string",
                                "value": "{{llm_1.output}}",
                            }
                        ]
                    },
                },
                x=1000,
                y=200,
            ),
        ],
        "edges": [
            _edge("e-start-connector", "start_1", "connector_1"),
            _edge("e-connector-llm", "connector_1", "llm_1"),
            _edge("e-llm-end", "llm_1", "end_1"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}


# ---------------------------------------------------------------------------
# Template 7 — MCP Tool Call
# ---------------------------------------------------------------------------

_MCP_TOOL_CALL: dict[str, Any] = {
    "id": "mcp-tool-call",
    "name": "MCP Tool Call",
    "description": (
        "Invoke a tool on an MCP server, then format the output with an LLM. "
        "Pick the server and tool after creating."
    ),
    "icon": "Cable",
    "category": "integration",
    "blueprint": {
        "nodes": [
            _node(
                "start_1",
                "START",
                {
                    "label": "Start",
                    "input_schema": {
                        "variables": [
                            {
                                "name": "query",
                                "type": "string",
                                "required": True,
                                "description": "Input passed to the MCP tool",
                            }
                        ]
                    },
                },
                x=100,
                y=200,
            ),
            _node(
                "mcp_1",
                "MCP",
                {
                    "label": "MCP Tool",
                    "server_id": "",
                    "tool_name": "",
                    "parameters": {},
                    "output_variable": "mcp_result",
                },
                x=400,
                y=200,
            ),
            _node(
                "llm_1",
                "LLM",
                {
                    "label": "Format Output",
                    "prompt_template": (
                        "Turn the following tool output into a clear, "
                        "user-facing answer.\n\n"
                        "Tool output:\n{{mcp_1.output}}"
                    ),
                },
                x=700,
                y=200,
            ),
            _node(
                "end_1",
                "END",
                {
                    "label": "End",
                    "output_schema": {
                        "variables": [
                            {
                                "name": "result",
                                "type": "string",
                                "value": "{{llm_1.output}}",
                            }
                        ]
                    },
                },
                x=1000,
                y=200,
            ),
        ],
        "edges": [
            _edge("e-start-mcp", "start_1", "mcp_1"),
            _edge("e-mcp-llm", "mcp_1", "llm_1"),
            _edge("e-llm-end", "llm_1", "end_1"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

WORKFLOW_TEMPLATES: list[dict[str, Any]] = [
    _SIMPLE_LLM_CHAIN,
    _CONDITIONAL_ROUTER,
    _KNOWLEDGE_QA,
    _AGENT_WITH_KB,
    _HUMAN_APPROVAL_PIPELINE,
    _CONNECTOR_ACTION,
    _MCP_TOOL_CALL,
]

_TEMPLATES_BY_ID: dict[str, dict[str, Any]] = {t["id"]: t for t in WORKFLOW_TEMPLATES}


def get_template(template_id: str) -> dict[str, Any] | None:
    """Return a deep copy of the template with the given ID, or None."""
    tpl = _TEMPLATES_BY_ID.get(template_id)
    if tpl is None:
        return None
    return copy.deepcopy(tpl)


def list_templates() -> list[dict[str, Any]]:
    """Return deep copies of all built-in templates."""
    return copy.deepcopy(WORKFLOW_TEMPLATES)
