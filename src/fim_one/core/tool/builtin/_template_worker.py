"""The template sandbox and the process it runs in.

This file is both a module (``build_environment`` and ``render`` are used
in-process by tests) and a script: ``template_render`` runs it as a child
process, one per render, and talks to it over pipes.  It imports the
standard library and Jinja2 only, so that the child starts quickly and has
nothing of the application loaded.

Two layers keep a template from taking the server down with it:

* inside, the sandbox (a template computes over plain data and nothing
  else), a bounded ``range``, an iteration budget for ``for`` loops
  (including each level of a ``recursive`` loop) and a cap on the output;
* outside, the parent enforces a wall-clock limit and a memory limit by
  killing this process, whatever the template is doing at that moment.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from jinja2 import StrictUndefined, TemplateError, nodes
from jinja2.exceptions import SecurityError
from jinja2.runtime import LoopContext, Macro, Undefined
from jinja2.sandbox import ImmutableSandboxedEnvironment, safe_range
from jinja2.utils import Cycler, Joiner, Namespace

# Hard cap on rendered output; rendering stops once it is exceeded.
_MAX_OUTPUT_CHARS = 2_000_000
# Caps for the two operators that can allocate without looping.
_MAX_REPEAT_LEN = 1_000_000
_MAX_POW_EXPONENT = 1_000
_MAX_INT_BITS = 4_096


# Total ``for`` iterations one render may make, across all loops.
MAX_ITERATIONS = 1_000_000
_BUDGET_NAME = "__bounded__"


class IterationBudgetExceeded(SecurityError):
    """The template looped more than :data:`MAX_ITERATIONS` times."""


class _BoundedIter:
    """Wraps the iterable of every ``for`` loop and counts what it yields."""

    spent = 0  # per process when run as a script; reset by ``render``

    def __init__(self, iterable: Any) -> None:
        self._iterable = iterable

    def __iter__(self) -> Any:
        for item in self._iterable:
            _BoundedIter.spent += 1
            if _BoundedIter.spent > MAX_ITERATIONS:
                raise IterationBudgetExceeded(
                    f"the template loops more than {MAX_ITERATIONS} times"
                )
            yield item

    def __repr__(self) -> str:
        return ""

    __str__ = __repr__


def _names_used(tree: nodes.Template) -> set[str]:
    """Every name the template reads, binds or declares."""
    names = {n.name for n in tree.find_all(nodes.Name)}
    names.update(m.name for m in tree.find_all(nodes.Macro))
    for imp in tree.find_all(nodes.FromImport):
        for item in imp.names:
            names.update(item if isinstance(item, tuple) else (item,))
    return names


def _bound_loops(tree: nodes.Template) -> nodes.Template:
    """Route the iterable of every ``for`` loop through :class:`_BoundedIter`.

    The budget is reached under a reserved name; a template that uses
    that name in any way (reads, assigns, declares as a macro, parameter
    or loop variable) is refused before it runs.
    """
    if _BUDGET_NAME in _names_used(tree):
        raise SecurityError(f"the name {_BUDGET_NAME!r} is reserved")
    for loop in tree.find_all(nodes.For):
        loop.iter = nodes.Call(
            nodes.Name(_BUDGET_NAME, "load", lineno=loop.lineno),
            [loop.iter],
            [],
            None,
            None,
            lineno=loop.lineno,
        )
    return tree

# Plain data, as produced by ``json.loads`` or built inside a template.
_DATA_TYPES: tuple[type, ...] = (
    str, int, float, bool, type(None), list, tuple, dict, set, frozenset, range,
)
# Jinja2's own runtime helpers a template legitimately touches
# (``loop.index``, ``ns.total``, ``cycler(...).next()``, macros).
_RUNTIME_TYPES: tuple[type, ...] = (
    LoopContext, Macro, Undefined, Namespace, Cycler, Joiner,
)
# The only global callables a template may invoke.
_SAFE_GLOBALS: dict[str, Any] = {
    # ``safe_range`` refuses ranges longer than ``sandbox.MAX_RANGE``.
    "range": safe_range,
    "dict": dict,
    "namespace": Namespace,
    "cycler": Cycler,
    "joiner": Joiner,
}
# ``attr`` reaches attributes by computed name; nothing here needs it.
_REMOVED_FILTERS = ("attr",)

class _TemplateSandbox(ImmutableSandboxedEnvironment):
    """Sandbox that only lets a template work with plain data.

    Jinja2's sandbox blocks underscore attributes and known-unsafe
    callables, which leaves every *other* attribute of every object in
    reach reachable.  This subclass inverts the rule: attribute access
    and calls are allowed only on plain data values and on Jinja2's own
    runtime helpers, so an object that slips into the context (a class,
    a function, a module, a bound method of something else) is inert.
    """

    intercepted_binops = frozenset(["*", "**"])

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Sandboxed stand-ins for ``str.format`` / ``str.format_map``
        # handed out by this environment (one environment per render).
        self._format_wrappers: list[Any] = []

    def wrap_str_format(self, value: Any) -> Any:
        wrapper = super().wrap_str_format(value)
        if wrapper is not None:
            self._format_wrappers.append(wrapper)
        return wrapper

    def is_safe_attribute(self, obj: Any, attr: str, value: Any) -> bool:
        if not isinstance(attr, str) or attr.startswith("_"):
            return False
        if isinstance(obj, type) or not isinstance(
            obj, _DATA_TYPES + _RUNTIME_TYPES
        ):
            return False
        return bool(super().is_safe_attribute(obj, attr, value))

    def is_safe_callable(self, obj: Any) -> bool:
        if obj is _BoundedIter or any(obj is allowed for allowed in _SAFE_GLOBALS.values()):
            return True
        if isinstance(obj, (Macro, LoopContext, Joiner)):
            return True
        if any(obj is wrapper for wrapper in self._format_wrappers):
            return True
        owner = getattr(obj, "__self__", None)
        # Bound method of a data value or runtime helper (``s.upper``,
        # ``d.items``, ``loop.cycle``).  ``__self__`` of a builtin
        # *function* is its module, which is not in either tuple.
        if (
            owner is not None
            and not isinstance(owner, type)
            and isinstance(owner, _DATA_TYPES + _RUNTIME_TYPES)
        ):
            return bool(super().is_safe_callable(obj))
        return False

    def call(__self, __context: Any, __obj: Any, *args: Any, **kwargs: Any) -> Any:
        # ``loop(children)`` in a ``recursive`` loop starts a new loop over
        # an iterable the ``for`` tag never saw; it counts against the same
        # budget.
        if isinstance(__obj, LoopContext) and args:
            args = (_BoundedIter(args[0]), *args[1:])
        return super().call(__context, __obj, *args, **kwargs)

    def call_binop(
        self, context: Any, operator: str, left: Any, right: Any
    ) -> Any:
        if operator == "*":
            for seq, count in ((left, right), (right, left)):
                if (
                    isinstance(seq, (str, list, tuple))
                    and isinstance(count, int)
                    and len(seq) * max(count, 0) > _MAX_REPEAT_LEN
                ):
                    raise SecurityError("repetition result is too large")
            if (
                isinstance(left, int)
                and isinstance(right, int)
                and left.bit_length() + right.bit_length() > _MAX_INT_BITS
            ):
                raise SecurityError("multiplication result is too large")
        elif operator == "**":
            if not isinstance(left, (int, float)) or not isinstance(
                right, (int, float)
            ):
                raise SecurityError("'**' is only supported on numbers")
            if abs(right) > _MAX_POW_EXPONENT or (
                isinstance(left, int)
                and isinstance(right, int)
                and left.bit_length() * max(right, 1) > _MAX_INT_BITS
            ):
                raise SecurityError("exponentiation result is too large")
        return super().call_binop(context, operator, left, right)

def build_environment() -> _TemplateSandbox:
    """A fresh sandbox; one per render (it carries the render's budget)."""
    env = _TemplateSandbox(undefined=StrictUndefined, autoescape=False)
    # Replace (not extend) the default globals: ``lipsum`` is a plain
    # function and nothing else in the defaults is needed.
    env.globals.clear()
    env.globals.update(_SAFE_GLOBALS)
    env.globals[_BUDGET_NAME] = _BoundedIter
    for name in _REMOVED_FILTERS:
        env.filters.pop(name, None)
    return env


def render(template_str: str, ctx: dict[str, Any]) -> str:
    """Render *template_str* over *ctx*; errors come back as ``[Error] …`` text."""
    try:
        env = build_environment()
        _BoundedIter.spent = 0
        template = env.from_string(_bound_loops(env.parse(template_str)))
        # A fresh dict, passed positionally: context keys never collide
        # with ``render()``'s own parameters, and the template only ever
        # sees JSON-decoded data.
        parts: list[str] = []
        total = 0
        data = {k: v for k, v in ctx.items() if k != _BUDGET_NAME}
        for chunk in template.generate(data):
            total += len(chunk)
            if total > _MAX_OUTPUT_CHARS:
                return f"[Error] Rendered output exceeds {_MAX_OUTPUT_CHARS} characters."
            parts.append(chunk)
        return "".join(parts)
    except SecurityError as exc:
        # Object reprs in the message carry nothing the model needs.
        reason = re.sub(r"<[^<>]*>", "this object", str(exc))
        return f"[Error] Template not allowed: {reason}"
    except TemplateError as exc:
        available = ", ".join(sorted(ctx.keys())) if ctx else "(none)"
        return f"[Error] Template error: {exc}. Available context variables: {available}"
    except (MemoryError, RecursionError) as exc:
        return f"[Error] Template needs too much memory or nesting ({type(exc).__name__})."
    except Exception as exc:
        return f"[Error] {type(exc).__name__}: {exc}"


def _limit_self(cpu_seconds: int, address_space_bytes: int) -> None:
    """Kernel-enforced backstops; the parent's kill is the limit that counts."""
    try:
        import resource
    except ImportError:  # pragma: no cover - platforms without rlimits
        return
    for name, value in (
        ("RLIMIT_CPU", cpu_seconds),
        ("RLIMIT_AS", address_space_bytes),
        ("RLIMIT_CORE", 0),
    ):
        limit = getattr(resource, name, None)
        if limit is None or value <= 0:
            continue
        try:
            _soft, hard = resource.getrlimit(limit)
            if hard != resource.RLIM_INFINITY:
                value = min(value, hard)
            resource.setrlimit(limit, (value, hard))
        except (ValueError, OSError):
            continue


def main(argv: list[str]) -> int:
    """Script entry: ``<cpu seconds> <address space bytes>``; JSON in, JSON out."""
    _limit_self(int(argv[1]), int(argv[2]))
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    output = render(str(request["template"]), dict(request["context"]))
    sys.stdout.buffer.write(json.dumps({"output": output}).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
