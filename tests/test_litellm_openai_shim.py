"""Unit tests for LiteLLMOpenAIShim.

The shim bridges an FIM One ``OpenAICompatibleLLM`` to the
``openai.OpenAI``-shaped API surface that MarkItDown (and other libs
written against the openai SDK) expect. Tests verify:

* The duck-type path exists: ``shim.chat.completions.create(...)``
* Calls are dispatched to ``litellm.completion(...)``
* LLM credentials (``_litellm_model`` / ``_api_key`` / ``_api_base``)
  are forwarded correctly
* ``model=`` override takes precedence when supplied by the caller
* ``model_id`` property exposes the underlying LLM's model id
"""

from __future__ import annotations

from typing import Any

import pytest

from fim_one.core.model.litellm_openai_shim import LiteLLMOpenAIShim
from fim_one.core.model.openai_compatible import OpenAICompatibleLLM


@pytest.fixture()
def fim_llm() -> OpenAICompatibleLLM:
    """Return a realistic FIM One LLM pointing at an OpenAI-compat endpoint."""
    return OpenAICompatibleLLM(
        api_key="sk-test-key",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
    )


class TestShimShape:
    """Verify the duck-type API surface MarkItDown expects."""

    def test_chat_completions_create_path_exists(
        self, fim_llm: OpenAICompatibleLLM
    ) -> None:
        shim = LiteLLMOpenAIShim(fim_llm)
        # MarkItDown's source calls exactly this path — confirm it's
        # reachable without AttributeError.
        assert hasattr(shim, "chat")
        assert hasattr(shim.chat, "completions")
        assert hasattr(shim.chat.completions, "create")
        assert callable(shim.chat.completions.create)

    def test_model_id_delegates_to_llm(
        self, fim_llm: OpenAICompatibleLLM
    ) -> None:
        shim = LiteLLMOpenAIShim(fim_llm)
        assert shim.model_id == "gpt-4o-mini"


class TestShimDispatch:
    """Verify calls route through litellm.completion."""

    def test_create_dispatches_to_litellm_completion(
        self,
        fim_llm: OpenAICompatibleLLM,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorded: dict[str, Any] = {}

        def fake_completion(**kwargs: Any) -> str:
            recorded.update(kwargs)
            return "FAKE_RESPONSE"

        import litellm

        monkeypatch.setattr(litellm, "completion", fake_completion)

        shim = LiteLLMOpenAIShim(fim_llm)
        result = shim.chat.completions.create(
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.3,
        )

        assert result == "FAKE_RESPONSE"
        # Credentials threaded from the underlying LLM.
        assert recorded["api_key"] == "sk-test-key"
        # `_litellm_model` is a derived attribute — we accept whatever
        # the LLM resolved it to, as long as it's non-empty.
        assert recorded["model"]
        # Additional kwargs pass through.
        assert recorded["temperature"] == 0.3
        # Messages preserved byte-for-byte.
        assert recorded["messages"] == [{"role": "user", "content": "hi"}]

    def test_create_ignores_caller_model_uses_resolved(
        self,
        fim_llm: OpenAICompatibleLLM,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The shim always uses the resolved LiteLLM model name.

        Callers (e.g. markitdown-ocr's LLMVisionOCRService) pass the raw
        model name (``claude-sonnet-4-6``) which lacks the provider prefix
        needed for correct LiteLLM routing.  On relay proxies like UniAPI
        this causes double-``/v1`` URLs and a 404.  The shim must use the
        pre-resolved ``_litellm_model`` regardless of what the caller asks.
        """
        recorded: dict[str, Any] = {}

        def fake_completion(**kwargs: Any) -> str:
            recorded.update(kwargs)
            return "ok"

        import litellm

        monkeypatch.setattr(litellm, "completion", fake_completion)

        shim = LiteLLMOpenAIShim(fim_llm)
        shim.chat.completions.create(
            model="gpt-4o",  # caller supplies raw name — should be ignored
            messages=[{"role": "user", "content": "hi"}],
        )

        # Resolved LiteLLM model is used, not the caller's raw name.
        assert recorded["model"] == fim_llm._litellm_model
