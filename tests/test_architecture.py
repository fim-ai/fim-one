"""Architecture guard tests.

The core layer (``fim_one.core``) must stay importable without dragging in the
FastAPI web layer (``fim_one.web``). Track B (2026-05) moved the ORM models from
``fim_one.web.models`` to ``fim_one.db.models`` precisely so that the database and
migration layers can populate ``Base.metadata`` without importing ``fim_one.web``.

These tests lock that property in so it cannot silently regress:

1. ``fim_one.core.{agent,model,planner}`` source contains NO reference to
   ``fim_one.web`` at all (the crown-jewel subpackages stay web-free).
2. No ``fim_one.core`` module imports ``fim_one.web`` at MODULE (top) level.
   Function-level (lazy) imports of ``fim_one.web.deps`` / ``fim_one.web.api.files``
   are still permitted — that residual coupling is tracked separately (Track B-2)
   and does not break standalone ``import fim_one.core.*``.
3. Importing ``fim_one.db.models`` in a fresh interpreter does NOT pull in
   ``fim_one.web`` (the actual guarantee the model move buys us).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "fim_one"
CORE = SRC / "core"

# Subpackages that must remain entirely free of any fim_one.web reference.
WEB_FREE_SUBPACKAGES = ("agent", "model", "planner")


class _ImportCollector(ast.NodeVisitor):
    """Collect every imported module path with whether it is module-level.

    An import is "top level" when no ``def``/``async def`` encloses it. Lazy
    (function-body) imports are recorded with ``top_level=False``.
    """

    def __init__(self) -> None:
        self._func_depth = 0
        self.imports: list[tuple[str, bool]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._func_depth += 1
        self.generic_visit(node)
        self._func_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._func_depth += 1
        self.generic_visit(node)
        self._func_depth -= 1

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append((alias.name, self._func_depth == 0))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # Only absolute imports carry a meaningful dotted module path here.
        if node.level == 0 and node.module:
            self.imports.append((node.module, self._func_depth == 0))


def _collect_imports(path: Path) -> list[tuple[str, bool]]:
    collector = _ImportCollector()
    collector.visit(ast.parse(path.read_text(encoding="utf-8")))
    return collector.imports


def _core_py_files() -> list[Path]:
    return sorted(CORE.rglob("*.py"))


class TestCrownJewelSubpackagesAreWebFree:
    """agent / model / planner must not reference fim_one.web in any form."""

    def test_no_web_reference_anywhere(self) -> None:
        offenders: list[str] = []
        for sub in WEB_FREE_SUBPACKAGES:
            pkg = CORE / sub
            if not pkg.exists():
                continue
            for py in sorted(pkg.rglob("*.py")):
                for module, _top in _collect_imports(py):
                    if module == "fim_one.web" or module.startswith("fim_one.web."):
                        rel = py.relative_to(SRC)
                        offenders.append(f"{rel}: imports {module}")
        assert not offenders, (
            "core.{agent,model,planner} must stay web-free, but found:\n  "
            + "\n  ".join(offenders)
        )


class TestCoreHasNoTopLevelWebImport:
    """No core module may import fim_one.web at module load time."""

    def test_no_module_level_web_import(self) -> None:
        offenders: list[str] = []
        for py in _core_py_files():
            for module, top_level in _collect_imports(py):
                if not top_level:
                    continue
                if module == "fim_one.web" or module.startswith("fim_one.web."):
                    rel = py.relative_to(SRC)
                    offenders.append(f"{rel}: top-level import of {module}")
        assert not offenders, (
            "core modules must not import fim_one.web at module level "
            "(use a function-level import if the web coupling is unavoidable):\n  "
            + "\n  ".join(offenders)
        )


class TestDbModelsImportsWithoutWeb:
    """Importing fim_one.db.models must not transitively import fim_one.web."""

    def test_db_models_does_not_pull_in_web(self) -> None:
        code = (
            "import sys\n"
            "import fim_one.db.models  # noqa: F401\n"
            "web = [m for m in sys.modules if m == 'fim_one.web' "
            "or m.startswith('fim_one.web.')]\n"
            "assert not web, 'fim_one.db.models pulled in web modules: ' + repr(web)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            "importing fim_one.db.models pulled in the web layer:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_core_crown_jewels_import_without_web(self) -> None:
        code = (
            "import sys\n"
            "import fim_one.core.agent, fim_one.core.model, fim_one.core.planner  # noqa: F401\n"
            "web = [m for m in sys.modules if m == 'fim_one.web' "
            "or m.startswith('fim_one.web.')]\n"
            "assert not web, 'core crown jewels pulled in web modules: ' + repr(web)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            "importing core.{agent,model,planner} pulled in the web layer:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
