"""Post-execution step verification using LLM judgment.

After a DAG step completes, optionally verify that the result
actually satisfies the step's stated task.
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

# Max chars of step result sent to the verification prompt.
_VERIFY_TRUNCATION = int(os.getenv("DAG_VERIFY_TRUNCATION", "2000"))

_VERIFICATION_PROMPT = """\
You are a quality-assurance judge. A task was given to an AI agent and it \
produced a result. Determine whether the result adequately addresses the task.

## Task
{task}

## Result (truncated to {truncation_limit} chars)
{result}

Respond with JSON: {{"passed": true/false, "reason": "brief explanation"}}
"""

_VERIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["passed", "reason"],
}


@dataclass
class VerificationResult:
    """Outcome of a step verification check.

    Attributes:
        passed: Whether the step result adequately addressed the task.
        reason: Brief explanation of the verdict.
    """

    passed: bool
    reason: str


async def verify_step(
    task: str,
    result_summary: str,
    llm: BaseLLM,
) -> VerificationResult:
    """Verify a step's result against its task description.

    Uses :func:`structured_llm_call` with the 3-level degradation strategy
    to extract a structured verdict from the LLM.

    On parse error, defaults to ``passed=True`` (safe failure -- don't block
    on verification bugs).

    Args:
        task: The step's task description.
        result_summary: The step's output text.
        llm: The LLM to use for verification.

    Returns:
        A :class:`VerificationResult` with the verdict and reason.
    """
    truncated = result_summary[:_VERIFY_TRUNCATION]
    prompt = _VERIFICATION_PROMPT.format(
        task=task, result=truncated, truncation_limit=_VERIFY_TRUNCATION,
    )

    messages = [
        ChatMessage(role="user", content=prompt),
    ]

    try:
        call_result: StructuredCallResult[Any] = await structured_llm_call(
            llm,
            messages,
            schema=_VERIFICATION_SCHEMA,
            function_name="verify_step",
            default_value={"passed": True, "reason": "Verification parse fallback"},
            temperature=0.0,
        )
        data = call_result.value
        return VerificationResult(
            passed=bool(data.get("passed", True)),
            reason=str(data.get("reason", "")),
        )
    except Exception as exc:
        logger.warning("Step verification failed, defaulting to passed: %s", exc)
        return VerificationResult(passed=True, reason=f"Verification error: {exc}")
