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

# ---------------------------------------------------------------------------
# Template 4 — HTTP API Pipeline
# ---------------------------------------------------------------------------

_HTTP_PIPELINE: dict[str, Any] = {
    "id": "http-api-pipeline",
    "name": "HTTP API Pipeline",
    "description": (
        "Call an external HTTP API and transform the response with a template"
    ),
    "icon": "Globe",
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
                                "name": "url",
                                "type": "string",
                                "required": True,
                                "description": "Target URL to call",
                            },
                            {
                                "name": "method",
                                "type": "string",
                                "required": False,
                                "description": "HTTP method (GET, POST, etc.)",
                                "default": "GET",
                            },
                        ]
                    },
                },
                x=100,
                y=200,
            ),
            _node(
                "http_1",
                "HTTP_REQUEST",
                {
                    "label": "HTTP Request",
                    "url": "{{input.url}}",
                    "method": "{{input.method}}",
                    "headers": {},
                    "body": "",
                },
                x=400,
                y=200,
            ),
            _node(
                "transform_1",
                "TEMPLATE_TRANSFORM",
                {
                    "label": "Format Response",
                    "template": (
                        "HTTP Response (status {{http_1.status_code}}):\n\n"
                        "{{http_1.output}}"
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
                                "value": "{{transform_1.output}}",
                            }
                        ]
                    },
                },
                x=1000,
                y=200,
            ),
        ],
        "edges": [
            _edge("e-start-http", "start_1", "http_1"),
            _edge("e-http-transform", "http_1", "transform_1"),
            _edge("e-transform-end", "transform_1", "end_1"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}

# ---------------------------------------------------------------------------
# Template 5 — Data Processing Pipeline
# ---------------------------------------------------------------------------

_DATA_PIPELINE: dict[str, Any] = {
    "id": "data-processing-pipeline",
    "name": "Data Processing Pipeline",
    "description": "Process input data with code, transform with a template, and output results.",
    "icon": "Database",
    "category": "data",
    "blueprint": {
        "nodes": [
            _node(
                "start_1",
                "START",
                {
                    "label": "Start",
                    "variables": [
                        {"name": "raw_data", "type": "string", "required": True},
                    ],
                },
                x=0,
                y=200,
            ),
            _node(
                "code_1",
                "CODE_EXECUTION",
                {
                    "label": "Process Data",
                    "language": "python",
                    "code": (
                        "import json\n\n"
                        "# Parse the raw data\n"
                        "data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data\n\n"
                        "# Process: extract, transform, filter as needed\n"
                        "result = {\n"
                        '    "processed": data,\n'
                        '    "count": len(data) if isinstance(data, (list, dict)) else 1\n'
                        "}\n"
                    ),
                    "output_variable": "processed",
                },
                x=300,
                y=200,
            ),
            _node(
                "transform_1",
                "TEMPLATE_TRANSFORM",
                {
                    "label": "Format Output",
                    "template": (
                        "Processing complete.\n\n"
                        "Result: {{code_1.output}}\n"
                    ),
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
                        "result": "{{transform_1.output}}",
                    },
                },
                x=900,
                y=200,
            ),
        ],
        "edges": [
            _edge("e-start-code", "start_1", "code_1"),
            _edge("e-code-transform", "code_1", "transform_1"),
            _edge("e-transform-end", "transform_1", "end_1"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}


# ---------------------------------------------------------------------------
# Template 6 — Agent with Knowledge Retrieval
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Template 7 — List Processing with Transform
# ---------------------------------------------------------------------------

_LIST_TRANSFORM_PIPELINE: dict[str, Any] = {
    "id": "list-transform-pipeline",
    "name": "List Processing Pipeline",
    "description": "Filter and transform a list of items, then format the output",
    "icon": "ListFilter",
    "category": "data",
    "blueprint": {
        "nodes": [
            _node(
                "start_1",
                "START",
                {
                    "label": "Start",
                    "variables": [
                        {"name": "items", "type": "string", "required": True,
                         "description": "JSON array of items to process"},
                    ],
                },
                x=0,
                y=200,
            ),
            _node(
                "filter_1",
                "LIST_OPERATION",
                {
                    "label": "Filter Items",
                    "input_variable": "{{input.items}}",
                    "operation": "filter",
                    "expression": "item is not None",
                    "output_variable": "filtered",
                },
                x=300,
                y=200,
            ),
            _node(
                "transform_1",
                "TRANSFORM",
                {
                    "label": "Transform Data",
                    "input_variable": "{{filter_1.filtered}}",
                    "operations": [
                        {"type": "type_cast", "config": {"target_type": "json"}},
                        {"type": "format", "config": {"template": "Processed {value}"}},
                    ],
                    "output_variable": "transformed",
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
                        "result": "{{transform_1.transformed}}",
                    },
                },
                x=900,
                y=200,
            ),
        ],
        "edges": [
            _edge("e-start-filter", "start_1", "filter_1"),
            _edge("e-filter-transform", "filter_1", "transform_1"),
            _edge("e-transform-end", "transform_1", "end_1"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}

# ---------------------------------------------------------------------------
# Template 8 — Question Understanding + LLM
# ---------------------------------------------------------------------------

_QUESTION_ENHANCED_QA: dict[str, Any] = {
    "id": "question-enhanced-qa",
    "name": "Enhanced Question Answering",
    "description": "Preprocess user questions for clarity, then answer with an LLM",
    "icon": "MessageCircleQuestion",
    "category": "ai",
    "blueprint": {
        "nodes": [
            _node(
                "start_1",
                "START",
                {
                    "label": "Start",
                    "variables": [
                        {"name": "question", "type": "string", "required": True,
                         "description": "User question (may be messy or unclear)"},
                    ],
                },
                x=0,
                y=200,
            ),
            _node(
                "qu_1",
                "QUESTION_UNDERSTANDING",
                {
                    "label": "Clarify Question",
                    "input_variable": "{{input.question}}",
                    "mode": "rewrite",
                    "output_variable": "clear_question",
                },
                x=300,
                y=200,
            ),
            _node(
                "llm_1",
                "LLM",
                {
                    "label": "Answer",
                    "prompt_template": (
                        "Answer the following question thoroughly and accurately.\n\n"
                        "Question: {{qu_1.clear_question}}"
                    ),
                    "model": "",
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
                        "answer": "{{llm_1.output}}",
                        "original_question": "{{input.question}}",
                        "clarified_question": "{{qu_1.clear_question}}",
                    },
                },
                x=900,
                y=200,
            ),
        ],
        "edges": [
            _edge("e-start-qu", "start_1", "qu_1"),
            _edge("e-qu-llm", "qu_1", "llm_1"),
            _edge("e-llm-end", "llm_1", "end_1"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}


# ---------------------------------------------------------------------------
# Template 9 — Human Approval Pipeline
# ---------------------------------------------------------------------------

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
# Template 10 — Multi-Step Data Processing
# ---------------------------------------------------------------------------

_MULTI_STEP_DATA_PROCESSING: dict[str, Any] = {
    "id": "multi-step-data-processing",
    "name": "Multi-Step Data Processing",
    "description": (
        "Validate, transform, and process data through a multi-step "
        "pipeline with error handling."
    ),
    "icon": "Layers",
    "category": "data",
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
                                "name": "raw_data",
                                "type": "string",
                                "required": True,
                                "description": "Raw data to validate and process",
                            }
                        ]
                    },
                },
                x=0,
                y=250,
            ),
            _node(
                "code_1",
                "CODE_EXECUTION",
                {
                    "label": "Validate Data",
                    "language": "python",
                    "code": (
                        "import json\n\n"
                        "try:\n"
                        "    data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data\n"
                        "    is_valid = isinstance(data, (list, dict)) and len(data) > 0\n"
                        "except Exception:\n"
                        "    is_valid = False\n"
                        "    data = None\n\n"
                        "result = {'is_valid': is_valid, 'data': data}\n"
                    ),
                    "output_variable": "validation",
                },
                x=300,
                y=250,
            ),
            _node(
                "cond_1",
                "CONDITION_BRANCH",
                {
                    "label": "Is Valid?",
                    "conditions": [
                        {
                            "id": "yes",
                            "handle": "yes",
                            "variable": "{{code_1.validation.is_valid}}",
                            "operator": "==",
                            "value": "True",
                        }
                    ],
                    "default_handle": "no",
                },
                x=600,
                y=250,
            ),
            _node(
                "transform_1",
                "TRANSFORM",
                {
                    "label": "Transform Data",
                    "input_variable": "{{code_1.validation.data}}",
                    "operations": [
                        {"type": "type_cast", "config": {"target_type": "json"}},
                    ],
                    "output_variable": "transformed",
                },
                x=900,
                y=100,
            ),
            _node(
                "list_1",
                "LIST_OPERATION",
                {
                    "label": "Process Items",
                    "input_variable": "{{transform_1.transformed}}",
                    "operation": "map",
                    "expression": "str(item)",
                    "output_variable": "processed",
                },
                x=1200,
                y=100,
            ),
            _node(
                "end_success",
                "END",
                {
                    "label": "Success",
                    "output_schema": {
                        "variables": [
                            {
                                "name": "result",
                                "type": "string",
                                "value": "{{list_1.processed}}",
                            }
                        ]
                    },
                },
                x=1500,
                y=100,
            ),
            _node(
                "end_error",
                "END",
                {
                    "label": "Error",
                    "output_schema": {
                        "variables": [
                            {
                                "name": "result",
                                "type": "string",
                                "value": "Validation failed: input data is invalid.",
                            }
                        ]
                    },
                },
                x=900,
                y=400,
            ),
        ],
        "edges": [
            _edge("e-start-code", "start_1", "code_1"),
            _edge("e-code-cond", "code_1", "cond_1"),
            _edge("e-cond-transform", "cond_1", "transform_1", source_handle="yes"),
            _edge("e-transform-list", "transform_1", "list_1"),
            _edge("e-list-end", "list_1", "end_success"),
            _edge("e-cond-error", "cond_1", "end_error", source_handle="no"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}

# ---------------------------------------------------------------------------
# Template 11 — Knowledge-Enhanced Agent
# ---------------------------------------------------------------------------

_KNOWLEDGE_ENHANCED_AGENT: dict[str, Any] = {
    "id": "knowledge-enhanced-agent",
    "name": "Knowledge-Enhanced Agent",
    "description": (
        "Enhance user queries, retrieve knowledge, and generate informed responses."
    ),
    "icon": "BrainCircuit",
    "category": "ai",
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
                                "name": "user_query",
                                "type": "string",
                                "required": True,
                                "description": "User query to answer with knowledge",
                            }
                        ]
                    },
                },
                x=0,
                y=200,
            ),
            _node(
                "qu_1",
                "QUESTION_UNDERSTANDING",
                {
                    "label": "Rewrite Query",
                    "input_variable": "{{input.user_query}}",
                    "mode": "rewrite",
                    "output_variable": "enhanced_query",
                },
                x=300,
                y=200,
            ),
            _node(
                "kb_1",
                "KNOWLEDGE_RETRIEVAL",
                {
                    "label": "Knowledge Retrieval",
                    "knowledge_base_id": "",
                    "query": "{{qu_1.enhanced_query}}",
                    "top_k": 5,
                },
                x=600,
                y=200,
            ),
            _node(
                "llm_1",
                "LLM",
                {
                    "label": "Answer with Context",
                    "prompt_template": (
                        "You are a knowledgeable assistant. Use the following "
                        "retrieved context to answer the user's question accurately. "
                        "If the context is insufficient, say so clearly.\n\n"
                        "Context:\n{{kb_1.output}}\n\n"
                        "Original question: {{input.user_query}}\n"
                        "Enhanced question: {{qu_1.enhanced_query}}"
                    ),
                    "model": "",
                },
                x=900,
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
                x=1200,
                y=200,
            ),
        ],
        "edges": [
            _edge("e-start-qu", "start_1", "qu_1"),
            _edge("e-qu-kb", "qu_1", "kb_1"),
            _edge("e-kb-llm", "kb_1", "llm_1"),
            _edge("e-llm-end", "llm_1", "end_1"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}

# ---------------------------------------------------------------------------
# Template 12 — Iterative Review Loop
# ---------------------------------------------------------------------------

_ITERATIVE_REVIEW_LOOP: dict[str, Any] = {
    "id": "iterative-review-loop",
    "name": "Iterative Review Loop",
    "description": (
        "Repeatedly review and improve text using LLM feedback "
        "up to a maximum number of iterations."
    ),
    "icon": "RefreshCcw",
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
                                "name": "draft_text",
                                "type": "string",
                                "required": True,
                                "description": "Initial draft text to review and improve",
                            }
                        ]
                    },
                },
                x=0,
                y=200,
            ),
            _node(
                "loop_1",
                "LOOP",
                {
                    "label": "Review Loop",
                    "condition": "{{review_count}} < 3",
                    "max_iterations": 3,
                    "body_nodes": ["llm_review", "va_increment"],
                },
                x=300,
                y=200,
            ),
            _node(
                "llm_review",
                "LLM",
                {
                    "label": "Review & Improve",
                    "prompt_template": (
                        "You are an expert editor. Review the following text and "
                        "improve it for clarity, grammar, and style. Return "
                        "only the improved version.\n\n"
                        "Text:\n{{input.draft_text}}"
                    ),
                    "model": "",
                },
                x=600,
                y=200,
            ),
            _node(
                "va_increment",
                "VARIABLE_ASSIGN",
                {
                    "label": "Increment Counter",
                    "assignments": [
                        {"variable": "review_count", "expression": "review_count + 1"},
                    ],
                },
                x=900,
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
                                "value": "{{llm_review.output}}",
                            }
                        ]
                    },
                },
                x=1200,
                y=200,
            ),
        ],
        "edges": [
            _edge("e-start-loop", "start_1", "loop_1"),
            _edge("e-loop-llm", "loop_1", "llm_review"),
            _edge("e-llm-va", "llm_review", "va_increment"),
            _edge("e-va-end", "va_increment", "end_1"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}


# ---------------------------------------------------------------------------
# Template 13 — Secure API Integration (ENV + HTTP)
# ---------------------------------------------------------------------------

_SECURE_API_INTEGRATION: dict[str, Any] = {
    "id": "secure-api-integration",
    "name": "Secure API Integration",
    "description": "Read API keys from encrypted env storage, then call an external API with authentication",
    "icon": "KeyRound",
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
                                "name": "endpoint",
                                "type": "string",
                                "required": True,
                                "description": "API endpoint path",
                            },
                            {
                                "name": "payload",
                                "type": "string",
                                "required": False,
                                "default": "{}",
                                "description": "Request body JSON",
                            },
                        ]
                    },
                },
                x=100, y=200,
            ),
            _node(
                "env_creds",
                "ENV",
                {
                    "label": "Load Credentials",
                    "env_keys": ["API_KEY", "API_BASE_URL"],
                    "output_variable": "credentials",
                },
                x=350, y=200,
            ),
            _node(
                "http_call",
                "HTTP_REQUEST",
                {
                    "label": "Call External API",
                    "method": "POST",
                    "url": "{{env_creds.API_BASE_URL}}{{start.endpoint}}",
                    "headers": {
                        "Authorization": "Bearer {{env_creds.API_KEY}}",
                        "Content-Type": "application/json",
                    },
                    "body": "{{start.payload}}",
                    "output_variable": "api_response",
                },
                x=600, y=200,
            ),
            _node(
                "cond_check",
                "CONDITION_BRANCH",
                {
                    "label": "Check Response",
                    "mode": "expression",
                    "conditions": [
                        {
                            "id": "success",
                            "label": "Success",
                            "expression": "api_response.status_code == 200",
                        },
                    ],
                },
                x=850, y=200,
            ),
            _node(
                "end_success",
                "END",
                {
                    "label": "Success",
                    "output_mapping": {
                        "response": "{{http_call.api_response}}",
                        "status": "success",
                    },
                },
                x=1100, y=100,
            ),
            _node(
                "end_error",
                "END",
                {
                    "label": "Error",
                    "output_mapping": {
                        "error": "{{http_call.api_response}}",
                        "status": "error",
                    },
                },
                x=1100, y=300,
            ),
        ],
        "edges": [
            _edge("e-start-env", "start_1", "env_creds"),
            _edge("e-env-http", "env_creds", "http_call"),
            _edge("e-http-cond", "http_call", "cond_check"),
            _edge("e-cond-success", "cond_check", "end_success", source_handle="success"),
            _edge("e-cond-error", "cond_check", "end_error", source_handle="default"),
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    },
}


# ---------------------------------------------------------------------------
# Template 14 — Sub-Workflow Orchestration
# ---------------------------------------------------------------------------

_SUB_WORKFLOW_ORCHESTRATION: dict[str, Any] = {
    "id": "sub-workflow-orchestration",
    "name": "Sub-Workflow Orchestration",
    "description": "Process input through an LLM, then delegate to a sub-workflow for specialized handling",
    "icon": "GitBranch",
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
                                "name": "task_description",
                                "type": "string",
                                "required": True,
                                "description": "What needs to be done",
                            },
                        ]
                    },
                },
                x=100, y=200,
            ),
            _node(
                "llm_classify",
                "LLM",
                {
                    "label": "Analyze Task",
                    "prompt_template": "Analyze this task and extract the key parameters:\n\n{{start.task_description}}\n\nReturn a JSON object with: category, priority, and summary.",
                    "output_variable": "analysis",
                    "temperature": 0.3,
                },
                x=350, y=200,
            ),
            _node(
                "sub_process",
                "SUB_WORKFLOW",
                {
                    "label": "Delegate to Handler",
                    "workflow_id": "",
                    "input_mapping": {
                        "task": "{{start.task_description}}",
                        "analysis": "{{llm_classify.analysis}}",
                    },
                    "output_variable": "handler_result",
                },
                x=600, y=200,
            ),
            _node(
                "llm_summarize",
                "LLM",
                {
                    "label": "Summarize Result",
                    "prompt_template": "Summarize the processing result:\n\nOriginal task: {{start.task_description}}\nHandler output: {{sub_process.handler_result}}\n\nProvide a brief status update.",
                    "output_variable": "summary",
                    "temperature": 0.5,
                },
                x=850, y=200,
            ),
            _node(
                "end_1",
                "END",
                {
                    "label": "Done",
                    "output_mapping": {
                        "summary": "{{llm_summarize.summary}}",
                        "details": "{{sub_process.handler_result}}",
                    },
                },
                x=1100, y=200,
            ),
        ],
        "edges": [
            _edge("e-start-llm", "start_1", "llm_classify"),
            _edge("e-llm-sub", "llm_classify", "sub_process"),
            _edge("e-sub-sum", "sub_process", "llm_summarize"),
            _edge("e-sum-end", "llm_summarize", "end_1"),
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
    _HTTP_PIPELINE,
    _DATA_PIPELINE,
    _AGENT_WITH_KB,
    _LIST_TRANSFORM_PIPELINE,
    _QUESTION_ENHANCED_QA,
    _HUMAN_APPROVAL_PIPELINE,
    _MULTI_STEP_DATA_PROCESSING,
    _KNOWLEDGE_ENHANCED_AGENT,
    _ITERATIVE_REVIEW_LOOP,
    _SECURE_API_INTEGRATION,
    _SUB_WORKFLOW_ORCHESTRATION,
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
