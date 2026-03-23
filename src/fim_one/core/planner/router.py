"""Auto-routing classifier for execution mode selection.

Uses a fast LLM to classify whether a user query is better served by
ReAct (single-step, conversational) or DAG (multi-step, decomposable).

# Domain detection moved to domain.py — see classify_domain()
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from fim_one.core.model.base import BaseLLM
from fim_one.core.model.structured import StructuredCallResult, structured_llm_call
from fim_one.core.model.types import ChatMessage

logger = logging.getLogger(__name__)

_CLASSIFICATION_PROMPT = """\
You are an execution-mode classifier for an AI agent system.

Given a user query, decide:

1. **mode** — how to execute:
- "react": Simple, single-step, conversational, or exploratory queries. \
Queries that need back-and-forth or are open-ended.
- "dag": Complex, multi-step tasks that can be decomposed into independent \
subtasks with dependencies. Tasks that benefit from parallel execution.

## Examples
- "What is the weather?" -> react
- "Summarize this document" -> react
- "Find all customers who churned last month, analyze the reasons, and \
draft an email campaign" -> dag
- "Research competitor pricing in 5 markets, create comparison table" -> dag
- "Hello" -> react

## Query
{query}

Respond with JSON: {{"mode": "react" or "dag", "reasoning": "brief explanation"}}
"""

_CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["react", "dag"]},
        "reasoning": {"type": "string"},
    },
    "required": ["mode", "reasoning"],
}


@dataclass
class RouteDecision:
    """Result of the auto-routing classification.

    Attributes:
        mode: Either ``"react"`` or ``"dag"``.
        reasoning: Brief explanation from the classifier.
    """

    mode: str  # "react" or "dag"
    reasoning: str = ""


async def classify_execution_mode(
    query: str,
    llm: BaseLLM,
) -> RouteDecision:
    """Classify a user query into react or dag execution mode.

    Uses a structured LLM call to classify the query.  On any error,
    defaults to ``"react"`` (safe fallback -- ReAct can handle everything,
    just less efficiently for complex multi-step tasks).

    Args:
        query: The user query to classify (truncated to 2000 chars).
        llm: The LLM to use for classification (typically the fast model).

    Returns:
        A :class:`RouteDecision` with the selected mode and reasoning.
    """
    _query_limit = int(os.getenv("DAG_ROUTER_QUERY_TRUNCATION", "2000"))
    truncated_query = query[:_query_limit]
    prompt = _CLASSIFICATION_PROMPT.format(query=truncated_query)

    messages = [
        ChatMessage(role="user", content=prompt),
    ]

    try:
        call_result: StructuredCallResult[Any] = await structured_llm_call(
            llm=llm,
            messages=messages,
            schema=_CLASSIFICATION_SCHEMA,
            function_name="classify_mode",
            default_value={"mode": "react", "reasoning": "Classification fallback"},
            temperature=0.0,
        )

        data = call_result.value
        mode = data.get("mode", "react") if isinstance(data, dict) else "react"
        if mode not in ("react", "dag"):
            mode = "react"
        reasoning = data.get("reasoning", "") if isinstance(data, dict) else ""

        return RouteDecision(mode=mode, reasoning=reasoning)
    except Exception as exc:
        logger.warning(
            "Auto-routing classification failed, defaulting to react: %s", exc,
        )
        return RouteDecision(
            mode="react",
            reasoning=f"Classification error: {exc}",
        )
