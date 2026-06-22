"""Seed data for DB-stored workflow templates.

These templates are inserted into the ``workflow_templates`` table on first
run (or via a management command).  They complement the hardcoded built-in
templates in ``templates.py`` by providing admin-editable starting points.

Usage::

    from fim_one.core.workflow.template_seeds import TEMPLATE_SEEDS
    # Each entry is a dict ready to be passed to WorkflowTemplate(**seed).
"""

from __future__ import annotations

from typing import Any


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
# 1. Simple LLM Chain
# ---------------------------------------------------------------------------

_SIMPLE_LLM_CHAIN: dict[str, Any] = {
    "name": "Simple LLM Chain",
    "description": "A minimal workflow: accept user input, process it with an LLM, and return the result.",
    "icon": "MessageSquare",
    "category": "ai",
    "sort_order": 10,
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
                                "name": "prompt",
                                "type": "string",
                                "required": True,
                                "description": "User prompt to send to the LLM",
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
                    "prompt_template": "{{input.prompt}}",
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
                    "output_mapping": {
                        "result": "{{llm_1.output}}",
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
# 2. Conditional Router
# ---------------------------------------------------------------------------

_CONDITIONAL_ROUTER: dict[str, Any] = {
    "name": "Conditional Router",
    "description": "Route input through a condition branch to different LLM processors based on criteria.",
    "icon": "GitFork",
    "category": "automation",
    "sort_order": 20,
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
                                "name": "text",
                                "type": "string",
                                "required": True,
                                "description": "Text to classify and route",
                            },
                            {
                                "name": "category",
                                "type": "string",
                                "required": True,
                                "description": "Category: 'technical' or 'general'",
                            },
                        ]
                    },
                },
                x=100,
                y=250,
            ),
            _node(
                "cond_1",
                "CONDITION_BRANCH",
                {
                    "label": "Route by Category",
                    "mode": "expression",
                    "conditions": [
                        {
                            "id": "technical",
                            "label": "Technical",
                            "expression": "category == 'technical'",
                        },
                    ],
                    "default_handle": "general",
                },
                x=400,
                y=250,
            ),
            _node(
                "llm_tech",
                "LLM",
                {
                    "label": "Technical LLM",
                    "prompt_template": (
                        "You are a technical expert. Respond to this technical query:\n\n"
                        "{{input.text}}"
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
                    "label": "General LLM",
                    "prompt_template": (
                        "You are a helpful assistant. Respond to this query:\n\n"
                        "{{input.text}}"
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
                    "output_mapping": {
                        "result": "{{llm_tech.output}}{{llm_general.output}}",
                    },
                },
                x=1000,
                y=250,
            ),
        ],
        "edges": [
            _edge("e-start-cond", "start_1", "cond_1"),
            _edge("e-cond-tech", "cond_1", "llm_tech", source_handle="technical"),
            _edge("e-cond-general", "cond_1", "llm_general", source_handle="general"),
            _edge("e-tech-end", "llm_tech", "end_1"),
            _edge("e-general-end", "llm_general", "end_1"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}

# ---------------------------------------------------------------------------
# 3. Knowledge Q&A
# ---------------------------------------------------------------------------

_KNOWLEDGE_QA: dict[str, Any] = {
    "name": "Knowledge Q&A",
    "description": "Retrieve relevant context from a knowledge base and answer user questions using an LLM.",
    "icon": "BookOpen",
    "category": "ai",
    "sort_order": 30,
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
                    "query_template": "{{input.query}}",
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
                    "output_mapping": {
                        "result": "{{llm_1.output}}",
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

# ---------------------------------------------------------------------------
# 5. Agent Task
# ---------------------------------------------------------------------------

_AGENT_TASK: dict[str, Any] = {
    "name": "Agent Task",
    "description": "Delegate a task to an AI agent and collect the results.",
    "icon": "Bot",
    "category": "ai",
    "sort_order": 50,
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
                                "name": "task",
                                "type": "string",
                                "required": True,
                                "description": "Task description for the agent",
                            }
                        ]
                    },
                },
                x=100,
                y=200,
            ),
            _node(
                "agent_1",
                "AGENT",
                {
                    "label": "AI Agent",
                    "agent_id": "",
                    "prompt_template": "{{input.task}}",
                    "output_variable": "agent_result",
                },
                x=400,
                y=200,
            ),
            _node(
                "end_1",
                "END",
                {
                    "label": "End",
                    "output_mapping": {
                        "result": "{{agent_1.output}}",
                    },
                },
                x=700,
                y=200,
            ),
        ],
        "edges": [
            _edge("e-start-agent", "start_1", "agent_1"),
            _edge("e-agent-end", "agent_1", "end_1"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

TEMPLATE_SEEDS: list[dict[str, Any]] = [
    _SIMPLE_LLM_CHAIN,
    _CONDITIONAL_ROUTER,
    _KNOWLEDGE_QA,
    _AGENT_TASK,
]
