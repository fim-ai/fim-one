"""Plan analyzer that reflects on execution results.

The ``PlanAnalyzer`` evaluates whether an executed plan has achieved its
original goal, providing a confidence score and an optional synthesised
final answer.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from fim_agent.core.model import BaseLLM, ChatMessage
from fim_agent.core.model.structured import structured_llm_call
from fim_agent.core.model.usage import UsageSummary

from .types import AnalysisResult, ExecutionPlan

logger = logging.getLogger(__name__)

_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "achieved": {"type": "boolean"},
        "confidence": {"type": "number"},
        "final_answer": {"type": ["string", "null"]},
        "reasoning": {"type": "string"},
    },
    "required": ["achieved", "confidence", "reasoning"],
}

_ANALYSIS_PROMPT = """\
You are a plan evaluator.  Given a goal and the results of an execution \
plan that was designed to achieve that goal, you must assess whether the \
goal has been achieved.

Respond with a single JSON object:
{{
  "achieved": true or false,
  "confidence": <float between 0.0 and 1.0>,
  "final_answer": "<synthesised answer based on all step results, or null if not achieved>",
  "reasoning": "<explain your assessment>"
}}

Guidelines:
- Set "achieved" to true only if the goal has been fully accomplished.
- "confidence" should reflect how certain you are (1.0 = absolutely certain, \
0.0 = no confidence at all).
- If achieved, provide a clear "final_answer" that synthesises the step \
results into a coherent response to the original goal.
- If not achieved, set "final_answer" to null and explain in "reasoning" \
what is missing or went wrong.
- The "final_answer" should be a concise synthesis -- present the key \
results and conclusions, not a verbose repetition of each step's raw output.
- LANGUAGE: The "final_answer" and "reasoning" must be in the same language \
as the original goal. If the goal is in Chinese, respond in Chinese.
- When step results come from different sources (e.g. web search vs knowledge \
base retrieval), explicitly compare them. If the information is consistent, \
note that sources corroborate each other. If there are contradictions \
(different numbers, dates, or claims), flag each discrepancy clearly in the \
"final_answer" with both versions and indicate which source is likely more \
authoritative based on recency and specificity.
- Lower the "confidence" score when sources contradict each other.
"""


class PlanAnalyzer:
    """Evaluates whether an executed plan achieved its goal.

    Uses an LLM to reflect on the original goal and the collected step
    results, producing a structured assessment.

    Args:
        llm: The language model to use for analysis.
    """

    def __init__(self, llm: BaseLLM, *, language_directive: str | None = None) -> None:
        self._llm = llm
        self._language_directive = language_directive

    async def analyze(
        self,
        goal: str,
        plan: ExecutionPlan,
    ) -> AnalysisResult:
        """Analyze whether the executed plan achieved the goal.

        Args:
            goal: The original high-level objective.
            plan: The execution plan with populated step results.

        Returns:
            An ``AnalysisResult`` with the LLM's assessment.
        """
        messages = self._build_messages(goal, plan)

        call_result = await structured_llm_call(
            self._llm,
            messages,
            schema=_ANALYSIS_SCHEMA,
            function_name="analyze_plan",
            parse_fn=self._dict_to_analysis_result,
            regex_fallback=_regex_extract_analysis,
            default_value=AnalysisResult(
                achieved=False,
                confidence=0.0,
                reasoning="Could not parse analysis response",
            ),
        )

        analysis = call_result.value
        if call_result.total_usage:
            analysis.usage = UsageSummary(
                prompt_tokens=call_result.total_usage.get("prompt_tokens", 0),
                completion_tokens=call_result.total_usage.get("completion_tokens", 0),
                total_tokens=call_result.total_usage.get("total_tokens", 0),
                llm_calls=call_result.llm_calls,
            )

        return analysis

    async def stream_synthesize(
        self,
        goal: str,
        plan: ExecutionPlan,
        judgment: AnalysisResult,
    ) -> AsyncIterator[str]:
        """Stream the final synthesised answer token by token.

        Called after :meth:`analyze` when the goal was achieved and we want
        real token-level streaming for the frontend.

        Args:
            goal: The original high-level objective.
            plan: The executed plan with populated step results.
            judgment: The analysis result from :meth:`analyze`.

        Yields:
            Incremental text chunks (tokens) of the synthesised answer.
        """
        step_summaries = self._format_step_results(plan)

        system_parts = [
            "You synthesize a final answer from execution plan results. "
            "Provide a concise, coherent response that addresses the original "
            "goal. Do NOT include meta-commentary like 'based on the results' "
            "— just answer directly.",
            "",
            "Guidelines:",
            "- When step results come from different sources (e.g. web search vs "
            "knowledge base retrieval), explicitly compare them. If the "
            "information is consistent, note that sources corroborate each other. "
            "If there are contradictions (different numbers, dates, or claims), "
            "flag each discrepancy clearly with both versions and indicate which "
            "source is likely more authoritative based on recency and specificity.",
            "- LANGUAGE: The answer must be in the same language as the original "
            "goal. If the goal is in Chinese, respond in Chinese.",
        ]
        if self._language_directive:
            system_parts.append(f"- {self._language_directive}")

        system_content = "\n".join(system_parts)

        user_content = (
            f"Goal: {goal}\n\n"
            f"Execution plan (round {plan.current_round}):\n{step_summaries}\n\n"
            f"Analyzer reasoning: {judgment.reasoning}"
        )

        messages = [
            ChatMessage(role="system", content=system_content),
            ChatMessage(role="user", content=user_content),
        ]

        async for chunk in self._llm.stream_chat(messages):
            if chunk.delta_content:
                yield chunk.delta_content

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        goal: str,
        plan: ExecutionPlan,
    ) -> list[ChatMessage]:
        """Construct the message list for the analysis LLM call.

        Args:
            goal: The original objective.
            plan: The executed plan with step results.

        Returns:
            A list of ``ChatMessage`` objects.
        """
        step_summaries = self._format_step_results(plan)

        user_content = (
            f"Goal: {goal}\n\n"
            f"Execution plan (round {plan.current_round}):\n{step_summaries}"
        )

        system_content = _ANALYSIS_PROMPT
        if self._language_directive:
            system_content += f"\n\n{self._language_directive}"
        return [
            ChatMessage(role="system", content=system_content),
            ChatMessage(role="user", content=user_content),
        ]

    @staticmethod
    def _format_step_results(
        plan: ExecutionPlan,
        *,
        max_result_chars: int = 10_000,
    ) -> str:
        """Format all step results into a readable summary.

        Each step result is truncated to *max_result_chars* to prevent the
        analyzer prompt from exceeding the LLM's context window when steps
        produce very large outputs.

        Args:
            plan: The execution plan with populated step results.
            max_result_chars: Maximum characters per step result.

        Returns:
            A multi-line summary of each step's status and result.
        """
        if not plan.steps:
            return "(no steps in plan)"

        lines: list[str] = []
        for step in plan.steps:
            deps = ", ".join(step.dependencies) if step.dependencies else "none"
            hint = step.tool_hint or "none"
            result_text = step.result or "(no result)"
            if len(result_text) > max_result_chars:
                result_text = result_text[:max_result_chars] + "\n  [Result truncated]"
            lines.append(
                f"[{step.id}] (status: {step.status}, deps: {deps}, tool_hint: {hint})\n"
                f"  Task: {step.task}\n"
                f"  Result: {result_text}"
            )

        return "\n\n".join(lines)

    @staticmethod
    def _dict_to_analysis_result(data: dict[str, Any]) -> AnalysisResult:
        """Transform a raw dict into an ``AnalysisResult``.

        Handles type coercion and clamping of the confidence score.
        """
        achieved = bool(data.get("achieved", False))

        raw_confidence = data.get("confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(raw_confidence)))
        except (TypeError, ValueError):
            confidence = 0.0

        final_answer = data.get("final_answer")
        if final_answer is not None:
            final_answer = str(final_answer)

        reasoning = str(data.get("reasoning", ""))

        return AnalysisResult(
            achieved=achieved,
            confidence=confidence,
            final_answer=final_answer,
            reasoning=reasoning,
        )


def _regex_extract_analysis(content: str) -> dict[str, Any] | None:
    """Last-resort regex extraction of analysis fields from malformed JSON.

    Attempts to pull ``achieved``, ``confidence``, and ``final_answer`` from
    *content* even when ``extract_json`` cannot parse it.
    """
    achieved_m = re.search(r'"achieved"\s*:\s*(true|false)', content, re.IGNORECASE)
    conf_m = re.search(r'"confidence"\s*:\s*([\d.]+)', content)
    if not achieved_m:
        return None
    data: dict[str, Any] = {
        "achieved": achieved_m.group(1).lower() == "true",
    }
    if conf_m:
        try:
            data["confidence"] = float(conf_m.group(1))
        except ValueError:
            pass
    # Extract final_answer — everything between "final_answer": " and the
    # matching close.  Since the value can span many lines we grab up to the
    # last `"` followed by optional whitespace and either `}` or `"reasoning"`.
    fa_m = re.search(
        r'"final_answer"\s*:\s*"([\s\S]*)',
        content,
    )
    if fa_m:
        raw = fa_m.group(1)
        # Walk backwards to find the true end of the string value.
        # Look for `", ` or `"}` or `"\n}` patterns.
        for end_pat in ('"\\s*,\\s*"reasoning"', '"\\s*,\\s*"', '"\\s*\\}'):
            end_m = re.search(end_pat, raw)
            if end_m:
                raw = raw[: end_m.start()]
                break
        # Unescape JSON string escapes.
        raw = raw.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
        data["final_answer"] = raw

    # Extract reasoning using the same greedy strategy as final_answer,
    # because reasoning may contain unescaped quotes from the LLM.
    reason_m = re.search(r'"reasoning"\s*:\s*"([\s\S]*)', content)
    if reason_m:
        raw_r = reason_m.group(1)
        # Walk backwards to find the true end of the string value.
        # Try specific field boundaries first, then generic patterns.
        for end_pat in (
            '"\\s*,\\s*"(?:final_answer|achieved|confidence)"',
            '"\\s*,\\s*"',
            '"\\s*\\}',
        ):
            end_r = re.search(end_pat, raw_r)
            if end_r:
                raw_r = raw_r[: end_r.start()]
                break
        raw_r = raw_r.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
        data["reasoning"] = raw_r

    return data
