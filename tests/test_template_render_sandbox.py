"""Security properties of ``template_render``.

The template string comes from the model and is treated as untrusted
input.  The rule under test: a template computes over JSON data and
nothing else.  No template in the corpus may start a process, show a value
from the process environment or render a Python object, and objects that
are not plain data stay inert even if one reaches the render context.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from fim_one.core.tool.base import ToolResult
from fim_one.core.tool.builtin import template_render as tr
from fim_one.core.tool.builtin.template_render import TemplateRenderTool

_CANARY = "CANARY-7f3a9c-not-in-output"

# Templates that reach for something other than plain data: attributes
# beginning with an underscore, functions' globals, template internals and
# objects returned by such lookups.  Each must yield an error or plain data.
_CORPUS: list[str] = [
    # globals of functions reachable from default globals
    "{{ cycler.__init__.__globals__ }}",
    "{{ joiner.__init__.__globals__ }}",
    "{{ namespace.__init__.__globals__ }}",
    "{{ lipsum.__globals__ }}",
    "{{ range.__class__.__mro__ }}",
    "{{ dict.__subclasses__() }}",
    "{{ dict.mro() }}",
    "{{ cycler.mro()[1].__subclasses__() }}",
    # class walk from literals
    "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    "{{ ''.__class__.__base__.__subclasses__() }}",
    "{{ ().__class__.__bases__[0].__subclasses__() }}",
    "{{ [].__class__.__base__.__subclasses__() }}",
    "{{ {}.__class__.__base__.__subclasses__() }}",
    "{{ (1).__class__.__base__.__subclasses__() }}",
    "{{ ''['__class__'] }}",
    "{{ ''|attr('__class__') }}",
    "{{ ''|attr('__cla' ~ 'ss__') }}",
    "{{ ''|attr('\\x5f\\x5fclass\\x5f\\x5f') }}",
    "{{ ''|attr('format') }}",
    "{{ ''|attr('format')('{0.__class__}', '') }}",
    "{% set a = 'at' ~ 'tr' %}{{ ''|map(a, '__class__')|list }}",
    "{{ ['']|map(attribute='__class__')|list }}",
    "{{ ['']|map(attribute='__class__.__mro__')|list }}",
    "{{ [{'a': ''}]|map(attribute='a.__class__')|list }}",
    "{{ ['']|selectattr('__class__')|list }}",
    "{{ ['']|rejectattr('__class__')|list }}",
    "{{ ['']|sort(attribute='__class__')|list }}",
    "{{ ['']|groupby('__class__')|list }}",
    "{{ ['']|unique(attribute='__class__')|list }}",
    "{{ ['']|sum(attribute='__class__') }}",
    "{{ ['']|max(attribute='__class__') }}",
    # template-internal objects
    "{{ self }}",
    "{{ self.__init__.__globals__ }}",
    "{{ self._TemplateReference__context }}",
    "{{ self._TemplateReference__context.environment }}",
    "{{ self._TemplateReference__context.cycler.__init__.__globals__ }}",
    "{{ config }}",
    "{{ request }}",
    "{{ get_flashed_messages.__globals__ }}",
    "{{ url_for.__globals__ }}",
    "{% for x in [1] %}{{ loop.__class__.__init__.__globals__ }}{% endfor %}",
    "{% for x in [1] %}{{ loop._undefined }}{% endfor %}",
    "{% for x in [1] %}{{ loop.cycle.__func__.__globals__ }}{% endfor %}",
    "{% for x in [1] %}{{ loop.cycle.__self__.__class__ }}{% endfor %}",
    "{% macro m() %}{% endmacro %}{{ m.__globals__ }}",
    "{% macro m() %}{% endmacro %}{{ m._func.__globals__ }}",
    "{% macro m() %}{% endmacro %}{{ m._environment }}",
    "{% macro m() %}{% endmacro %}{{ m.__class__.__init__.__globals__ }}",
    "{% set ns = namespace() %}{{ ns.__class__.__init__.__globals__ }}",
    "{% set ns = namespace(a=1) %}{{ ns._Namespace__attrs }}",
    "{% set c = cycler(1) %}{{ c.__class__.__init__.__globals__ }}",
    "{% set c = cycler(1) %}{{ c.next.__func__.__globals__ }}",
    "{% set j = joiner() %}{{ j.__class__.__init__.__globals__ }}",
    "{{ undefined_name.__class__.__init__.__globals__ }}",
    "{{ undefined_name._undefined_obj }}",
    # bound-method pivots
    "{{ ''.join.__self__.__class__ }}",
    "{{ ''.upper.__call__.__self__ }}",
    "{{ [].append.__self__.__class__ }}",
    "{{ {}.items.__self__.__class__.__base__ }}",
    "{{ ''.format.__globals__ }}",
    "{{ range(1).__reduce__() }}",
    "{{ range(1).__reduce_ex__(2) }}",
    # str.format / format_map attribute walks
    "{{ '{0.__class__}'.format('') }}",
    "{{ '{0.__class__.__mro__[1]}'.format('') }}",
    "{{ '{0.__init__.__globals__}'.format(cycler) }}",
    "{{ '{0.__globals__}'.format(lipsum) }}",
    "{{ '{0._TemplateReference__context}'.format(self) }}",
    "{{ '{a.__class__}'.format(a='') }}",
    "{{ '{a.__class__}'.format_map({'a': ''}) }}",
    "{{ '%s'|format(''.__class__) }}",
    "{% set f = ''.format %}{{ f.__self__.__class__ }}",
    "{% set f = '{0.__class__}'.format %}{{ f('') }}",
    "{{ ['{0.__class__}']|map('format', '')|list }}",
    # generators / frames / code objects
    "{{ (x for x in [1]).gi_frame }}",
    "{{ (x for x in [1]).gi_frame.f_globals }}",
    "{{ (x for x in [1]).gi_code }}",
    "{% macro m() %}{% endmacro %}{{ m.func_globals }}",
    "{% macro m() %}{% endmacro %}{{ m.func_code }}",
    # statements that would pull code in
    "{% import 'helpers.txt' as h %}{{ h }}",
    "{% include 'other.txt' %}",
    "{% extends 'base.txt' %}",
    "{% from 'helpers.txt' import f %}{{ f() }}",
    # context-supplied strings used as attribute names
    "{{ data[key] }}",
    "{{ data|attr(key) }}",
    "{{ ''[key] }}",
    "{{ ''[key][key2] }}",
    "{{ [''] | map(attribute=key) | list }}",
    "{{ fmt.format('') }}",
    "{{ fmt.format(data) }}",
    "{{ fmt|format('') }}",
    # calling non-allowlisted callables obtained indirectly
    "{{ dict.fromkeys.__self__.__subclasses__() }}",
    "{{ dict(a=1).__class__.__base__.__subclasses__() }}",
    "{{ range.mro() }}",
    "{{ namespace.__mro__ }}",
    "{{ cycler.__dict__ }}",
    "{{ joiner.__module__ }}",
]

_CORPUS_CONTEXT = {
    "data": {"a": 1},
    "key": "__class__",
    "key2": "__mro__",
    "fmt": "{0.__class__.__init__.__globals__}",
}

# Substrings that only appear when a Python object (not data) was rendered.
_OBJECT_MARKERS = (
    "<class ",
    "<module ",
    "<function ",
    "<built-in ",
    "<bound method",
    "<method ",
    "<frame ",
    "<code object",
    "<jinja2.",
    "<fim_one.",
    "environ(",
    "__builtins__",
)


def _text(result: str | ToolResult) -> str:
    if isinstance(result, ToolResult):
        return str(result.content)
    return result


@pytest.fixture()
def tripwires(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> dict[str, Any]:
    """Record any process spawn and plant a secret in the environment.

    The process tripwires are for the in-process runs only: the tool itself
    starts its render process."""
    calls: list[str] = []

    def _trip(name: str) -> Any:
        def _inner(*args: Any, **kwargs: Any) -> Any:
            calls.append(name)
            raise AssertionError(f"{name} was reached from a template")

        return _inner

    monkeypatch.setenv("TEMPLATE_CANARY_SECRET", _CANARY)
    if getattr(request.node, "callspec", None) is None or request.node.callspec.params.get("where") != "tool":
        monkeypatch.setattr(os, "popen", _trip("os.popen"))
        monkeypatch.setattr(os, "system", _trip("os.system"))
        monkeypatch.setattr(subprocess, "Popen", _trip("subprocess.Popen"))
    return {"calls": calls}


class TestCorpusReachesOnlyData:
    @pytest.mark.parametrize("where", ["in_process", "tool"])
    @pytest.mark.parametrize("payload", _CORPUS)
    async def test_template_yields_only_data(
        self, payload: str, where: str, tripwires: dict[str, Any]
    ) -> None:
        """Once against the sandbox in this process (so the tripwires above
        would fire), once through the tool, which renders in a child."""
        template = payload
        if where == "in_process":
            out = tr._worker.render(template, dict(_CORPUS_CONTEXT))
        else:
            out = _text(
                await TemplateRenderTool().run(
                    template=template, context=json.dumps(_CORPUS_CONTEXT)
                )
            )

        assert tripwires["calls"] == []
        assert _CANARY not in out
        for needle in _OBJECT_MARKERS:
            assert needle not in out, f"{needle!r} in output for {payload!r}: {out[:200]}"
        # Filters that look attributes up by name yield an undefined value
        # for a refused attribute instead of raising; either way nothing
        # but data may come out.
        assert out.startswith("[Error]") or set(out) <= set(
            "[]()',. Undefined<TemplateReference None>"
        ), f"{payload!r} rendered: {out[:200]}"


class TestUnderscoreAttributesAreNeverReachable:
    @pytest.mark.parametrize(
        "name",
        ["__globals__", "__class__", "__mro__", "__subclasses__", "__init__",
         "__builtins__", "__dict__", "__self__", "__func__", "__code__"],
    )
    def test_no_value_exposes_the_attribute(self, name: str) -> None:
        env = tr._build_environment()
        values: list[Any] = ["", 1, 1.5, [], {}, (), range(1), None, True]
        values += [v for v in env.globals.values()]
        for value in values:
            attr = getattr(value, name, None)
            assert env.is_safe_attribute(value, name, attr) is False, (value, name)


class TestOnlyDataIsReachable:
    """Even if a non-data object reaches the context, it must be inert."""

    class _Service:
        secret = _CANARY

        def run(self) -> str:
            return _CANARY

    @pytest.mark.parametrize(
        "template",
        [
            "{{ svc.secret }}",
            "{{ svc.run() }}",
            "{{ svc['secret'] }}",
            "{{ '{0.secret}'.format(svc) }}",
            "{{ [svc]|map(attribute='secret')|list }}",
            "{{ mod.sep }}",
            "{{ mod.getcwd() }}",
            "{{ fn() }}",
            "{{ fn.__globals__ }}",
            "{{ cls() }}",
            "{{ cls.secret }}",
        ],
    )
    def test_foreign_objects_cannot_be_used(self, template: str) -> None:
        env = tr._build_environment()
        ctx = {
            "svc": self._Service(),
            "mod": os,
            "fn": os.getcwd,
            "cls": self._Service,
        }
        try:
            out = env.from_string(template).render(ctx)
        except Exception as exc:  # refused outright
            assert _CANARY not in str(exc)
        else:  # attribute-by-name filters degrade to an undefined value
            assert _CANARY not in out
            assert out == "[Undefined]"

    def test_globals_are_an_explicit_allowlist(self) -> None:
        env = tr._build_environment()
        assert set(env.globals) == {"range", "dict", "namespace", "cycler", "joiner", "__bounded__"}
        from jinja2.sandbox import safe_range

        assert env.globals["range"] is safe_range
        assert "attr" not in env.filters

    def test_environment_is_sandboxed(self) -> None:
        from jinja2.sandbox import ImmutableSandboxedEnvironment

        assert isinstance(tr._build_environment(), ImmutableSandboxedEnvironment)


class TestLegitimateTemplatesStillWork:
    @pytest.mark.parametrize(
        ("template", "context", "expected"),
        [
            ("{{ user.name|upper }}", {"user": {"name": "al"}}, "AL"),
            ("{% for k, v in d.items() %}{{ k }}={{ v }};{% endfor %}", {"d": {"a": 1}}, "a=1;"),
            ("{% for x in xs %}{{ loop.index }}{{ loop.cycle('a','b') }}{% endfor %}", {"xs": [1, 2]}, "1a2b"),
            ("{% set ns = namespace(t=0) %}{% for x in xs %}{% set ns.t = ns.t + x %}{% endfor %}{{ ns.t }}", {"xs": [1, 2]}, "3"),
            ("{% macro row(x) %}<{{ x }}>{% endmacro %}{{ row(1) }}{{ row(2) }}", {}, "<1><2>"),
            ("{{ rows|map(attribute='n')|sum }}", {"rows": [{"n": 1}, {"n": 2}]}, "3"),
            ("{{ rows|sort(attribute='n', reverse=true)|map(attribute='n')|join(',') }}", {"rows": [{"n": 1}, {"n": 2}]}, "2,1"),
            ("{{ '%.2f'|format(x) }} {{ '{:>4}'.format(x) }}", {"x": 1.5}, "1.50  1.5"),
            ("{{ s.split(',')|length }} {{ s.replace(',', ';') }}", {"s": "a,b"}, "2 a;b"),
            ("{% for i in range(3) %}{{ i }}{% endfor %}{{ dict(a=1).a }}", {}, "0121"),
            ("{% set j = joiner('|') %}{% for x in xs %}{{ j() }}{{ x }}{% endfor %}", {"xs": [1, 2]}, "1|2"),
            ("{{ 2 ** 10 }} {{ 'ab' * 2 }} {{ 3 * 4 }}", {}, "1024 abab 12"),
            ("{{ self_ }} {{ context }}", {"self_": "a", "context": "b"}, "a b"),
        ],
    )
    async def test_renders(
        self, template: str, context: dict[str, Any], expected: str
    ) -> None:
        out = _text(
            await TemplateRenderTool().run(
                template=template, context=json.dumps(context)
            )
        )
        assert out == expected

    async def test_context_keys_never_collide_with_render_parameters(self) -> None:
        out = _text(
            await TemplateRenderTool().run(
                template="{{ args }}|{{ kwargs }}|{{ vars }}",
                context=json.dumps({"self": "s", "args": "a", "kwargs": "k", "vars": "v"}),
            )
        )
        assert out == "a|k|v"


def _peak_rss_mb() -> float:
    import resource
    import sys

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


_HEAVY = [
    # loops that never finish, or finish after the heat death of the server
    "{% for i in range(10**12) %}{% endfor %}",
    "{% for a in range(100000) %}{% for b in range(100000) %}{% for c in range(100000) %}{% endfor %}{% endfor %}{% endfor %}",
    "{% for a in range(100000) %}{% for b in range(100000) %}{{ '' }}{% endfor %}{% endfor %}",
    "{% macro m(n) %}{% for i in range(100000) %}{{ m(n) }}{% endfor %}{% endmacro %}{{ m(1) }}",
    "{% macro m(n) %}{{ m(n + 1) }}{% endmacro %}{{ m(1) }}",
    "{{ range(100000)|map('string')|map('length')|sum }}{% for i in range(100000) %}{{ range(100000)|list|length }}{% endfor %}",
    # one-shot allocations
    "{{ 'a' * 100000000 }}",
    "{{ 100000000 * 'a' }}",
    "{{ [0] * 100000000 }}",
    "{{ 9 ** 99999 }}",
    "{{ (9 ** 900) ** 900 }}",
    "{{ 2 ** 100000000 }}",
    "{{ range(100000000)|list }}",
    "{{ range(30000000)|list|length }}",
    "{{ 'x'.center(6000000000) }}",
    "{{ 'x'.ljust(6000000000)|length }}",
    "{{ 'x'.zfill(6000000000)|length }}",
    "{{ 'x'|center(6000000000)|length }}",
    "{{ '%6000000000d'|format(1)|length }}",
    "{{ '%60000000d'|format(1) }}",
    "{{ '{:>6000000000}'.format('')|length }}",
    "{{ '{:>60000000}'.format('') }}",
    "{{ 'x'|indent(6000000000)|length }}",
    "{{ ('x\\n' * 100000)|indent(100000)|length }}",
    "{{ 'x'|wordwrap(1)|center(6000000000)|length }}",
    # doubling
    "{% set ns = namespace(a='xxxxxxxx') %}{% for i in range(40) %}{% set ns.a = ns.a + ns.a %}{% endfor %}{{ ns.a|length }}",
    "{% set ns = namespace(a=[1,2,3,4]) %}{% for i in range(40) %}{% set ns.a = ns.a + ns.a %}{% endfor %}{{ ns.a|length }}",
    "{% set ns = namespace(a='x') %}{% for i in range(40) %}{% set ns.a = ns.a ~ ns.a %}{% endfor %}{{ ns.a|length }}",
    "{% for i in range(99999) %}{{ 'x' * 1000 }}{% endfor %}",
    # each level of a recursive loop counts
    "{% for i in [1] recursive %}{% for j in range(1000) %}{% endfor %}{{ loop(range(100000)) }}{% endfor %}",
]


@pytest.fixture()
def tight_limits(monkeypatch: pytest.MonkeyPatch) -> float:
    monkeypatch.setenv("TEMPLATE_RENDER_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("TEMPLATE_RENDER_MEMORY_MB", "128")
    return 3.0


class TestResourceBounds:
    @pytest.mark.parametrize("template", _HEAVY)
    async def test_heavy_templates_end_within_the_limit(
        self, template: str, tight_limits: float
    ) -> None:
        import time

        before = _peak_rss_mb()
        started = time.monotonic()
        out = _text(await TemplateRenderTool().run(template=template))
        elapsed = time.monotonic() - started

        assert out.startswith("[Error]"), out[:80]
        assert len(out) < 1000
        assert elapsed < tight_limits + 3, f"took {elapsed:.1f}s"
        # The work happened in the child; this process did not pay for it.
        assert _peak_rss_mb() - before < 48

    async def test_stuck_renders_do_not_hold_threads_or_the_event_loop(
        self, tight_limits: float
    ) -> None:
        import asyncio
        import time

        stuck = [
            asyncio.create_task(TemplateRenderTool().run(template=_HEAVY[1])) for _ in range(12)
        ]
        await asyncio.sleep(0.3)
        started = time.monotonic()
        # Threads and the loop stay available while the renders are stuck.
        assert await asyncio.gather(*[asyncio.to_thread(lambda: 1) for _ in range(64)]) == [1] * 64
        ok = _text(await TemplateRenderTool().run(template="{{ 1 + 1 }}"))
        assert time.monotonic() - started < tight_limits + 3
        assert ok == "2" or ok.startswith("[Error] Too many")
        for out in await asyncio.gather(*stuck):
            assert _text(out).startswith("[Error]")

    async def test_the_render_process_is_gone_afterwards_and_had_no_secrets(
        self, tight_limits: float, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        monkeypatch.setenv("TEMPLATE_CANARY_SECRET", _CANARY)
        spawned: list[tuple[int, dict[str, Any]]] = []
        real = asyncio.create_subprocess_exec

        async def _spy(*args: Any, **kwargs: Any) -> Any:
            proc = await real(*args, **kwargs)
            if args and args[-3:-2] and "_template_worker" in str(args[2]):
                spawned.append((proc.pid, kwargs))
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spy)
        for template in ("{{ 1 }}", _HEAVY[0], _HEAVY[14]):
            await TemplateRenderTool().run(template=template)

        assert len(spawned) == 3
        for pid, kwargs in spawned:
            assert set(kwargs["env"]) <= {"PATH", "LC_ALL"}
            assert _CANARY not in str(kwargs["env"])
            assert kwargs["start_new_session"] is True
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)

    @pytest.mark.parametrize(
        "template",
        [
            "{% set __bounded__ = dict %}{% for i in range(3) %}{{ i }}{% endfor %}",
            "{% set __bounded__ %}x{% endset %}{% for i in range(3) %}{{ i }}{% endfor %}",
            "{% macro __bounded__(x) %}{{ x }}{% endmacro %}{% for i in range(3) %}{{ i }}{% endfor %}",
            "{% macro m(__bounded__) %}{% for i in range(3) %}{{ i }}{% endfor %}{% endmacro %}{{ m(1) }}",
            "{% for __bounded__ in [1] %}{% for i in range(3) %}{{ i }}{% endfor %}{% endfor %}",
            "{% with __bounded__ = dict %}{% for i in range(3) %}{{ i }}{% endfor %}{% endwith %}",
            "{% call(__bounded__) m() %}{% endcall %}",
            "{{ __bounded__ }}",
        ],
    )
    def test_templates_cannot_bind_the_loop_budget_name(self, template: str) -> None:
        out = tr._worker.render(template, {})
        assert out.startswith("[Error] Template not allowed"), out[:200]

    def test_context_cannot_supply_the_loop_budget_name(self) -> None:
        out = tr._worker.render(
            "{% for i in range(3) %}{{ i }}{% endfor %}", {"__bounded__": [7]}
        )
        assert out == "012"

    @pytest.mark.parametrize(
        "template",
        [
            # Each level recurses into a fresh range: only the outer loop's
            # iterable passes through the budget wrapper of the ``for`` tag.
            "{% for i in [1] recursive %}{% for j in range(100000) %}{% endfor %}"
            "{{ loop(range(100000)) }}{% endfor %}",
        ],
    )
    def test_recursive_loops_count_against_the_budget(
        self, template: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tr._worker, "MAX_ITERATIONS", 10_000)
        out = tr._worker.render(template, {})
        assert out.startswith("[Error] Template not allowed: the template loops more than"), out[:200]

    def test_recursive_loops_over_data_still_work(self) -> None:
        tree = [{"n": "a", "c": [{"n": "b", "c": [{"n": "c", "c": []}]}]}, {"n": "d", "c": []}]
        out = tr._worker.render(
            "{% for x in tree recursive %}{{ x.n }}{{ loop.depth }}{{ loop(x.c) }}{% endfor %}",
            {"tree": tree},
        )
        assert out == "a1b2c3d1"

    async def test_ordinary_loops_are_within_the_budget(self) -> None:
        out = _text(
            await TemplateRenderTool().run(
                template="{% for i in range(50000) %}{% if loop.last %}{{ loop.length }}{% endif %}{% endfor %}"
            )
        )
        assert out == "50000"


_HTML = "<html><body><div>{{ t }}</div></body></html>"


class TestWorkspaceWriteContainment:
    """The rendered file lands inside the workspace or nowhere."""

    async def _run(self, tmp_path: Path, filename: Any) -> ToolResult:
        tool = TemplateRenderTool(
            artifacts_dir=tmp_path / "uploads" / "conversations" / "c" / "artifacts",
            workspace_dir=tmp_path / "ws",
        )
        result = await tool.run(template=_HTML, context='{"t": "x"}', filename=filename)
        assert isinstance(result, ToolResult)
        return result

    @staticmethod
    def _files_outside(tmp_path: Path) -> list[Path]:
        allowed = (tmp_path / "ws", tmp_path / "uploads")
        return [
            p
            for p in tmp_path.rglob("*")
            if p.is_file() and not any(p.is_relative_to(a) for a in allowed)
        ]

    @pytest.mark.parametrize(
        "filename",
        [
            "../escape.html",
            "../../escape.html",
            "/abs/escape.html",
            "..\\..\\escape.html",
            "C:\\escape.html",
            "a/../../escape.html",
            "..",
            ".",
            "../",
            "sub/dir/escape.html",
            "nul\x00byte.html",
            "x" * 5000,
            "line\nbreak.html",
            123,
            None,
        ],
    )
    async def test_filename_never_leaves_workspace(
        self, tmp_path: Path, filename: Any
    ) -> None:
        result = await self._run(tmp_path, filename)

        assert self._files_outside(tmp_path) == []
        name = result.artifacts[0].name
        assert "/" not in name and "\\" not in name and "\x00" not in name
        assert len(name) <= 120
        assert [p.name for p in (tmp_path / "ws").iterdir()] == [name]

    async def test_planted_symlink_is_not_followed(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.py"
        outside.write_text("ORIGINAL")
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "report.html").symlink_to(outside)

        result = await self._run(tmp_path, "report.html")

        assert outside.read_text() == "ORIGINAL"
        assert (ws / "report.html").is_symlink()
        # The artifact copy is unaffected.
        assert result.artifacts[0].name == "report.html"

    async def test_planted_dangling_symlink_creates_nothing(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "new_module.py"
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "report.html").symlink_to(target)

        await self._run(tmp_path, "report.html")

        assert not target.exists()

    async def test_planted_fifo_does_not_block(self, tmp_path: Path) -> None:
        if not hasattr(os, "mkfifo"):
            pytest.skip("no FIFOs on this platform")
        ws = tmp_path / "ws"
        ws.mkdir()
        os.mkfifo(ws / "report.html")

        result = await self._run(tmp_path, "report.html")

        assert result.artifacts[0].name == "report.html"
