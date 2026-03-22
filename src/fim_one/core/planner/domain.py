"""Unified domain detection for high-accuracy specialist tasks.

Single source of truth for determining whether a query belongs to a
specialist domain (legal, medical, financial, etc.).  Used by:

- ReAct endpoint: model escalation, domain instructions
- DAG endpoint: planner guidance for model_hint and step structure
- DAG executor: citation verification trigger

Uses LLM-based classification via ``classify_domain()``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fim_one.core.model.base import BaseLLM
from fim_one.core.model.structured import structured_llm_call
from fim_one.core.model.types import ChatMessage

logger = logging.getLogger(__name__)

# Specialist domains that trigger domain-aware features.
# Configurable via env var (comma-separated).
ESCALATION_DOMAINS: list[str] = [
    d.strip()
    for d in os.getenv(
        "ESCALATION_DOMAINS",
        "legal,medical,financial,tax,compliance,patent",
    ).split(",")
    if d.strip()
]

# Human-readable descriptions for the LLM classification prompt.
_DOMAIN_DESCRIPTIONS: dict[str, str] = {
    "legal": "laws, regulations, compliance, trademarks, contracts, litigation",
    "medical": "health, drugs, clinical trials, diagnosis, medical devices",
    "financial": "securities, accounting, audit, financial regulations",
    "tax": "tax law, tax codes, VAT, income tax, transfer pricing",
    "compliance": "GDPR, SOX, data protection, industry regulations, certifications",
    "patent": "patent law, claims, prior art, IP prosecution, utility models",
}


def _build_domain_prompt_block() -> str:
    """Build the domain_hint section of the classification prompt."""
    lines = []
    for domain in ESCALATION_DOMAINS:
        desc = _DOMAIN_DESCRIPTIONS.get(domain, domain)
        lines.append(f'- "{domain}": {desc}')
    lines.append("- null: general purpose, no specialist domain")
    return "\n".join(lines)


def _build_domain_enum() -> list[str | None]:
    """Build the JSON schema enum for domain_hint."""
    return [*ESCALATION_DOMAINS, None]


_CLASSIFICATION_PROMPT = """\
You are a domain classifier for an AI agent system.

Given a user query, determine which specialist domain it belongs to (if any).

**domain_hint** — the specialist domain (or null for general):
{domain_block}

## Rules
- Only classify as a specialist domain when the query CLEARLY requires \
domain expertise (e.g. legal analysis, medical diagnosis, financial audit).
- General knowledge questions that happen to mention a domain topic should \
be classified as null. For example, "what is a trademark?" is general, but \
"evaluate the legal risks of using a competitor's brand name" is legal.
- When in doubt, return null.

## Query
{{query}}

Respond with JSON: {{{{"domain_hint": one of {domain_names_str} or null}}}}
""".format(
    domain_block=_build_domain_prompt_block(),
    domain_names_str="/".join(f'"{d}"' for d in ESCALATION_DOMAINS),
)

_CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "domain_hint": {
            "type": ["string", "null"],
            "enum": _build_domain_enum(),
        },
    },
    "required": ["domain_hint"],
}


async def classify_domain(
    query: str,
    llm: BaseLLM,
) -> str | None:
    """Classify a user query into a specialist domain using LLM.

    This is the preferred detection method — more accurate than keyword
    matching for nuanced queries.

    Args:
        query: The user query (truncated to 2000 chars internally).
        llm: The LLM to use for classification (typically the fast model).

    Returns:
        One of :data:`ESCALATION_DOMAINS` or ``None`` for general queries.
    """
    truncated = query[:2000]
    prompt = _CLASSIFICATION_PROMPT.format(query=truncated)

    try:
        call_result = await structured_llm_call(
            llm=llm,
            messages=[ChatMessage(role="user", content=prompt)],
            schema=_CLASSIFICATION_SCHEMA,
            function_name="classify_domain",
            default_value={"domain_hint": None},
            temperature=0.0,
        )

        data = call_result.value
        raw = data.get("domain_hint") if isinstance(data, dict) else None
        valid = set(ESCALATION_DOMAINS)
        result = raw if raw in valid else None

        if result:
            logger.info("Domain classification: %s (query: %.80s…)", result, truncated)
        return result
    except Exception as exc:
        logger.warning("Domain classification failed, defaulting to None: %s", exc)
        return None
