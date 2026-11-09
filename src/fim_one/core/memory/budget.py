"""Input-token budget arithmetic shared by every context-management layer.

One formula turns a model window into a usable input budget
(:func:`compute_input_budget`), and one helper applies it to an LLM
instance from the window that instance itself declares
(:func:`declared_input_budget` / :func:`llm_input_budget`).  Keeping both
here lets the compaction utilities, :class:`ContextGuard`, the DAG executor
and the web layer agree on what "fits in the window" means for a given
model.
"""

from __future__ import annotations

import os

#: Fraction of the raw model window (``context_size - max_output_tokens``)
#: actually budgeted for input.  Token counts are estimated with a character
#: heuristic (:meth:`CompactUtils.estimate_tokens`) that can under-estimate
#: real tokenizer counts by ~10%; without headroom the compaction trigger
#: sits exactly on the API hard limit and any under-estimate becomes an
#: HTTP 400 rescued only by the one-shot overflow retry.
BUDGET_SAFETY_FACTOR = 0.92

#: Input budget assumed for a model that declares no context window, when
#: the caller has nothing better to fall back on.  The web layer does have
#: something better (the system model configured for the role, then the
#: ``LLM_CONTEXT_SIZE`` family of env vars) and passes it explicitly.
DEFAULT_INPUT_BUDGET = int(os.getenv("CONTEXT_GUARD_DEFAULT_BUDGET") or "32000")

#: Output reserve assumed for a model that declares a context window but no
#: max-output setting.  Mirrors the ``LLM_MAX_OUTPUT_TOKENS`` default.
DEFAULT_MAX_OUTPUT_RESERVE = 64_000


def compute_input_budget(context_size: int, max_output: int) -> int:
    """Usable input token budget for a model window.

    ``(context_size - max_output) * BUDGET_SAFETY_FACTOR``, floored at
    4 000 tokens.  The system prompt is NOT subtracted — ContextGuard
    estimates the full message list (system prompt included) dynamically,
    so a static reserve would double-count it.

    An output reserve as large as the window itself cannot be a real
    setting for that model: it is what a small model gets when its max
    output is left at the 64 000 default.  Half the window is reserved
    instead, so such a model is budgeted at a usable size rather than at
    the floor — chat budgets a conversation by its smallest model, and the
    floor would starve the whole turn.
    """
    if max_output >= context_size:
        max_output = context_size // 2
    budget = int((context_size - max_output) * BUDGET_SAFETY_FACTOR)
    return max(budget, 4_000)


def _positive_int(value: object) -> int | None:
    """Return *value* when it is a real positive ``int``, else ``None``.

    Guards against mocks and ``bool`` — test doubles hand back a
    ``MagicMock`` for any attribute, and ``True`` is an ``int``.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def declared_input_budget(llm: object) -> int | None:
    """Input budget derived from the window *llm* itself declares.

    Reads ``llm.context_size`` and ``llm.max_output_tokens`` (see
    :class:`~fim_one.core.model.base.BaseLLM`).  Returns ``None`` when the
    model carries no window metadata, so the caller can pick its own
    fallback.  A missing max-output setting reserves
    :data:`DEFAULT_MAX_OUTPUT_RESERVE`.
    """
    context_size = _positive_int(getattr(llm, "context_size", None))
    if context_size is None:
        return None
    max_output = _positive_int(getattr(llm, "max_output_tokens", None))
    if max_output is None:
        max_output = DEFAULT_MAX_OUTPUT_RESERVE
    return compute_input_budget(context_size, max_output)


def llm_input_budget(llm: object, default: int = DEFAULT_INPUT_BUDGET) -> int:
    """Input budget for *llm*: its declared window, else *default*.

    *default* is :data:`DEFAULT_INPUT_BUDGET` (``CONTEXT_GUARD_DEFAULT_BUDGET``,
    32 000 tokens) unless the caller knows a better fallback.
    """
    declared = declared_input_budget(llm)
    return declared if declared is not None else default
