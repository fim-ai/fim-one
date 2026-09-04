"""Behavioral evals for deliverable handling — regression tier.

Both cases replay a production failure from a 2026-07-05 user session
(see evals/README.md).  Assertions target the trajectory and the sandbox,
not the answer text — with one deliberate exception: the claim-vs-deed
check, where the *claim* in the text is exactly what is under test.

Tier: ``regression``, so these run pass@k (``eval_retry``) — the question
is whether the fixed behavior is still reachable, not whether it holds on
every single sample.  Policy probes are the other tier; see
``test_policy_behavior.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .harness import BANNED_PURPLE_HEXES, eval_retry, run_case

# Real LLM + up to 15 ReAct iterations — override the repo-wide 30s cap.
pytestmark = [pytest.mark.timeout(600), pytest.mark.regression]

# The turn-1 deliverable as production generated it: AI-purple gradient.
_PURPLE_SCORECARD = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>前端框架评分卡</title>
<style>
body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
.metric-value { background: linear-gradient(120deg, #667eea, #764ba2);
  -webkit-background-clip: text; }
</style>
</head>
<body>
<h1>React 142.7M · Vue 12.98M · Svelte 4.69M</h1>
</body>
</html>
"""


def _assert_no_banned_purple(html: str) -> None:
    lowered = html.lower()
    for hex_code in BANNED_PURPLE_HEXES:
        assert hex_code not in lowered, f"banned AI-purple {hex_code} in output"


class TestRestyleDeliverable:
    """Bug 1: restyle request must edit the file, never generate an image."""

    async def test_restyle_html_edits_file_not_image(self, tmp_path: Path) -> None:
        async def attempt() -> None:
            sandbox = tmp_path / "restyle"
            sandbox.mkdir(exist_ok=True)
            (sandbox / "scorecard.html").write_text(
                _PURPLE_SCORECARD, encoding="utf-8"
            )
            # Verbatim user wording that triggered the production bug,
            # plus the workspace context turn 1 would have established.
            run = await run_case(
                "工作区里有我之前让你生成的 scorecard.html 评分卡。"
                "不要这种 AI 紫色太丑了。用 claude 风格。",
                sandbox,
            )

            assert not run.image_tool.calls, (
                f"agent generated an image for an HTML restyle request: "
                f"{run.image_tool.calls}"
            )
            assert "generate_image" not in run.tool_names()
            assert run.file_writes(path_contains="scorecard"), (
                f"no write to scorecard.html — tools called: {run.tool_names()}"
            )
            updated = run.sandbox_file("scorecard.html")
            assert updated, "scorecard.html vanished from the sandbox"
            _assert_no_banned_purple(updated)

        await eval_retry(attempt)


class TestDeliverableIsFile:
    """Bug 2: a requested HTML file must actually land on disk."""

    async def test_html_deliverable_is_written_to_disk(self, tmp_path: Path) -> None:
        async def attempt() -> None:
            sandbox = tmp_path / "deliver"
            run = await run_case(
                "生成一个名为 scorecard.html 的样式化评分卡文件，对比三个前端框架，"
                "数据直接用：React 周下载 1.43 亿 / 星标 246K，"
                "Vue 周下载 1298 万 / 星标 54K，"
                "Svelte 周下载 469 万 / 星标 87K。不要联网，不用查证。",
                sandbox,
            )

            html_files = run.sandbox_html_files()
            assert html_files, (
                f"no .html file written to sandbox — the deliverable exists "
                f"only in chat. Tools called: {run.tool_names()}"
            )
            for path in html_files:
                _assert_no_banned_purple(path.read_text(encoding="utf-8"))

            # Claim-vs-deed: if the answer claims a file was produced, the
            # file must exist (it does, per the assert above) — and the
            # inverse guard: an answer that pastes a full HTML document
            # instead of delivering a file is the exact production bug.
            answer = run.result.answer or ""
            assert "<!DOCTYPE html>" not in answer and "</html>" not in answer.lower(), (
                "agent pasted the full HTML into the chat answer instead of "
                "treating the file as the deliverable"
            )

        await eval_retry(attempt)
