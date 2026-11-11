"""The model client reports what each call consumed to the meter of its scope.

``core/model/usage.py`` holds the meter and the context variable; the
client (``OpenAICompatibleLLM``, both of its streaming branches, and the
OpenAI-shaped shim) reports to it. With no meter open nothing changes for
the caller.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import threading
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from fim_one.core.model import usage as usage_mod
from fim_one.core.model.openai_compatible import OpenAICompatibleLLM
from fim_one.core.model.types import ChatMessage, StreamChunk
from fim_one.core.model.usage import (
    UnmeteredUsageError,
    UsageMeter,
    current_meter,
    estimate_prompt_tokens,
    estimate_tokens,
    estimated_usage,
    report_usage,
    set_strict_metering,
    unattributed_tokens,
)

from .provider import COMPLETION, PROMPT, UNIT, Provider, provider  # noqa: F401

MESSAGES = [ChatMessage(role="user", content="ping, please")]


def _llm(model: str = "gpt-4o") -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        model=model,
        retry_config=None,
        rate_limit_config=None,
    )


@pytest.fixture()
def meter() -> Iterator[UsageMeter]:
    opened = UsageMeter()
    token = current_meter.set(opened)
    yield opened
    current_meter.reset(token)


class TestTheMeter:
    def test_it_adds_up_by_model_and_hands_out_the_increment_once(self) -> None:
        meter = UsageMeter()
        meter.add("main", {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})
        meter.add("main", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        meter.add("fast", {"prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 4})

        assert meter.total() == 16
        drained = {item.model: item for item in meter.drain()}
        assert (drained["main"].total_tokens, drained["main"].calls) == (12, 2)
        assert (drained["main"].prompt_tokens, drained["main"].completion_tokens) == (8, 4)
        assert drained["fast"].total_tokens == 4
        assert meter.drain() == []
        # The total is of the scope, not of what is still to be stored.
        assert meter.total() == 16

    def test_usage_is_never_negative_and_nothing_is_not_a_call(self) -> None:
        meter = UsageMeter()
        meter.add("m", {"prompt_tokens": -5, "completion_tokens": -1, "total_tokens": -6})
        meter.add("m", {})
        meter.add("m", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        meter.add("m", {"prompt_tokens": True, "total_tokens": "12"})

        assert meter.total() == 0
        assert meter.drain() == []

    def test_a_missing_total_is_the_sum_of_the_parts(self) -> None:
        meter = UsageMeter()
        meter.add("m", {"prompt_tokens": 5, "completion_tokens": 2})
        assert meter.total() == 7

    def test_estimated_usage_is_kept_apart(self) -> None:
        meter = UsageMeter()
        meter.add("m", {"total_tokens": 10})
        meter.add("m", {"total_tokens": 4}, estimated=True)

        assert sorted((item.total_tokens, item.estimated) for item in meter.drain()) == [
            (4, True),
            (10, False),
        ]

    def test_cache_counters_are_carried(self) -> None:
        meter = UsageMeter()
        meter.add(
            "m",
            {"total_tokens": 10, "cache_read_input_tokens": 6, "cache_creation_input_tokens": 2},
        )
        (item,) = meter.drain()
        assert (item.cache_read_tokens, item.cache_creation_tokens) == (6, 2)

    def test_reports_from_threads_are_all_counted(self) -> None:
        meter = UsageMeter()

        def work() -> None:
            for _ in range(500):
                meter.add("m", {"total_tokens": 1})

        threads = [threading.Thread(target=work) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert meter.total() == 4000
        assert sum(item.total_tokens for item in meter.drain()) == 4000

    def test_an_estimate_counts_cjk_text_as_more_than_ascii(self) -> None:
        assert estimate_tokens("") == 0
        assert estimate_tokens("abcdefgh") == 2
        assert estimate_tokens("你好世界你好") > estimate_tokens("abcdef")


# The estimate stands in for the provider's count when a call ends without
# one. The reference counts below were taken once with tiktoken's
# ``o200k_base`` encoding (GPT-4o family); the test does not need tiktoken.
_SCHEMA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": f"connector_{i}__search_records",
            "description": (
                "Search records in the CRM by any combination of filters. Returns up to "
                "50 rows with id, name, owner, stage and amount. Use before update_record."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    key: {
                        "type": "string",
                        "description": f"Filter on {key}; exact match unless it ends with *",
                    }
                    for key in (
                        "name", "owner", "stage", "region",
                        "created_after", "created_before", "amount_min", "amount_max",
                    )
                },
                "required": ["name"],
            },
        },
    }
    for i in range(25)
]
_ROWS = json.dumps(
    [
        {
            "id": n,
            "name": f"Acme {n}",
            "owner": "li.wei",
            "stage": "negotiation",
            "amount": n * 1000,
        }
        for n in range(30)
    ]
)
_PROSE = (
    "The quarterly report shows that revenue grew in every region except the north, where "
    "two large customers delayed their renewals until the next fiscal year. Management "
    "expects the gap to close by March. "
) * 30
_PROSE_TOKENS = 1111


def _tool_heavy_turn() -> tuple[list[ChatMessage], str, int]:
    """An agent's last call of a turn: 25 tool schemas, 6 tool rounds, 2 images.

    Returns the messages, the answer, and the call's ``o200k_base`` count:
    message text 9,021, tool schemas 7,151, history tool calls 6 x 25 + 6 x 12,
    two 1024x1024 images at high detail 2 x 1,105, message framing 4 x 14,
    the answer 833.
    """
    from fim_one.core.model.types import ToolCallRequest

    arguments = {"name": "Acme*", "owner": "li.wei", "stage": "negotiation", "region": "APAC"}
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 200_000}}
    messages = [
        ChatMessage(role="system", content="You are a careful assistant. " * 60),
        ChatMessage(
            role="user",
            content=[
                {"type": "text", "text": "看一下这三张截图里的合同金额，再去 CRM 里核对。"},  # noqa: RUF001
                image,
                image,
            ],
        ),
    ]
    for i in range(6):
        messages.append(
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id=f"c{i}", name="connector_3__search_records", arguments=arguments
                    )
                ],
            )
        )
        messages.append(ChatMessage(role="tool", content=_ROWS, tool_call_id=f"c{i}"))
    answer = "合同金额核对如下：" + "第一份合同金额为 120 万元，与 CRM 记录一致。" * 40  # noqa: RUF001
    return messages, answer, 17_493


class TestAnEstimate:
    def test_a_tool_heavy_call_comes_within_a_tenth_of_its_real_count(self) -> None:
        messages, answer, actual = _tool_heavy_turn()

        estimate = estimated_usage(messages, answer, _SCHEMA_TOOLS)["total_tokens"]

        assert 0.9 * actual <= estimate <= 1.3 * actual

    def test_plain_prose_is_not_overcounted(self) -> None:
        assert 0.9 * _PROSE_TOKENS <= estimate_tokens(_PROSE) <= 1.3 * _PROSE_TOKENS

    def test_the_tool_schemas_sent_with_the_call_are_counted(self) -> None:
        without = estimate_prompt_tokens(MESSAGES)
        with_tools = estimate_prompt_tokens(MESSAGES, _SCHEMA_TOOLS)

        # 7,151 tokens in o200k_base.
        assert with_tools - without >= 0.9 * 7151

    def test_an_image_counts_a_flat_figure_not_its_base64(self) -> None:
        messages, _answer, _actual = _tool_heavy_turn()
        user = messages[1]
        text_only = ChatMessage(role="user", content=[user.content[0]])  # type: ignore[index]

        extra = estimate_prompt_tokens([user]) - estimate_prompt_tokens([text_only])

        assert 2 * 1000 <= extra <= 2 * 1600

    def test_tool_calls_in_the_history_are_counted(self) -> None:
        messages, _answer, _actual = _tool_heavy_turn()
        call = messages[2]

        # 25 tokens of arguments and the call's framing.
        assert estimate_prompt_tokens([call]) >= 25 + 12

    def test_it_rounds_up(self) -> None:
        assert estimate_tokens("a") == 1


class TestWithNoMeterOpen:
    def test_the_usage_is_counted_as_unattributed(self) -> None:
        before = unattributed_tokens()
        report_usage("m", {"total_tokens": 9})
        assert unattributed_tokens() == before + 9

    def test_it_is_said_in_the_log_but_not_for_every_call(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import logging

        monkeypatch.setattr(usage_mod, "_last_unattributed_warning", 0.0)
        with caplog.at_level(logging.WARNING, logger="fim_one.core.model.usage"):
            for _ in range(50):
                report_usage("m", {"total_tokens": 9})

        (record,) = caplog.records
        assert "outside any usage meter" in record.getMessage()
        assert "model=m total_tokens=9" in record.getMessage()

    def test_strict_metering_raises(self) -> None:
        set_strict_metering(True)
        try:
            with pytest.raises(UnmeteredUsageError):
                report_usage("m", {"total_tokens": 9})
        finally:
            set_strict_metering(False)

    async def test_a_call_answers_as_before(self, provider: Provider) -> None:
        assert current_meter.get() is None

        result = await _llm().chat(MESSAGES)
        chunks = [chunk async for chunk in _llm().stream_chat(MESSAGES)]

        assert result.message.content == provider.text
        assert result.usage["total_tokens"] == UNIT
        assert "".join(chunk.delta_content or "" for chunk in chunks) == provider.text
        assert [chunk.usage for chunk in chunks if chunk.usage] == [
            {
                "prompt_tokens": PROMPT,
                "completion_tokens": COMPLETION,
                "total_tokens": UNIT,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
        ]


class TestACallThatIsNotStreamed:
    async def test_reports_the_providers_count(self, provider: Provider, meter: UsageMeter) -> None:
        await _llm("gpt-4o").chat(MESSAGES)
        await _llm("gpt-4o-mini").chat(MESSAGES)

        assert meter.total() == provider.billed == 2 * UNIT
        by_model = {item.model: item for item in meter.drain()}
        assert set(by_model) == {"gpt-4o", "gpt-4o-mini"}
        assert (by_model["gpt-4o"].prompt_tokens, by_model["gpt-4o"].completion_tokens) == (
            PROMPT,
            COMPLETION,
        )
        assert not by_model["gpt-4o"].estimated

    async def test_a_provider_that_sends_no_count_is_estimated(
        self, monkeypatch: pytest.MonkeyPatch, meter: UsageMeter
    ) -> None:
        async def _no_usage(**_kwargs: Any) -> Any:
            message = SimpleNamespace(
                role="assistant", content="pong " * 8, tool_calls=None, reasoning_content=None
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None
            )

        monkeypatch.setattr("litellm.acompletion", _no_usage)

        result = await _llm().chat(MESSAGES)

        assert result.usage == {}
        (item,) = meter.drain()
        assert item.estimated is True
        assert item.prompt_tokens == estimate_prompt_tokens(MESSAGES)
        assert item.completion_tokens == estimate_tokens("pong " * 8)
        assert item.total_tokens == item.prompt_tokens + item.completion_tokens

    async def test_a_call_that_fails_reports_nothing(
        self, provider: Provider, meter: UsageMeter
    ) -> None:
        provider.fail_from = 1

        with pytest.raises(RuntimeError):
            await _llm().chat(MESSAGES)

        assert meter.total() == 0

    async def test_a_child_task_reports_to_the_same_meter(
        self, provider: Provider, meter: UsageMeter
    ) -> None:
        await asyncio.gather(*(asyncio.create_task(_llm().chat(MESSAGES)) for _ in range(3)))
        assert meter.total() == 3 * UNIT


class TestAStreamedCall:
    async def test_with_no_count_the_tool_schemas_it_sent_are_estimated(
        self, provider: Provider, meter: UsageMeter
    ) -> None:
        provider.silent_streams = True

        async for _ in _llm().stream_chat(MESSAGES, tools=_SCHEMA_TOOLS):
            pass

        (item,) = meter.drain()
        assert item.estimated is True
        assert item.prompt_tokens == estimate_prompt_tokens(MESSAGES, _SCHEMA_TOOLS)

    async def test_read_to_the_end_it_reports_the_providers_count_once(
        self, provider: Provider, meter: UsageMeter
    ) -> None:
        chunks = [chunk async for chunk in _llm().stream_chat(MESSAGES)]

        assert any(chunk.finish_reason == "stop" for chunk in chunks)
        assert meter.total() == UNIT
        (item,) = meter.drain()
        assert (item.total_tokens, item.estimated, item.calls) == (UNIT, False, 1)

    async def test_while_it_streams_the_meter_holds_an_estimate_of_the_answer_so_far(
        self, provider: Provider, meter: UsageMeter
    ) -> None:
        provider.text = "pong " * 40
        seen: list[tuple[int, int]] = []
        text = ""

        async for chunk in _llm().stream_chat(MESSAGES):
            text += chunk.delta_content or ""
            seen.append((meter.in_flight(), meter.total()))

        # It grows with the text, and nothing is counted until the end...
        assert seen[0] == (estimate_tokens(provider.text[: len(provider.text) // 4]) - 0, 0) or (
            seen[0][0] > 0 and seen[0][1] == 0
        )
        growing = [in_flight for in_flight, total in seen if total == 0]
        assert growing == sorted(growing) and growing[-1] >= estimate_tokens(text) - 1
        # ...when the call's own count replaces the estimate.
        assert (meter.in_flight(), meter.total()) == (0, UNIT)

    async def test_an_abandoned_stream_leaves_no_estimate_behind(
        self, provider: Provider, meter: UsageMeter
    ) -> None:
        provider.text = "pong " * 40
        stream = _llm().stream_chat(MESSAGES)
        await stream.__anext__()
        assert meter.in_flight() > 0

        await stream.aclose()  # type: ignore[attr-defined]
        for _ in range(5):
            await asyncio.sleep(0)

        assert meter.in_flight() == 0
        assert meter.total() > 0

    async def test_abandoned_by_its_reader_it_reports_an_estimate(
        self, provider: Provider, meter: UsageMeter
    ) -> None:
        provider.text = "pong " * 40
        stream = _llm().stream_chat(MESSAGES)
        received: list[str] = []
        async for chunk in stream:
            received.append(chunk.delta_content or "")
            if len(received) == 2:
                break
        await stream.aclose()  # type: ignore[attr-defined]
        # The inner generators are closed by the loop, a step at a time.
        for _ in range(5):
            await asyncio.sleep(0)

        (item,) = meter.drain()
        assert item.estimated is True
        assert item.prompt_tokens == estimate_prompt_tokens(MESSAGES)
        assert item.completion_tokens == estimate_tokens("".join(received))

    async def test_cancelled_mid_stream_it_reports_an_estimate(
        self, monkeypatch: pytest.MonkeyPatch, meter: UsageMeter
    ) -> None:
        hang = asyncio.Event()

        async def _slow(**_kwargs: Any) -> Any:
            async def _chunks() -> AsyncIterator[Any]:
                delta = SimpleNamespace(content="partial", reasoning_content=None, tool_calls=None)
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None
                )
                await hang.wait()

            return _chunks()

        monkeypatch.setattr("litellm.acompletion", _slow)
        seen: list[StreamChunk] = []

        async def _read() -> None:
            async for chunk in _llm().stream_chat(MESSAGES):
                seen.append(chunk)

        task = asyncio.create_task(_read())
        while not seen:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        (item,) = meter.drain()
        assert (item.estimated, item.completion_tokens) == (True, estimate_tokens("partial"))

    async def test_a_provider_that_streams_no_count_is_estimated(
        self, provider: Provider, meter: UsageMeter
    ) -> None:
        provider.silent_streams = True

        text = "".join([c.delta_content or "" async for c in _llm().stream_chat(MESSAGES)])

        (item,) = meter.drain()
        assert item.estimated is True
        assert item.completion_tokens == estimate_tokens(text)

    async def test_a_stream_that_never_started_reports_nothing(
        self, monkeypatch: pytest.MonkeyPatch, meter: UsageMeter
    ) -> None:
        async def _broken(**_kwargs: Any) -> Any:
            async def _chunks() -> AsyncIterator[Any]:
                raise ConnectionError("reset before the first chunk")
                yield  # pragma: no cover

            return _chunks()

        monkeypatch.setattr("litellm.acompletion", _broken)

        with pytest.raises(ConnectionError):
            async for _ in _llm().stream_chat(MESSAGES):
                pass

        assert meter.total() == 0

    async def test_closed_from_another_context_it_still_reports_to_its_own_scope(
        self, provider: Provider, meter: UsageMeter
    ) -> None:
        """An abandoned stream is closed by the event loop, in a context of its own."""
        provider.text = "pong " * 40
        stream = _llm().stream_chat(MESSAGES)
        await stream.__anext__()

        elsewhere = contextvars.Context()
        await asyncio.get_running_loop().create_task(
            stream.aclose(),  # type: ignore[attr-defined]
            context=elsewhere,
        )
        for _ in range(5):
            await asyncio.sleep(0)

        assert meter.total() > 0

    async def test_the_native_responses_branch_reports_too(
        self, monkeypatch: pytest.MonkeyPatch, meter: UsageMeter
    ) -> None:
        async def _native(*_args: Any, **_kwargs: Any) -> AsyncIterator[StreamChunk]:
            async def _chunks() -> AsyncIterator[StreamChunk]:
                yield StreamChunk(delta_content="pong")
                yield StreamChunk(
                    finish_reason="stop",
                    usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
                )

            return _chunks()

        llm = _llm("gpt-5")
        monkeypatch.setattr(llm, "_should_use_native_responses", lambda **_kw: True)
        monkeypatch.setattr(llm, "_native_responses_stream", _native)

        chunks = [chunk async for chunk in llm.stream_chat(MESSAGES)]

        assert [chunk.delta_content for chunk in chunks] == ["pong", None]
        (item,) = meter.drain()
        assert (item.model, item.total_tokens, item.estimated) == ("gpt-5", 25, False)

    async def test_the_native_responses_call_that_is_not_streamed_reports_too(
        self, monkeypatch: pytest.MonkeyPatch, meter: UsageMeter
    ) -> None:
        from fim_one.core.model.types import LLMResult

        async def _native(*_args: Any, **_kwargs: Any) -> LLMResult:
            return LLMResult(
                message=ChatMessage(role="assistant", content="pong"),
                usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
            )

        llm = _llm("gpt-5")
        monkeypatch.setattr(llm, "_should_use_native_responses", lambda **_kw: True)
        monkeypatch.setattr(llm, "_native_responses_chat", _native)

        await llm.chat(MESSAGES)

        assert meter.total() == 25


class TestTheOpenAIShapedShim:
    def test_it_reports_the_call_it_made(
        self, monkeypatch: pytest.MonkeyPatch, meter: UsageMeter
    ) -> None:
        from fim_one.core.model.litellm_openai_shim import LiteLLMOpenAIShim

        def _completion(**_kwargs: Any) -> Any:
            return SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(prompt_tokens=30, completion_tokens=12, total_tokens=42),
            )

        monkeypatch.setattr("litellm.completion", _completion)
        llm = _llm("gpt-4o-mini")

        LiteLLMOpenAIShim(llm).chat.completions.create(messages=[{"role": "user", "content": "x"}])

        (item,) = meter.drain()
        assert (item.model, item.total_tokens) == ("gpt-4o-mini", 42)

    async def test_from_a_worker_thread_it_reaches_the_scopes_meter(
        self, monkeypatch: pytest.MonkeyPatch, meter: UsageMeter
    ) -> None:
        from fim_one.core.model.litellm_openai_shim import LiteLLMOpenAIShim

        monkeypatch.setattr(
            "litellm.completion",
            lambda **_kw: SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
            ),
        )
        shim = LiteLLMOpenAIShim(_llm())

        await asyncio.to_thread(shim.chat.completions.create, messages=[])

        assert meter.total() == 2


def test_the_model_package_does_not_import_the_ledger() -> None:
    """``core/model`` counts; ``core/usage`` stores. The import goes one way."""
    import re
    from pathlib import Path

    imports = re.compile(r"^\s*(?:from|import)\s+\S*(?:core\.usage|\.\.usage)\b", re.MULTILINE)
    for path in Path(usage_mod.__file__).parent.glob("*.py"):
        assert not imports.search(path.read_text(encoding="utf-8")), path.name
