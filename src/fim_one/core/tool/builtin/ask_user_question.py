"""ask_user_question — pause the ReAct loop and ask the user to choose.

Borrowed in shape from Claude Code's ``AskUserQuestion`` tool: when a task
has multiple materially different directions, the model asks 1-4
structured multiple-choice questions instead of guessing.  The frontend
renders an interactive question card; the user's selections come back as
the tool result and the run continues in the same turn.

Mechanically this reuses the inline-confirmation pipeline end to end
(the same machinery ``FeishuGateHook`` uses for approve/reject gates):

1. ``run()`` validates the questions and commits a
   ``ConfirmationRequest(kind="user_question", mode="inline")`` row.
2. It fires :func:`fim_one.core.hooks.emit_inline_confirmation`; the web
   layer's SSE bridge turns the row into an ``awaiting_user_question``
   frame mid-stream.
3. It polls the row until the answer endpoint
   (``POST /api/confirmations/{id}/answer``) flips it to ``answered`` /
   ``dismissed``, or the timeout expires.

The tool needs live per-request state (session factory + requester
identity), so it is excluded from builtin auto-discovery and registered
explicitly by the ReAct chat endpoint — the same pattern as
``UpdatePlanTool`` and ``MarkItDownTool``.  The DAG executor does NOT
register it: mid-plan questions don't fit a precomputed DAG.
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.tool.base import BaseTool

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]

#: Users read 1-4 questions and may type a custom answer — noticeably
#: slower than an approve/reject glance, hence a wider window than the
#: confirmation gate's 120s.  Kept deliberately bounded beyond that: these
#: prompts are meant for immediate, in-flow decisions, and an unanswered
#: question should release the turn rather than pin it.
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_POLL_INTERVAL_SECONDS = 1.5

MAX_QUESTIONS = 4
MIN_OPTIONS = 2
MAX_OPTIONS = 4
MAX_HEADER_CHARS = 12


def normalize_questions(raw: Any) -> list[dict[str, Any]]:
    """Validate and normalise the ``questions`` argument from the LLM.

    Raises:
        ValueError: With a model-readable message when the structure is
            invalid.  The caller returns it as the tool result so the
            model can self-correct on the next iteration.
    """
    if not isinstance(raw, list):
        raise ValueError("questions must be an array of question objects.")
    if not raw:
        raise ValueError("questions must not be empty.")
    if len(raw) > MAX_QUESTIONS:
        raise ValueError(f"questions supports at most {MAX_QUESTIONS} items.")

    normalized: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(
                f"questions[{i}] must be an object, got {type(item).__name__}."
            )
        question = str(item.get("question", "")).strip()
        if not question:
            raise ValueError(f"questions[{i}].question must be a non-empty string.")
        if question in seen_questions:
            raise ValueError(f"questions[{i}].question duplicates an earlier question.")
        seen_questions.add(question)

        header = str(item.get("header", "")).strip()[:MAX_HEADER_CHARS] or f"Q{i + 1}"

        raw_options = item.get("options")
        if not isinstance(raw_options, list):
            raise ValueError(f"questions[{i}].options must be an array.")
        if not (MIN_OPTIONS <= len(raw_options) <= MAX_OPTIONS):
            raise ValueError(
                f"questions[{i}].options must have between {MIN_OPTIONS} and "
                f"{MAX_OPTIONS} options."
            )
        options: list[dict[str, str]] = []
        seen_labels: set[str] = set()
        for j, opt in enumerate(raw_options):
            if not isinstance(opt, dict):
                raise ValueError(f"questions[{i}].options[{j}] must be an object.")
            label = str(opt.get("label", "")).strip()
            if not label:
                raise ValueError(
                    f"questions[{i}].options[{j}].label must be a non-empty string."
                )
            if label in seen_labels:
                raise ValueError(
                    f"questions[{i}].options[{j}].label duplicates an earlier option."
                )
            seen_labels.add(label)
            options.append(
                {
                    "label": label,
                    "description": str(opt.get("description", "")).strip(),
                }
            )

        normalized.append(
            {
                "question": question,
                "header": header,
                "options": options,
                "multi_select": bool(item.get("multi_select", False)),
            }
        )
    return normalized


def format_answers(
    questions: list[dict[str, Any]],
    answers: dict[str, Any],
    notes: dict[str, Any] | None = None,
) -> str:
    """Render the user's answers as the observation string for the model.

    ``notes`` are optional per-question free-text additions the user
    attached to their selection ("option 1, but …") — rendered inline so
    the model treats them as qualifying the chosen option.
    """
    parts: list[str] = []
    for q in questions:
        text = q["question"]
        answer = answers.get(text)
        note = str((notes or {}).get(text) or "").strip()
        if answer is None:
            line = f'"{text}" = (not answered)'
        else:
            if isinstance(answer, list):
                answer = ", ".join(str(a) for a in answer)
            line = f'"{text}" = "{answer}"'
        if note:
            line += f" (user note: {note})"
        parts.append(line)
    return (
        "User answered your questions: "
        + "; ".join(parts)
        + ". You can now continue with the user's answers in mind."
    )


class AskUserQuestionTool(BaseTool):
    """Ask the user structured multiple-choice questions mid-run."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        user_id: str,
        agent_id: str | None = None,
        org_id: str | None = None,
        conversation_id: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._user_id = user_id
        self._agent_id = agent_id
        self._org_id = org_id
        self._conversation_id = conversation_id
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds

    @property
    def name(self) -> str:
        return "ask_user_question"

    @property
    def category(self) -> str:
        return "interaction"

    @property
    def display_name(self) -> str:
        return "Ask User"

    @property
    def description(self) -> str:
        return (
            "Ask the user 1-4 multiple choice questions to clarify ambiguity, "
            "gather preferences, or decide between materially different "
            "directions (scope, format, approach) that you cannot resolve from "
            "context. The run PAUSES until the user answers, so use it only "
            "when the answer genuinely changes what you do next — never to ask "
            "for permission to proceed, to report progress, or when a sensible "
            "default exists (pick the default and continue). Each question has "
            "2-4 options with a short label and a one-line description; the UI "
            "automatically adds an 'Other' free-text choice, so do not add "
            "your own. If you recommend an option, put it first and append "
            "' (Recommended)' to its label. Set multi_select true when several "
            "options can be chosen together. Batch related questions into one "
            "call instead of asking one at a time."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_QUESTIONS,
                    "description": "Questions to ask the user (1-4).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": (
                                    "The complete question to ask. Clear, "
                                    "specific, ends with a question mark."
                                ),
                            },
                            "header": {
                                "type": "string",
                                "description": (
                                    "Very short chip label for this question "
                                    f"(max {MAX_HEADER_CHARS} chars), e.g. "
                                    "'Scope' or 'Format'."
                                ),
                            },
                            "options": {
                                "type": "array",
                                "minItems": MIN_OPTIONS,
                                "maxItems": MAX_OPTIONS,
                                "description": (
                                    "2-4 distinct choices. Do NOT include an "
                                    "'Other' option — it is added "
                                    "automatically."
                                ),
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {
                                            "type": "string",
                                            "description": (
                                                "Concise display text "
                                                "(1-5 words)."
                                            ),
                                        },
                                        "description": {
                                            "type": "string",
                                            "description": (
                                                "One line on what this choice "
                                                "means or implies."
                                            ),
                                        },
                                    },
                                    "required": ["label", "description"],
                                },
                            },
                            "multi_select": {
                                "type": "boolean",
                                "description": (
                                    "Allow selecting multiple options "
                                    "(default false)."
                                ),
                            },
                        },
                        "required": ["question", "header", "options"],
                    },
                },
            },
            "required": ["questions"],
        }

    @property
    def timeout_seconds(self) -> float | None:
        # Loop backstop must sit ABOVE the internal wait so the poll's own
        # timeout path (mark row expired, tell the model to continue) runs
        # instead of the loop's generic tool-timeout message.
        return float(self._timeout_seconds + 30)

    async def run(self, **kwargs: Any) -> str:
        try:
            questions = normalize_questions(kwargs.get("questions"))
        except ValueError as exc:
            return f"[Error] {exc}"

        question_id = str(uuid.uuid4())
        try:
            row = await self._create_request_row(question_id, questions)
        except Exception:
            logger.exception("ask_user_question: failed to create request row")
            return (
                "[Error] Could not deliver the questions to the user. "
                "Continue with your best judgment and state your assumptions."
            )

        # Fire the SSE-bound listener (best-effort — never raises).
        from fim_one.core.hooks.inline_confirmation import emit_inline_confirmation

        await emit_inline_confirmation(row)

        outcome, response = await self._await_answer(question_id)
        if outcome == "answered":
            response = response or {}
            answers = response.get("answers")
            notes = response.get("notes")
            return format_answers(
                questions,
                answers if isinstance(answers, dict) else {},
                notes if isinstance(notes, dict) else None,
            )
        if outcome == "dismissed":
            return (
                "The user chose not to answer these questions. Do not ask "
                "again — continue with your best judgment and state the "
                "assumptions you made."
            )
        # timeout / expired
        return (
            f"No response from the user within {self._timeout_seconds}s. "
            "Do not ask again — continue with your best judgment and state "
            "the assumptions you made."
        )

    # ------------------------------------------------------------------
    # DB helpers — mirror FeishuGateHook's inline flow.
    # ------------------------------------------------------------------

    async def _create_request_row(
        self, question_id: str, questions: list[dict[str, Any]]
    ) -> Any:
        from fim_one.db.models.channel import ConfirmationRequest

        payload: dict[str, Any] = {
            "kind": "user_question",
            "mode": "inline",
            "questions": questions,
            "conversation_id": self._conversation_id or "",
            "timeout_seconds": self._timeout_seconds,
        }
        async with self._session_factory() as session:
            row = ConfirmationRequest(
                id=question_id,
                agent_id=self._agent_id,
                user_id=self._user_id,
                # Only the initiator may answer their own question.
                approver_user_id=self._user_id,
                org_id=self._org_id or None,
                channel_id=None,
                mode="inline",
                kind="user_question",
                status="pending",
                payload=payload,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def _await_answer(
        self, question_id: str
    ) -> tuple[str, dict[str, Any] | None]:
        """Poll the request row until answered / dismissed / timeout.

        Returns ``(outcome, response_payload)`` — the payload carries
        ``answers`` and optional ``notes`` dicts when answered.
        """
        from fim_one.db.models.channel import ConfirmationRequest

        deadline = asyncio.get_event_loop().time() + self._timeout_seconds
        while True:
            async with self._session_factory() as session:
                stmt = select(ConfirmationRequest).where(
                    ConfirmationRequest.id == question_id
                )
                row = (await session.execute(stmt)).scalar_one_or_none()
                if row is not None and row.status == "answered":
                    response = (
                        row.response_payload
                        if isinstance(row.response_payload, dict)
                        else {}
                    )
                    return ("answered", response)
                if row is not None and row.status in ("dismissed", "rejected"):
                    return ("dismissed", None)
                if row is not None and row.status == "expired":
                    return ("expired", None)

            if asyncio.get_event_loop().time() >= deadline:
                await self._mark_expired(question_id)
                return ("expired", None)
            await asyncio.sleep(self._poll_interval_seconds)

    async def _mark_expired(self, question_id: str) -> None:
        from fim_one.db.models.channel import ConfirmationRequest

        try:
            async with self._session_factory() as session:
                stmt = select(ConfirmationRequest).where(
                    ConfirmationRequest.id == question_id
                )
                row = (await session.execute(stmt)).scalar_one_or_none()
                if row is not None and row.status == "pending":
                    row.status = "expired"
                    row.responded_at = datetime.now(UTC)
                    await session.commit()
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "ask_user_question: failed to mark %s expired", question_id
            )


__all__ = [
    "AskUserQuestionTool",
    "normalize_questions",
    "format_answers",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_POLL_INTERVAL_SECONDS",
]
