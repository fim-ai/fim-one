"""OpenAI-SDK-shaped duck-type that routes through LiteLLM.

Background
----------
Some third-party libraries written against the ``openai`` SDK (e.g.
Microsoft's MarkItDown) hard-code calls like::

    client.chat.completions.create(model=..., messages=[...])

FIM One's :class:`OpenAICompatibleLLM` exposes a different method:
``llm.chat(messages, ...)`` — a custom FIM One interface, not the nested
``.chat.completions.create`` path those libraries expect. Naively
passing a FIM One LLM to such libraries raises ``AttributeError`` the
moment they try ``client.chat.completions.create``.

Why not hand them a raw ``openai.OpenAI(api_key=..., base_url=...)``?
Because the user's active model might not be OpenAI-compatible at the
*wire* level. If the resolved LLM points at Anthropic's or Google's
native API, the OpenAI SDK will fail — those providers don't serve the
``/v1/chat/completions`` endpoint. LiteLLM already solves the
"one API, many providers" problem: ``litellm.completion(...)`` accepts
OpenAI-format messages and internally translates to Anthropic, Google
Gemini, Bedrock, Azure, and anything else LiteLLM supports.

This module provides the missing piece: a tiny duck-type object that
*looks like* ``openai.OpenAI`` to consumers but delegates every call to
``litellm.completion()``. The MarkItDown / instructor / langchain-openai
ecosystem all talk to it via the ``.chat.completions.create`` path; under
the hood it's the same LiteLLM routing the rest of FIM One uses.

One shim, N providers. When a new provider joins LiteLLM, this shim
inherits support for free — no code changes needed in FIM One.

Design notes
------------
- **No state beyond the wrapped LLM.** The shim holds a reference to the
  resolved :class:`OpenAICompatibleLLM` and reads its ``_litellm_model``,
  ``_api_key``, and ``_api_base`` on each call. This means rotating
  credentials or switching models on the underlying LLM is visible to the
  shim without rebuilding it.
- **Return type is LiteLLM's ``ModelResponse``**, which is already
  OpenAI-compatible (``response.choices[0].message.content`` works).
  MarkItDown and friends don't know the difference.
- **``create()`` is synchronous**, matching the ``openai.OpenAI``
  non-async API that MarkItDown expects. Callers running inside an event
  loop should wrap the whole conversion in ``asyncio.to_thread``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fim_one.core.model.openai_compatible import OpenAICompatibleLLM


class LiteLLMOpenAIShim:
    """An ``openai.OpenAI``-shaped client backed by a FIM One LLM + LiteLLM.

    Attributes:
        chat: A :class:`_Chat` namespace exposing
            ``.completions.create(...)`` — the exact method path
            MarkItDown (and other openai-SDK consumers) call.

    Example:
        >>> llm = await _resolve_llm(agent_cfg, db)   # FIM One's resolver
        >>> shim = LiteLLMOpenAIShim(llm)
        >>> # Now pass it anywhere an openai.OpenAI is expected:
        >>> MarkItDown(llm_client=shim, llm_model=llm.model_id)
    """

    def __init__(self, llm: "OpenAICompatibleLLM") -> None:
        self._llm = llm
        self.chat: _Chat = _Chat(llm)

    @property
    def model_id(self) -> str:
        """The model id of the underlying FIM One LLM."""
        return self._llm.model_id


class _Chat:
    """Private namespace object that mimics ``openai.OpenAI().chat``."""

    def __init__(self, llm: "OpenAICompatibleLLM") -> None:
        self.completions: _Completions = _Completions(llm)


class _Completions:
    """Private namespace object that mimics ``chat.completions``."""

    def __init__(self, llm: "OpenAICompatibleLLM") -> None:
        self._llm = llm

    def create(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Mimic ``openai.OpenAI().chat.completions.create(...)``.

        Args:
            messages: OpenAI-format messages, including vision content
                blocks (``{"type": "image_url", "image_url": {...}}``).
                LiteLLM translates these to each provider's native
                format (e.g. Anthropic's ``source.type=base64``) when
                routing.
            model: Optional model override. When ``None`` (the default),
                the underlying FIM One LLM's resolved LiteLLM model id
                is used — this is what you want almost always, so
                consumers can pass ``llm_model=llm.model_id`` and the
                shim threads it straight through.
            **kwargs: Forwarded verbatim to ``litellm.completion()`` —
                ``temperature``, ``max_tokens``, ``response_format``,
                ``tools``, etc. all work unchanged.

        Returns:
            A LiteLLM ``ModelResponse`` — already OpenAI-shaped, so
            ``response.choices[0].message.content`` works in consumer
            code written against the openai SDK.
        """
        import litellm  # local import keeps module load cheap

        # Always use the resolved LiteLLM model name (e.g. "openai/claude-
        # sonnet-4-6") rather than the raw caller-supplied name.  The
        # resolved name carries the correct provider prefix so LiteLLM
        # routes to the right endpoint.  Without this, a raw name like
        # "claude-sonnet-4-6" triggers Anthropic-native routing which
        # appends /v1/messages to api_base — doubling the /v1 path on
        # OpenAI-compatible relay proxies (e.g. UniAPI) and causing a 404.
        target_model = self._llm._litellm_model
        return litellm.completion(
            model=target_model,
            messages=messages,
            api_key=self._llm._api_key,
            api_base=self._llm._api_base,
            **kwargs,
        )
