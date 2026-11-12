"""Model endpoints supplied by a user.

What is enforced:

* A request to a URL a user chose carries only a key that user supplied.
  No combination of settings falls back to a platform credential.
* Such an endpoint is called by the OpenAI SDK on the address-checking
  client and by nothing else: no model name, provider or URL shape selects
  another transport.  Internal addresses are refused when connecting and
  redirects are not followed, for streaming and non-streaming calls.
* A failing endpoint's response body is not repeated in the error.
"""

from __future__ import annotations

import http.server
import itertools
import json
import socket
import threading
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from fim_one.core.model import openai_compatible as oc
from fim_one.core.model.openai_compatible import OpenAICompatibleLLM, UntrustedEndpointError
from fim_one.core.model.types import ChatMessage
from fim_one.web import deps
from fim_one.web.deps import InlineModelConfigError, check_inline_model_config, get_llm_from_config

_SYSTEM_SECRETS = {
    "LLM_API_KEY": "sk-SYSTEM-llm-key",
    "FAST_LLM_API_KEY": "sk-SYSTEM-fast-key",
    "REASONING_LLM_API_KEY": "sk-SYSTEM-reasoning-key",
    "OPENAI_API_KEY": "sk-SYSTEM-openai-key",
    "ANTHROPIC_API_KEY": "sk-SYSTEM-anthropic-key",
    "GEMINI_API_KEY": "sk-SYSTEM-gemini-key",
    "DEEPSEEK_API_KEY": "sk-SYSTEM-deepseek-key",
    "MISTRAL_API_KEY": "sk-SYSTEM-mistral-key",
    "EMBEDDING_API_KEY": "sk-SYSTEM-embedding-key",
    "JINA_API_KEY": "sk-SYSTEM-jina-key",
    "IMAGE_GEN_API_KEY": "sk-SYSTEM-image-key",
}
_SYSTEM_URL = "https://llm.internal.example/v1"
_USER_URL = "https://models.user-endpoint.example/v1"
_USER_KEY = "sk-user-own-key"


@pytest.fixture(autouse=True)
def _system_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _SYSTEM_SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("LLM_BASE_URL", _SYSTEM_URL)


def _host(url: str | None) -> str:
    return (httpx.URL(url).host if url else "").lower()


def _carries_system_secret(value: Any) -> bool:
    return any(secret in str(value) for secret in _SYSTEM_SECRETS.values())


class _Captured(Exception):
    pass


_LITELLM_ENTRY_POINTS = (
    "acompletion", "completion", "aresponses", "responses", "atext_completion",
    "text_completion", "aembedding", "embedding", "anthropic_messages",
)


async def _outbound(llm: OpenAICompatibleLLM, monkeypatch: pytest.MonkeyPatch, *, stream: bool) -> dict[str, Any]:
    """Run one call and report which transport was asked to send what."""
    seen: dict[str, Any] = {}

    def _litellm(name: str) -> Any:
        async def _fake(**kwargs: Any) -> Any:
            seen["via"] = f"litellm.{name}"
            seen.update(kwargs)
            raise _Captured()

        return _fake

    for name in _LITELLM_ENTRY_POINTS:
        if hasattr(oc.litellm, name):
            monkeypatch.setattr(oc.litellm, name, _litellm(name))

    class _Completions:
        def __init__(self, client: Any) -> None:
            self._client = client

        async def create(self, **kwargs: Any) -> Any:
            seen["via"] = "pinned"
            seen["client_key"] = self._client.api_key
            seen["client_base"] = str(self._client.base_url)
            seen["http_client"] = self._client._client
            seen.update(kwargs)
            raise _Captured()

    real_pinned = getattr(OpenAICompatibleLLM, "_pinned_client", None)
    if real_pinned is not None:

        def _pinned(self: OpenAICompatibleLLM) -> Any:
            client = real_pinned(self)
            client.chat.completions = _Completions(client)
            return client

        monkeypatch.setattr(OpenAICompatibleLLM, "_pinned_client", _pinned)
    from fim_one.core.model.retry import RetryConfig

    llm._retry_config = RetryConfig(max_retries=0)
    with pytest.raises(Exception):
        if stream:
            async for _ in llm.stream_chat([ChatMessage(role="user", content="hi")]):
                pass
        else:
            await llm.chat([ChatMessage(role="user", content="hi")])
    assert seen, "no outbound call was attempted"
    return seen


# Model names that a router could read as "use another protocol or provider".
_ROUTING_PREFIXES = [
    "", "responses/", "openai/", "openai/responses/", "azure/", "azure_ai/", "anthropic/", "bedrock/",
    "vertex_ai/", "gemini/", "ollama/", "ollama_chat/", "huggingface/", "sagemaker/", "openrouter/",
    "text-completion-openai/", "custom_openai/", "litellm_proxy/", "github_copilot/", "chatgpt/",
    "responses/responses/", "openai/openai/", "/", "//", "../", "http://127.0.0.1/", "RESPONSES/",
]
_ROUTING_BASES = ["m", "gpt-5", "gpt-5.1-codex", "gpt-5-pro", "o3-pro", "o1-pro", "gpt-4o", "claude-3", "codex-mini-latest"]
_ROUTING_MODELS = sorted({f"{prefix}{base}" for prefix in _ROUTING_PREFIXES for base in _ROUTING_BASES})

# Every value a user could put in each routing field.
_BASE_URLS = [None, "", _USER_URL, "https://api.openai.com.user-endpoint.example/v1",
              "https://user-endpoint.example/api.openai.com/v1", "https://user-endpoint.example/anthropic.com",
              "https://user-endpoint.example/claude", "https://user-endpoint.example/gemini/v1",
              "https://api.openai.com/v1", "https://api.anthropic.com/v1"]
_API_KEYS = [None, "", "   ", _USER_KEY]
_PROVIDERS = [None, "", "openai", "anthropic", "gemini", "deepseek", "mistral",
              "azure", "bedrock", "vertex_ai", "ollama", "sagemaker", "huggingface", "custom_openai"]
_MODELS = ["gpt-4o", "openai/gpt-4o", "anthropic/claude-3", "gpt-5", "responses/gpt-5", "o3-pro"]


class TestInlineConfigNeverUsesSystemCredentials:
    async def test_matrix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checked = 0
        for base_url, api_key, provider, model in itertools.product(
            _BASE_URLS, _API_KEYS, _PROVIDERS, _MODELS
        ):
            config = {
                k: v
                for k, v in {
                    "model_name": model, "base_url": base_url,
                    "api_key": api_key, "provider": provider,
                }.items()
                if v is not None
            }
            try:
                llm = get_llm_from_config(config)
            except InlineModelConfigError:
                continue
            assert llm is not None
            with monkeypatch.context() as m:
                sent = await _outbound(llm, m, stream=bool(checked % 2))
            checked += 1

            if (base_url or "").strip():
                # Their URL: the pinned client, their key, their host, and
                # the model name as a plain field.
                assert sent["via"] == "pinned", (config, sent["via"])
                assert sent["client_key"] == _USER_KEY
                assert _host(sent["client_base"]) == _host(base_url)
                assert sent["http_client"] is oc._get_untrusted_http_client()
                assert sent["model"] == model
                assert "api_key" not in sent and "api_base" not in sent
                assert not _carries_system_secret(
                    {k: v for k, v in sent.items() if k != "http_client"}
                ), config
            else:
                # Platform endpoint: the URL is the operator's, whoever's key.
                assert str(sent["via"]).startswith("litellm.")
                assert _host(sent.get("api_base")) == _host(_SYSTEM_URL), config
                assert sent.get("client") is None
        assert checked > 100

    @pytest.mark.parametrize("stream", [False, True])
    async def test_no_model_name_selects_another_transport(
        self, stream: bool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for model in _ROUTING_MODELS:
            llm = get_llm_from_config({"model_name": model, "base_url": _USER_URL, "api_key": _USER_KEY})
            assert llm is not None
            with monkeypatch.context() as m:
                sent = await _outbound(llm, m, stream=stream)
            assert sent["via"] == "pinned", (model, sent["via"])
            assert sent["model"] == model

    @pytest.mark.parametrize("api_key", [None, "", "  "])
    def test_custom_url_without_own_key_is_refused(self, api_key: str | None) -> None:
        config = {"model_name": "m", "base_url": _USER_URL, "api_key": api_key}
        with pytest.raises(InlineModelConfigError):
            get_llm_from_config(config)
        with pytest.raises(InlineModelConfigError):
            check_inline_model_config(config)

    @pytest.mark.parametrize(
        "base_url",
        [
            "file:///etc/passwd", "ftp://x.example/", "gopher://x.example/", "//x.example/v1",
            "x.example/v1", "http://user:pw@x.example/v1", "http://sk@x.example/v1",
            "http://x.example/v1\r\nX: y", "http://x.example /v1", "http://[::1/v1", "http://:80/v1",
        ],
    )
    def test_malformed_urls_are_refused(self, base_url: str) -> None:
        config = {"model_name": "m", "base_url": base_url, "api_key": _USER_KEY}
        with pytest.raises(InlineModelConfigError):
            get_llm_from_config(config)

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://127.0.0.1:8000/v1", "http://localhost:8000/v1", "http://[::1]/v1",
            "http://169.254.169.254/v1", "http://100.100.100.200/v1", "http://10.0.0.5/v1",
            "http://192.168.0.2:11434/v1", "http://2130706433/v1", "http://[::ffff:10.0.0.1]/v1",
        ],
    )
    def test_internal_urls_are_refused_when_saved(self, base_url: str) -> None:
        config = {"model_name": "m", "base_url": base_url, "api_key": _USER_KEY}
        with pytest.raises(InlineModelConfigError):
            check_inline_model_config(config, resolve=True)

    def test_non_string_fields_are_refused(self) -> None:
        for bad in ({"base_url": ["x"]}, {"api_key": {"a": 1}}, {"provider": 5}):
            with pytest.raises(InlineModelConfigError):
                get_llm_from_config({"model_name": "m", **bad})

    def test_direct_construction_requires_a_key(self) -> None:
        with pytest.raises(UntrustedEndpointError):
            OpenAICompatibleLLM("", _USER_URL, "m", untrusted_endpoint=True)

    def test_document_ocr_shim_refuses_user_endpoints(self) -> None:
        from fim_one.core.model.litellm_openai_shim import LiteLLMOpenAIShim

        llm = get_llm_from_config({"model_name": "m", "base_url": _USER_URL, "api_key": _USER_KEY})
        assert llm is not None
        with pytest.raises(ValueError):
            LiteLLMOpenAIShim(llm)


class _FakeRow:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(
            dict(model_name="m", temperature=None, max_output_tokens=None, provider=None,
                 context_size=None, is_active=True), **kw
        )


class _FakeDb:
    def __init__(self, row: Any) -> None:
        self._row = row

    async def execute(self, stmt: Any) -> Any:
        row = self._row

        class _R:
            def scalar_one_or_none(self) -> Any:
                return row

        return _R()


class TestModelConfigRows:
    async def test_user_owned_row_with_url_needs_its_own_key(self) -> None:
        row = _FakeRow(user_id="u1", base_url=_USER_URL, api_key=None)
        with pytest.raises(InlineModelConfigError):
            await deps.get_llm_by_config_id(_FakeDb(row), "id", owner_id="u1")  # type: ignore[arg-type]

    async def test_user_owned_row_uses_the_checked_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _FakeRow(user_id="u1", base_url=_USER_URL, api_key=_USER_KEY, provider="bedrock")
        llm = await deps.get_llm_by_config_id(_FakeDb(row), "id", owner_id="u1")  # type: ignore[arg-type]
        assert llm is not None and llm.uses_pinned_client
        sent = await _outbound(llm, monkeypatch, stream=False)
        assert sent["via"] == "pinned" and sent["client_key"] == _USER_KEY
        assert _host(sent["client_base"]) == _host(_USER_URL)

    @pytest.mark.parametrize("owner_id", [None, "u2"])
    async def test_a_row_of_another_user_is_not_used(self, owner_id: str | None) -> None:
        row = _FakeRow(user_id="u1", base_url=_USER_URL, api_key=_USER_KEY)
        assert await deps.get_llm_by_config_id(_FakeDb(row), "id", owner_id=owner_id) is None  # type: ignore[arg-type]
        row = _FakeRow(user_id="u1", base_url=None, api_key=_USER_KEY)
        assert await deps.get_llm_by_config_id(_FakeDb(row), "id", owner_id=owner_id) is None  # type: ignore[arg-type]

    @pytest.mark.parametrize("owner_id", [None, "u2"])
    async def test_an_admin_row_is_used_for_any_agent(self, owner_id: str | None) -> None:
        row = _FakeRow(user_id=None, base_url="http://10.0.0.5:8000/v1", api_key=None)
        assert await deps.get_llm_by_config_id(_FakeDb(row), "id", owner_id=owner_id) is not None  # type: ignore[arg-type]

    async def test_admin_row_keeps_working_with_internal_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _FakeRow(user_id=None, base_url="http://10.0.0.5:8000/v1", api_key=None)
        llm = await deps.get_llm_by_config_id(_FakeDb(row), "id", owner_id="u1")  # type: ignore[arg-type]
        assert llm is not None and not llm.uses_pinned_client
        sent = await _outbound(llm, monkeypatch, stream=False)
        assert sent["via"] == "litellm.acompletion"
        assert sent["api_base"] == "http://10.0.0.5:8000/v1"
        assert sent["api_key"] == _SYSTEM_SECRETS["LLM_API_KEY"]


_BODY_MARKER = "ENDPOINT-RESPONSE-BODY-7c1e"


class _Endpoint(http.server.BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []
    redirect_to: str = ""
    fail_with: int = 0

    def _handle(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {}
        _Endpoint.requests.append(
            {"method": self.command, "path": self.path, "auth": self.headers.get("authorization"),
             "model": payload.get("model"), "stream": bool(payload.get("stream"))}
        )
        if self.path.startswith("/redir"):
            self.send_response(307)
            self.send_header("Location", _Endpoint.redirect_to)
            self.end_headers()
            return
        if _Endpoint.fail_with:
            body = json.dumps({"error": {"message": _BODY_MARKER, "type": "x"}}).encode()
            self.send_response(_Endpoint.fail_with)
            self.send_header("Content-Type", "application/json")
        elif payload.get("stream"):
            chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": _BODY_MARKER}, "finish_reason": "stop"}]}
            body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        else:
            body = json.dumps(
                {
                    "id": "x", "object": "chat.completion", "created": 0, "model": "m",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": _BODY_MARKER}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET = do_PUT = _handle  # noqa: N815

    def log_message(self, *a: Any) -> None:
        pass


@pytest.fixture()
def endpoint() -> Iterator[int]:
    _Endpoint.requests = []
    _Endpoint.fail_with = 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Endpoint)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def fresh_untrusted_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oc, "_UNTRUSTED_HTTP_CLIENT", None, raising=False)


@pytest.fixture()
def public_name(monkeypatch: pytest.MonkeyPatch) -> str:
    """A host name that resolves to a public address and is delivered,
    below the address-checking transport, to the loopback listener.  No
    packet leaves this machine."""
    real_getaddrinfo = socket.getaddrinfo

    def _getaddrinfo(host: Any, *a: Any, **kw: Any) -> Any:
        if host == "llm.public.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", 0))]
        return real_getaddrinfo(host, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    real_send = httpx.AsyncHTTPTransport.handle_async_request

    async def _routed(self: Any, request: httpx.Request) -> httpx.Response:
        if request.url.host == "203.0.113.9":
            request.url = request.url.copy_with(host="127.0.0.1")
        return await real_send(self, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _routed)
    return "llm.public.test"


async def _call(base_url: str, *, model: str = "m", stream: bool = False) -> str:
    """One real call (real SDKs, real sockets); the text that came back or the error."""
    from fim_one.core.model.retry import RetryConfig

    llm = get_llm_from_config({"model_name": model, "base_url": base_url, "api_key": _USER_KEY})
    assert llm is not None
    llm._retry_config = RetryConfig(max_retries=0)
    try:
        if stream:
            parts = [c.delta_content or "" async for c in llm.stream_chat([ChatMessage(role="user", content="hi")])]
            return "".join(parts)
        result = await llm.chat([ChatMessage(role="user", content="hi")])
    except Exception as exc:
        return f"ERROR {type(exc).__name__}: {exc} | {exc.__cause__!r} | {exc.__context__!r}"
    return str(result.message.content)


class TestConnectTimeChecks:
    """Real SDKs, real sockets: what actually leaves the process."""

    @pytest.mark.parametrize("stream", [False, True])
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]", "0.0.0.0", "127.1", "2130706433"])
    async def test_internal_endpoint_is_never_contacted(
        self, endpoint: int, host: str, stream: bool, fresh_untrusted_client: None
    ) -> None:
        out = await _call(f"http://{host}:{endpoint}/v1", stream=stream)
        assert out.startswith("ERROR")
        assert _BODY_MARKER not in out
        assert _Endpoint.requests == []

    @pytest.mark.parametrize("stream", [False, True])
    async def test_no_model_name_reaches_an_internal_endpoint(
        self, endpoint: int, stream: bool, fresh_untrusted_client: None
    ) -> None:
        for model in _ROUTING_MODELS:
            out = await _call(f"http://127.0.0.1:{endpoint}/v1", model=model, stream=stream)
            assert out.startswith("ERROR"), (model, out[:120])
            assert _BODY_MARKER not in out, model
        assert _Endpoint.requests == []

    @pytest.mark.parametrize("stream", [False, True])
    async def test_every_model_name_goes_to_chat_completions_with_the_users_key(
        self, endpoint: int, stream: bool, fresh_untrusted_client: None, public_name: str
    ) -> None:
        for model in _ROUTING_MODELS:
            out = await _call(f"http://{public_name}:{endpoint}/v1", model=model, stream=stream)
            assert out == _BODY_MARKER, (model, out[:200])
        assert len(_Endpoint.requests) == len(_ROUTING_MODELS)
        for seen, model in zip(_Endpoint.requests, _ROUTING_MODELS, strict=True):
            assert (seen["method"], seen["path"]) == ("POST", "/v1/chat/completions")
            assert seen["auth"] == f"Bearer {_USER_KEY}"
            assert seen["model"] == model
            assert seen["stream"] is stream

    @pytest.mark.parametrize("stream", [False, True])
    async def test_redirects_are_not_followed(
        self, endpoint: int, stream: bool, fresh_untrusted_client: None, public_name: str
    ) -> None:
        _Endpoint.redirect_to = f"http://127.0.0.1:{endpoint}/v1/chat/completions"
        out = await _call(f"http://{public_name}:{endpoint}/redir", stream=stream)
        assert out.startswith("ERROR")
        assert _BODY_MARKER not in out
        assert [r["path"] for r in _Endpoint.requests] == ["/redir/chat/completions"]

    @pytest.mark.parametrize("stream", [False, True])
    @pytest.mark.parametrize("status", [400, 401, 404, 429, 500])
    async def test_a_failing_endpoints_body_is_not_repeated(
        self, endpoint: int, status: int, stream: bool, fresh_untrusted_client: None, public_name: str
    ) -> None:
        _Endpoint.fail_with = status
        out = await _call(f"http://{public_name}:{endpoint}/v1", stream=stream)
        assert out.startswith("ERROR UntrustedEndpointCallError")
        assert str(status) in out
        assert _BODY_MARKER not in out
        assert _USER_KEY not in out


class TestAgentApiRefusesBadEndpoints:
    @pytest.mark.parametrize(
        "config",
        [
            {"model_name": "m", "base_url": _USER_URL},
            {"model_name": "m", "base_url": "http://127.0.0.1:8000/v1", "api_key": _USER_KEY},
            {"model_name": "m", "base_url": "http://169.254.169.254/", "api_key": _USER_KEY},
            {"model_name": "m", "base_url": "http://u:p@8.8.8.8/v1", "api_key": _USER_KEY},
            {"model_name": "m", "provider": "bedrock"},
            {"base_url": _USER_URL},  # no model name: still not storable without a key
        ],
    )
    async def test_save_is_refused(self, config: dict[str, Any]) -> None:
        from fim_one.web.api.agents import _validate_model_endpoint
        from fim_one.web.exceptions import AppError

        with pytest.raises(AppError) as excinfo:
            await _validate_model_endpoint(config)
        assert excinfo.value.status_code == 400
        assert not _carries_system_secret(excinfo.value.detail)

    @pytest.mark.parametrize(
        "config",
        [
            None,
            {},
            {"temperature": 0.2, "model_config_id": "abc"},
            {"model_name": "gpt-4o"},
            {"model_name": "m", "base_url": "https://8.8.8.8/v1", "api_key": _USER_KEY},
            {"model_name": "m", "provider": "anthropic"},
        ],
    )
    async def test_ordinary_settings_are_accepted(self, config: Any) -> None:
        from fim_one.web.api.agents import _validate_model_endpoint

        await _validate_model_endpoint(config)

    def test_unchanged_endpoint_settings_do_not_block_other_edits(self) -> None:
        from fim_one.web.api.agents import _model_routing

        stored = {"model_name": "m", "base_url": _USER_URL, "temperature": 0.1}
        resent = {**stored, "temperature": 0.9, "notifications": {"on_complete": {}}}
        assert _model_routing(resent) == _model_routing(stored)
        assert _model_routing({**stored, "base_url": "https://other.example"}) != _model_routing(stored)
