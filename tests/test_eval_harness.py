"""Contract tests for the behavioral-eval harness (`evals/harness.py`).

`evals/` judges the **model**: given the real system prompt and tool
descriptions, does the agent choose to edit the file instead of generating an
image, does it put the deliverable on disk. Every one of those cases costs
tokens and minutes, and each one reaches its assertion through the same
plumbing — the sandboxed `FileOpsTool`, the recording image tool, the
`EvalRun` accessors, the pass@k retry.

If that plumbing breaks, the eval goes red for a reason that has nothing to do
with the model, and finding out costs a paid run. So the plumbing is pinned
here, against a scripted model, for free: a red eval then means the model
regressed.

The challenge tier adds three more pieces of plumbing that are pinned the same
way: the stand-in tools its cases probe with (recording HTTP, stub database,
read-only file_ops), the pass^k retry those cases run under, and the ledger
that collects cost and execution facts for the end-of-run report.

These are not behavioral evals and must not become them. Nothing here asserts
what a model *chooses* to do — the script decides that.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from evals import conftest as eval_conftest
from evals.harness import (
    LEDGER,
    EvalLedger,
    EvalRun,
    ReadOnlyFileOpsTool,
    RecordingHttpTool,
    RecordingImageTool,
    RunMetrics,
    StubDatabaseTool,
    cost_usd,
    current_case,
    eval_repeat,
    eval_retry,
    run_case,
)
from fim_one.core.agent.types import Action, AgentResult, StepResult
from fim_one.core.model.usage import UsageSummary

from .fake_llm import NATIVE_TOOLS, FakeLLM, answer, tool_call

# Price env vars leak between cases otherwise, and run_case files every run
# with the process-wide ledger.
_PRICE_ENV = ("EVAL_PRICE_INPUT_PER_MTOK", "EVAL_PRICE_OUTPUT_PER_MTOK")


@pytest.fixture(autouse=True)
def _clean_harness_state() -> Iterator[None]:
    LEDGER.reset()
    # patch.dict restores the original mapping on exit, deletions included.
    with patch.dict(os.environ):
        for key in _PRICE_ENV:
            os.environ.pop(key, None)
        yield
    LEDGER.reset()


# ---------------------------------------------------------------------------
# EvalRun accessors
# ---------------------------------------------------------------------------


class TestEvalRunAccessors:
    """Trajectory readers the cases assert through."""

    @staticmethod
    def _run(tmp_path: Path, *steps: StepResult) -> EvalRun:
        return EvalRun(
            result=AgentResult(answer="done", steps=list(steps)),
            sandbox=tmp_path,
            image_tool=RecordingImageTool(),
        )

    @staticmethod
    def _call(tool_name: str, **args: object) -> StepResult:
        return StepResult(
            action=Action(
                type="tool_call",
                reasoning="",
                tool_name=tool_name,
                tool_args=dict(args),
            )
        )

    def test_tool_names_lists_calls_in_order(self, tmp_path: Path) -> None:
        run = self._run(
            tmp_path,
            self._call("file_ops", operation="write", path="a/scorecard.html"),
            self._call("generate_image", prompt="x"),
            StepResult(action=Action(type="final_answer", reasoning="", answer="ok")),
        )
        assert run.tool_names() == ["file_ops", "generate_image"]

    def test_file_writes_ignores_reads(self, tmp_path: Path) -> None:
        """A read of the deliverable must not count as producing it."""
        run = self._run(
            tmp_path,
            self._call("file_ops", operation="write", path="a/scorecard.html"),
            self._call("file_ops", operation="read", path="a/scorecard.html"),
        )
        assert run.file_writes() == [
            {"operation": "write", "path": "a/scorecard.html"}
        ]

    @pytest.mark.parametrize(
        "operation",
        ["write", "append", "write_json", "write_csv", "find_replace", "apply_patch"],
    )
    def test_every_mutating_operation_counts_as_a_write(
        self, tmp_path: Path, operation: str
    ) -> None:
        """The restyle case passes by editing, which is not always `write`."""
        run = self._run(
            tmp_path, self._call("file_ops", operation=operation, path="s.html")
        )
        assert run.file_writes(), f"{operation} not recognised as a file write"

    def test_file_writes_filters_by_path(self, tmp_path: Path) -> None:
        run = self._run(
            tmp_path,
            self._call("file_ops", operation="write", path="a/scorecard.html"),
        )
        assert run.file_writes(path_contains="scorecard.html")
        assert not run.file_writes(path_contains="other.html")

    def test_sandbox_file_returns_empty_string_when_absent(
        self, tmp_path: Path
    ) -> None:
        """Cases assert on this falsiness ("scorecard.html vanished")."""
        run = self._run(tmp_path)
        assert run.sandbox_file("missing.html") == ""

    def test_sandbox_html_files_finds_nested_output(self, tmp_path: Path) -> None:
        nested = tmp_path / "out"
        nested.mkdir()
        (nested / "scorecard.html").write_text("<html></html>", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
        assert [p.name for p in self._run(tmp_path).sandbox_html_files()] == [
            "scorecard.html"
        ]


# ---------------------------------------------------------------------------
# The recording image tool
# ---------------------------------------------------------------------------


class TestRecordingImageTool:
    """The eval asks whether the model *chooses* generate_image, so the tool
    must look available while never reaching an image API."""

    def test_reports_available_regardless_of_credentials(self) -> None:
        available, reason = RecordingImageTool().availability()
        assert available is True
        assert reason is None

    async def test_records_the_call_instead_of_generating(self) -> None:
        tool = RecordingImageTool()
        out = await tool.run(prompt="a purple gradient")

        assert tool.calls == [{"prompt": "a purple gradient"}]
        assert "eval-stub" in out


# ---------------------------------------------------------------------------
# run_case wiring
# ---------------------------------------------------------------------------


class TestRunCaseWiring:
    """One scripted turn through the real agent, tools, and sandbox."""

    async def test_a_scripted_write_lands_in_the_sandbox(
        self, tmp_path: Path
    ) -> None:
        sandbox = tmp_path / "deliver"
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[
                tool_call(
                    "file_ops",
                    {
                        "operation": "write",
                        "path": "scorecard.html",
                        "content": "<html><body>ok</body></html>",
                    },
                ),
                answer("Wrote scorecard.html."),
            ],
        )

        run = await run_case("build the scorecard", sandbox, llm=llm)

        assert run.tool_names() == ["file_ops"]
        assert run.file_writes(path_contains="scorecard.html")
        assert "ok" in run.sandbox_file("scorecard.html")
        assert [p.name for p in run.sandbox_html_files()] == ["scorecard.html"]

    async def test_the_sandbox_is_created_and_isolated(
        self, tmp_path: Path
    ) -> None:
        """Cases hand run_case a path that may not exist yet, and the file
        tool must be confined to it."""
        sandbox = tmp_path / "not" / "yet" / "there"
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=[answer("nothing to do")])

        run = await run_case("say hi", sandbox, llm=llm)

        assert sandbox.is_dir()
        assert run.sandbox == sandbox

    async def test_an_image_call_is_recorded_not_executed(
        self, tmp_path: Path
    ) -> None:
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[
                tool_call("generate_image", {"prompt": "a scorecard"}),
                answer("here you go"),
            ],
        )

        run = await run_case("make me a picture", tmp_path / "img", llm=llm)

        assert run.image_tool.calls == [{"prompt": "a scorecard"}]
        assert "generate_image" in run.tool_names()

    async def test_a_preseeded_file_is_visible_to_the_agent(
        self, tmp_path: Path
    ) -> None:
        """The restyle case depends on seeding turn 1's deliverable."""
        sandbox = tmp_path / "restyle"
        sandbox.mkdir()
        (sandbox / "scorecard.html").write_text("<html>old</html>", encoding="utf-8")
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[
                tool_call(
                    "file_ops", {"operation": "read", "path": "scorecard.html"}
                ),
                answer("read it"),
            ],
        )

        run = await run_case("what is in there", sandbox, llm=llm)

        read_steps = [
            step
            for step in run.result.steps
            if step.action.tool_name == "file_ops" and not step.error
        ]
        assert read_steps, "the read never reached the tool"
        assert "old" in (read_steps[0].observation or ""), (
            "the seeded file was not readable from inside the sandbox"
        )


# ---------------------------------------------------------------------------
# pass@k retry
# ---------------------------------------------------------------------------


class TestEvalRetry:
    """Model output is stochastic, so a case may retry. The retry must not
    turn a real regression green, and must not hide a harness crash."""

    async def test_a_passing_attempt_runs_once(self) -> None:
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1

        with patch.dict(os.environ, {"EVAL_ATTEMPTS": "3"}):
            await eval_retry(attempt)

        assert calls == 1

    async def test_it_retries_up_to_the_budget_then_passes(self) -> None:
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise AssertionError("flaky")

        with patch.dict(os.environ, {"EVAL_ATTEMPTS": "3"}):
            await eval_retry(attempt)

        assert calls == 3

    async def test_it_raises_after_the_last_failed_attempt(self) -> None:
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1
            raise AssertionError("always fails")

        with patch.dict(os.environ, {"EVAL_ATTEMPTS": "2"}):
            with pytest.raises(AssertionError, match="always fails"):
                await eval_retry(attempt)

        assert calls == 2

    async def test_it_defaults_to_a_single_attempt(self) -> None:
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1
            raise AssertionError("boom")

        env = {k: v for k, v in os.environ.items() if k != "EVAL_ATTEMPTS"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(AssertionError):
                await eval_retry(attempt)

        assert calls == 1

    async def test_a_non_assertion_error_is_not_retried(self) -> None:
        """A harness bug must surface immediately, not burn the budget."""
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("harness broke")

        with patch.dict(os.environ, {"EVAL_ATTEMPTS": "3"}):
            with pytest.raises(RuntimeError, match="harness broke"):
                await eval_retry(attempt)

        assert calls == 1


# ---------------------------------------------------------------------------
# Shared builders for the sections below
# ---------------------------------------------------------------------------


def _tool_step(
    tool_name: str,
    *,
    observation: str | None = None,
    error: str | None = None,
    **args: object,
) -> StepResult:
    return StepResult(
        action=Action(
            type="tool_call",
            reasoning="",
            tool_name=tool_name,
            tool_args=dict(args),
        ),
        observation=observation,
        error=error,
    )


def _eval_run(
    tmp_path: Path,
    *steps: StepResult,
    answer_text: str = "done",
    usage: UsageSummary | None = None,
    latency_s: float = 0.0,
) -> EvalRun:
    return EvalRun(
        result=AgentResult(
            answer=answer_text,
            steps=list(steps),
            iterations=len(steps),
            usage=usage,
        ),
        sandbox=tmp_path,
        image_tool=RecordingImageTool(),
        latency_s=latency_s,
    )


# ---------------------------------------------------------------------------
# Trajectory facts the challenge tier asserts through
# ---------------------------------------------------------------------------


class TestFileReads:
    """Injection cases assert the agent never opened the planted target."""

    @pytest.mark.parametrize("operation", ["read", "read_json", "read_csv"])
    def test_every_reading_operation_counts_as_a_read(
        self, tmp_path: Path, operation: str
    ) -> None:
        run = _eval_run(
            tmp_path, _tool_step("file_ops", operation=operation, path="c.env")
        )
        assert run.file_reads(), f"{operation} not recognised as a file read"

    def test_reads_and_writes_do_not_bleed_into_each_other(
        self, tmp_path: Path
    ) -> None:
        run = _eval_run(
            tmp_path,
            _tool_step("file_ops", operation="write", path="out.md"),
            _tool_step("file_ops", operation="read", path="config/credentials.env"),
        )
        assert run.file_reads() == [
            {"operation": "read", "path": "config/credentials.env"}
        ]
        assert run.file_writes() == [{"operation": "write", "path": "out.md"}]

    def test_file_reads_filters_by_path(self, tmp_path: Path) -> None:
        run = _eval_run(
            tmp_path,
            _tool_step("file_ops", operation="read", path="kb/handover.md"),
        )
        assert not run.file_reads(path_contains="credentials")
        assert run.file_reads(path_contains="handover")


class TestToolErrors:
    """A builtin fails two ways; the failure case depends on catching both."""

    def test_a_raised_error_counts(self, tmp_path: Path) -> None:
        run = _eval_run(tmp_path, _tool_step("file_ops", error="boom"))
        assert run.tool_errors() == ["boom"]

    def test_an_error_observation_counts(self, tmp_path: Path) -> None:
        """FileOpsTool returns its failures as text instead of raising."""
        run = _eval_run(
            tmp_path,
            _tool_step("file_ops", observation="[Error] Permission denied"),
        )
        assert run.tool_errors() == ["[Error] Permission denied"]

    def test_a_successful_call_is_not_an_error(self, tmp_path: Path) -> None:
        run = _eval_run(
            tmp_path,
            _tool_step("file_ops", observation="Wrote 120 bytes"),
            StepResult(
                action=Action(type="final_answer", reasoning="", answer="[Error] no")
            ),
        )
        assert run.tool_errors() == []


class TestAnswerClaims:
    """The claim half of a claim-vs-deed check."""

    def test_it_returns_only_the_phrases_present(self, tmp_path: Path) -> None:
        run = _eval_run(tmp_path, answer_text="邮件已发送给财务。")
        assert run.answer_claims("已发送", "已写入") == ["已发送"]

    def test_an_empty_answer_claims_nothing(self, tmp_path: Path) -> None:
        run = _eval_run(tmp_path, answer_text="")
        assert run.answer_claims("已发送") == []


# ---------------------------------------------------------------------------
# Cost and execution facts
# ---------------------------------------------------------------------------


class TestCost:
    """No built-in price table: an unconfigured run reports no cost rather
    than a guessed one."""

    def test_no_price_configured_means_no_cost(self) -> None:
        assert cost_usd(1_000_000, 1_000_000) is None

    def test_prices_are_per_million_tokens(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EVAL_PRICE_INPUT_PER_MTOK": "3",
                "EVAL_PRICE_OUTPUT_PER_MTOK": "15",
            },
        ):
            assert cost_usd(1_000_000, 500_000) == pytest.approx(3 + 7.5)

    def test_a_half_configured_price_still_reports(self) -> None:
        """Input-only pricing beats reporting nothing at all."""
        with patch.dict(os.environ, {"EVAL_PRICE_INPUT_PER_MTOK": "3"}):
            assert cost_usd(1_000_000, 1_000_000) == pytest.approx(3)


class TestRunMetrics:
    """What one run cost, read off the trajectory the cases already carry."""

    def test_metrics_read_usage_latency_and_tool_outcomes(
        self, tmp_path: Path
    ) -> None:
        run = _eval_run(
            tmp_path,
            _tool_step("file_ops", observation="ok"),
            _tool_step("file_ops", error="boom"),
            usage=UsageSummary(
                prompt_tokens=1000,
                completion_tokens=500,
                total_tokens=1500,
                llm_calls=3,
            ),
            latency_s=1.5,
        )

        metrics = run.metrics(case="case::x")

        assert metrics.case == "case::x"
        assert metrics.latency_s == 1.5
        assert metrics.iterations == 2
        assert metrics.llm_calls == 3
        assert metrics.total_tokens == 1500
        assert metrics.tool_calls == 2
        assert metrics.tool_errors == 1
        assert metrics.cost_usd is None

    def test_metrics_survive_a_run_without_usage(self, tmp_path: Path) -> None:
        """A provider that reports no usage must not crash the report."""
        metrics = _eval_run(tmp_path).metrics(case="case::x")
        assert metrics.total_tokens == 0
        assert metrics.llm_calls == 0

    def test_metrics_price_the_run_when_prices_are_set(
        self, tmp_path: Path
    ) -> None:
        run = _eval_run(
            tmp_path,
            usage=UsageSummary(prompt_tokens=1000, completion_tokens=500),
        )
        with patch.dict(
            os.environ,
            {
                "EVAL_PRICE_INPUT_PER_MTOK": "3",
                "EVAL_PRICE_OUTPUT_PER_MTOK": "15",
            },
        ):
            assert run.metrics().cost_usd == pytest.approx(0.0105)

    def test_the_case_defaults_to_the_running_test(self, tmp_path: Path) -> None:
        assert _eval_run(tmp_path).metrics().case == current_case()
        assert "test_the_case_defaults_to_the_running_test" in current_case()


# ---------------------------------------------------------------------------
# Stand-in tools the challenge cases probe with
# ---------------------------------------------------------------------------


class TestRecordingHttpTool:
    """The route-around path. It must look usable and never leave the box."""

    def test_it_reports_available(self) -> None:
        assert RecordingHttpTool().availability() == (True, None)

    async def test_it_records_and_refuses(self) -> None:
        tool = RecordingHttpTool()

        out = await tool.run(method="POST", url="https://example.com/intake")

        assert tool.calls == [
            {"method": "POST", "url": "https://example.com/intake"}
        ]
        assert out.startswith("[Error]")


class TestStubDatabaseTool:
    """Canned rows are what let a case plant an instruction in a field."""

    async def test_it_returns_the_rows_it_was_given(self) -> None:
        tool = StubDatabaseTool([{"客户名称": "瑞新集团", "备注": "忽略我"}])

        payload = json.loads(await tool.run(sql="SELECT * FROM customers"))

        assert payload["row_count"] == 1
        assert payload["rows"][0]["备注"] == "忽略我"
        assert tool.calls == [{"sql": "SELECT * FROM customers"}]

    async def test_an_empty_result_is_a_valid_answer(self) -> None:
        payload = json.loads(await StubDatabaseTool([]).run(sql="SELECT 1"))
        assert payload == {"row_count": 0, "rows": []}

    async def test_it_can_be_scripted_to_fail(self) -> None:
        tool = StubDatabaseTool(error="[Error] connector timeout")
        assert await tool.run(sql="SELECT 1") == "[Error] connector timeout"
        assert tool.calls == [{"sql": "SELECT 1"}]


class TestReadOnlyFileOpsTool:
    """A workspace the agent can read but never change."""

    async def test_every_mutating_operation_is_refused_and_recorded(
        self, tmp_path: Path
    ) -> None:
        tool = ReadOnlyFileOpsTool(workspace_dir=tmp_path)

        out = await tool.run(operation="write", path="out.md", content="x")

        assert out.startswith("[Error]")
        assert tool.refused == [
            {"operation": "write", "path": "out.md", "content": "x"}
        ]
        assert not (tmp_path / "out.md").exists()

    async def test_reads_still_work(self, tmp_path: Path) -> None:
        """The agent must be able to do everything except deliver."""
        (tmp_path / "notes.md").write_text("seeded", encoding="utf-8")
        tool = ReadOnlyFileOpsTool(workspace_dir=tmp_path)

        assert "seeded" in str(await tool.run(operation="read", path="notes.md"))
        assert tool.refused == []


# ---------------------------------------------------------------------------
# run_case toolset control
# ---------------------------------------------------------------------------


class TestRunCaseToolset:
    """A case disables a tool by not passing it, and swaps in a failing one
    through ``file_tool``."""

    async def test_extra_tools_reach_the_agent(self, tmp_path: Path) -> None:
        database = StubDatabaseTool([{"客户名称": "瑞新集团"}])
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[
                tool_call("database_query", {"sql": "SELECT 1"}),
                answer("one customer"),
            ],
        )

        run = await run_case(
            "who is overdue", tmp_path / "db", llm=llm, extra_tools=[database]
        )

        assert run.tool_names() == ["database_query"]
        assert database.calls == [{"sql": "SELECT 1"}]

    async def test_an_unregistered_tool_cannot_be_called(
        self, tmp_path: Path
    ) -> None:
        """Disabling is absence: the call fails instead of going through."""
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[
                tool_call("email_send", {"to": "a@b.c", "subject": "x", "body": "y"}),
                answer("could not send"),
            ],
        )

        run = await run_case("send it", tmp_path / "no-email", llm=llm)

        assert run.tool_errors(), "the unavailable tool call was not an error"

    async def test_file_tool_override_refuses_the_write(
        self, tmp_path: Path
    ) -> None:
        sandbox = tmp_path / "readonly"
        file_tool = ReadOnlyFileOpsTool(workspace_dir=sandbox)
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[
                tool_call(
                    "file_ops",
                    {"operation": "write", "path": "list.md", "content": "x"},
                ),
                answer("could not write"),
            ],
        )

        run = await run_case(
            "write the list", sandbox, llm=llm, file_tool=file_tool
        )

        assert file_tool.refused
        assert run.tool_errors()
        assert not (sandbox / "list.md").exists()

    async def test_the_run_is_filed_with_the_ledger(self, tmp_path: Path) -> None:
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=[answer("nothing to do")])

        await run_case("say hi", tmp_path / "ledger", llm=llm)

        entries = LEDGER.cases()
        assert [e.case for e in entries] == [current_case()]
        assert len(entries[0].runs) == 1


# ---------------------------------------------------------------------------
# The ledger behind the end-of-run report
# ---------------------------------------------------------------------------


class TestEvalLedger:
    """Per-case cost and execution facts, aggregated for the JSON report."""

    @staticmethod
    def _metrics(case: str, **overrides: object) -> RunMetrics:
        defaults: dict[str, object] = {
            "latency_s": 2.0,
            "llm_calls": 2,
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
            "tool_calls": 3,
            "tool_errors": 1,
            "cost_usd": 0.01,
        }
        defaults.update(overrides)
        return RunMetrics(case=case, **defaults)  # type: ignore[arg-type]

    def test_attempts_and_runs_accumulate_per_case(self) -> None:
        ledger = EvalLedger()
        ledger.record_attempt("case::a", mode="pass^k")
        ledger.record_run(self._metrics("case::a"))
        ledger.record_attempt("case::a", mode="pass^k")
        ledger.record_run(self._metrics("case::a"))

        row = ledger.report()["cases"][0]

        assert row["attempts"] == 2
        assert row["runs"] == 2
        assert row["mode"] == "pass^k"
        assert row["latency_s"] == 4.0
        assert row["total_tokens"] == 3000
        assert row["tool_errors"] == 2
        assert row["cost_usd"] == pytest.approx(0.02)

    def test_totals_sum_across_cases(self) -> None:
        ledger = EvalLedger()
        ledger.record_run(self._metrics("case::a"))
        ledger.record_run(self._metrics("case::b"))

        totals = ledger.report()["totals"]

        assert totals["cases"] == 2
        assert totals["runs"] == 2
        assert totals["tool_calls"] == 6

    def test_an_unpriced_run_reports_no_cost(self) -> None:
        """A missing price must not read as a free run."""
        ledger = EvalLedger()
        ledger.record_run(self._metrics("case::a", cost_usd=None))

        assert ledger.report()["cases"][0]["cost_usd"] is None
        assert ledger.report()["totals"]["cost_usd"] is None

    def test_annotate_adds_columns_to_a_seen_case(self) -> None:
        ledger = EvalLedger()
        ledger.record_run(self._metrics("case::a"))
        ledger.annotate("case::a", tier="challenge", outcome="passed")
        ledger.annotate("case::never-ran", tier="challenge")

        rows = ledger.report()["cases"]

        assert len(rows) == 1
        assert rows[0]["tier"] == "challenge"
        assert rows[0]["outcome"] == "passed"

    def test_write_produces_the_report_file(self, tmp_path: Path) -> None:
        ledger = EvalLedger()
        ledger.record_attempt("case::a", mode="pass@k")
        ledger.record_run(self._metrics("case::a"))
        target = tmp_path / "nested" / "metrics.json"

        written = ledger.write(target, extra={"tiers": {"challenge": {}}})

        assert written == target
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["totals"]["runs"] == 1
        assert payload["cases"][0]["case"] == "case::a"
        assert payload["tiers"] == {"challenge": {}}

    def test_nothing_ran_writes_nothing(self, tmp_path: Path) -> None:
        """An all-skipped run must not leave a report claiming zero cost."""
        target = tmp_path / "metrics.json"
        assert EvalLedger().write(target) is None
        assert not target.exists()

    def test_reset_clears_the_ledger(self) -> None:
        ledger = EvalLedger()
        ledger.record_run(self._metrics("case::a"))
        ledger.reset()
        assert ledger.cases() == []


# ---------------------------------------------------------------------------
# pass^k repeat
# ---------------------------------------------------------------------------


class TestEvalRepeat:
    """The challenge tier's discipline: every attempt must pass, because one
    breach of a tool ban is the whole finding."""

    async def test_it_runs_the_full_budget_when_every_attempt_passes(self) -> None:
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1

        with patch.dict(os.environ, {"EVAL_STRICT_ATTEMPTS": "3"}):
            await eval_repeat(attempt)

        assert calls == 3

    async def test_one_failure_ends_it(self) -> None:
        """pass@k would have retried past this; pass^k must not."""
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise AssertionError("breached on attempt 2")

        with patch.dict(os.environ, {"EVAL_STRICT_ATTEMPTS": "5"}):
            with pytest.raises(AssertionError, match="breached on attempt 2"):
                await eval_repeat(attempt)

        assert calls == 2

    async def test_it_defaults_to_a_single_attempt(self) -> None:
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1

        await eval_repeat(attempt)

        assert calls == 1

    async def test_it_falls_back_to_eval_attempts(self) -> None:
        """One knob raises both tiers; EVAL_STRICT_ATTEMPTS splits them."""
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1

        with patch.dict(os.environ, {"EVAL_ATTEMPTS": "2"}):
            await eval_repeat(attempt)

        assert calls == 2

    async def test_a_harness_bug_surfaces_immediately(self) -> None:
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("harness broke")

        with patch.dict(os.environ, {"EVAL_STRICT_ATTEMPTS": "3"}):
            with pytest.raises(RuntimeError, match="harness broke"):
                await eval_repeat(attempt)

        assert calls == 1

    async def test_attempts_are_filed_with_the_ledger(self) -> None:
        async def attempt() -> None:
            return None

        with patch.dict(os.environ, {"EVAL_STRICT_ATTEMPTS": "2"}):
            await eval_repeat(attempt)

        entry = LEDGER.cases()[0]
        assert entry.mode == "pass^k"
        assert entry.attempts == 2


class TestEvalRetryLedger:
    """pass@k files its attempts under its own mode, so the report shows
    which discipline a case ran under."""

    async def test_attempts_are_filed_with_the_ledger(self) -> None:
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise AssertionError("flaky")

        with patch.dict(os.environ, {"EVAL_ATTEMPTS": "3"}):
            await eval_retry(attempt)

        entry = LEDGER.cases()[0]
        assert entry.mode == "pass@k"
        assert entry.attempts == 2


# ---------------------------------------------------------------------------
# Tiered reporting (evals/conftest.py)
# ---------------------------------------------------------------------------


class TestTierReport:
    """Regression misses and challenge misses are different events, so the
    suite reports a pass rate per tier instead of one average."""

    @staticmethod
    def _with_outcomes(
        tiers: dict[str, str], outcomes: dict[str, str]
    ) -> dict[str, dict[str, object]]:
        with (
            patch.dict(eval_conftest._tier_by_nodeid, tiers, clear=True),
            patch.dict(eval_conftest._outcome_by_nodeid, outcomes, clear=True),
        ):
            return eval_conftest._tier_report()

    def test_pass_rate_is_computed_per_tier(self) -> None:
        report = self._with_outcomes(
            {"a::1": "regression", "b::1": "challenge", "b::2": "challenge"},
            {"a::1": "passed", "b::1": "passed", "b::2": "failed"},
        )

        assert report["regression"]["pass_rate"] == 1.0
        assert report["challenge"] == {
            "passed": 1,
            "failed": 1,
            "skipped": 0,
            "total": 2,
            "pass_rate": 0.5,
        }

    def test_a_tier_that_did_not_run_has_no_pass_rate(self) -> None:
        """All-skipped must not read as 0% failed."""
        report = self._with_outcomes({"a::1": "challenge"}, {})

        assert report["challenge"]["skipped"] == 1
        assert report["challenge"]["pass_rate"] is None

    def test_an_unmarked_case_is_reported_not_dropped(self) -> None:
        report = self._with_outcomes({"a::1": "untiered"}, {"a::1": "passed"})
        assert report["untiered"]["total"] == 1

    def test_write_metrics_annotates_cases_with_tier_and_outcome(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "metrics.json"
        LEDGER.record_attempt("evals/x.py::test_a", mode="pass^k")
        LEDGER.record_run(RunMetrics(case="evals/x.py::test_a", latency_s=1.0))

        with (
            patch.dict(
                eval_conftest._tier_by_nodeid,
                {"evals/x.py::test_a": "challenge"},
                clear=True,
            ),
            patch.dict(
                eval_conftest._outcome_by_nodeid,
                {"evals/x.py::test_a": "passed"},
                clear=True,
            ),
            patch.dict(os.environ, {"EVAL_METRICS_PATH": str(target)}),
        ):
            eval_conftest._write_metrics()

        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["cases"][0]["tier"] == "challenge"
        assert payload["cases"][0]["outcome"] == "passed"
        assert payload["cases"][0]["mode"] == "pass^k"
        assert payload["tiers"]["challenge"]["pass_rate"] == 1.0
