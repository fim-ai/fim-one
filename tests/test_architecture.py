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
import re
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "fim_one"
CORE = SRC / "core"

# Subpackages that must remain entirely free of any fim_one.web reference.
# ``assembly`` is where the rules shared by every entry point live; data only
# the web layer has reaches it through ports the web layer registers.
WEB_FREE_SUBPACKAGES = ("agent", "model", "planner", "assembly")


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
    """agent / model / planner / assembly must not reference fim_one.web in any form."""

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
            "core.{agent,model,planner,assembly} must stay web-free, but found:\n  "
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


class TestCoreDoesNotReachIntoChatOrTheHookBootstrap:
    """An agent's runtime reaches core through the ``core.assembly.runtime`` port.

    ``call_agent`` and the workflow Agent step used to import chat's private
    resolvers and the web hook bootstrap inside their functions.
    """

    FORBIDDEN = ("fim_one.web.api.chat", "fim_one.web.hooks_bootstrap")

    def test_no_import_at_any_level(self) -> None:
        offenders = [
            f"{py.relative_to(SRC)}: {module}"
            for py in _core_py_files()
            for module, _top in _collect_imports(py)
            if module in self.FORBIDDEN
        ]
        assert not offenders, (
            "core modules get an agent runtime from fim_one.core.assembly.runtime:\n  "
            + "\n  ".join(offenders)
        )


class TestCoreDoesNotImportWebVisibility:
    """The visibility rule lives in core.assembly; core must not reach back.

    Function-level imports count here: ``fim_one.web.visibility`` is only a
    re-export for the list pages under ``web/api``.
    """

    def test_no_import_of_web_visibility(self) -> None:
        offenders: list[str] = []
        for py in _core_py_files():
            for module, _top in _collect_imports(py):
                if module == "fim_one.web.visibility":
                    offenders.append(str(py.relative_to(SRC)))
        assert not offenders, (
            "core modules must use fim_one.core.assembly.visibility, but these "
            "import fim_one.web.visibility:\n  " + "\n  ".join(offenders)
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


def _source_lines(path: Path) -> list[tuple[int, str]]:
    return list(enumerate(path.read_text(encoding="utf-8").splitlines(), start=1))


def _occurrences(needle: str, files: list[Path]) -> list[str]:
    return [
        f"{py.relative_to(SRC)}:{lineno}: {needle}"
        for py in files
        for lineno, line in _source_lines(py)
        if needle in line
    ]


# The append-only record tables, by model and by table name.
_RECORD_MODELS = frozenset({"AuditLog", "LoginHistory", "ReviewLog", "ConnectorCallLog"})
_RECORD_TABLES = frozenset({"audit_logs", "login_history", "review_log", "connector_call_logs"})
_Q = r"[\"'`\[]?"
_RAW_WRITE = re.compile(
    r"\b(?:delete\s+from|update|truncate(?:\s+table)?|drop\s+table(?:\s+if\s+exists)?"
    r"|alter\s+table(?:\s+if\s+exists)?)"
    r"(?:\s+only)?\s+"
    # An optional schema: ``public.audit_logs``, ``"public"."audit_logs"``.
    + rf"(?:{_Q}\w+{_Q}\s*\.\s*)?{_Q}(?:"
    + "|".join(sorted(_RECORD_TABLES))
    + r")\b",
    re.IGNORECASE,
)
# SQLAlchemy's statement builders, and the session methods that change rows
# of a model given as their first argument.
_DML = frozenset({"delete", "update", "Delete", "Update"})
_BULK = frozenset({"bulk_update_mappings", "bulk_save_objects"})


def _dml_names(tree: ast.AST) -> dict[str, str]:
    """The names ``delete`` and ``update`` are called by in *tree*: ``{"sa_update": "update"}``."""
    names = {verb: verb for verb in _DML}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sqlalchemy"):
            for alias in node.names:
                if alias.name in _DML:
                    names[alias.asname or alias.name] = alias.name.lower()
    return {name: verb.lower() for name, verb in names.items()}


def _verb_of(func: ast.expr, dml: dict[str, str]) -> str | None:
    if isinstance(func, ast.Name):
        return dml.get(func.id)
    if isinstance(func, ast.Attribute) and func.attr in _DML:
        return func.attr.lower()
    return None


def generic_writers(source: str) -> dict[str, frozenset[int | str]]:
    """Functions of *source* that ``delete()`` / ``update()`` a model they are given.

    ``{"_delete_older": {0, "model"}}``: the position (a method's ``self``
    not counted) and the name of each parameter that reaches a
    ``delete(param)`` / ``update(param)`` in the body.
    """
    tree = ast.parse(source)
    dml = _dml_names(tree)
    found: dict[str, frozenset[int | str]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        params = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]
        # A method is called without its ``self``.
        if params[:1] in (["self"], ["cls"]):
            params = params[1:]
        named = params + [a.arg for a in fn.args.kwonlyargs]
        reached: set[int | str] = set()
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call) or _verb_of(node.func, dml) is None:
                continue
            target = node.args[0] if node.args else None
            if isinstance(node.func, ast.Attribute) and not node.args:
                target = node.func.value  # ``param.__table__.delete()``
            while isinstance(target, ast.Attribute):
                target = target.value
            if isinstance(target, ast.Name) and target.id in named:
                reached.add(target.id)
                if target.id in params:
                    reached.add(params.index(target.id))
        if reached:
            found[fn.name] = frozenset(reached)
    return found


def _append_only_offences(
    source: str, helpers: dict[str, frozenset[int | str]] | None = None
) -> list[str]:
    """Where *source* changes or removes rows of the four record tables.

    Read from the syntax tree, so aliases and line breaks do not hide it:

    - ``delete(X)`` / ``update(X)`` (also ``sa.delete``, and either under
      any name it is imported as: ``update as sa_update``), and
      ``session.bulk_update_mappings(X, ...)``, whose first argument is one
      of the models, under any imported name or a name it is assigned to,
      as ``module.Model``, or its ``__table__``;
    - ``X.__table__.delete()`` / ``.update()``, and
      ``metadata.tables["audit_logs"].delete()``, also through a name the
      table is assigned to;
    - ``session.delete(row)`` in a module that imports one of the models,
      unless what ``row`` is can be seen to be another model (its
      annotation, or what it was assigned from in the same function);
    - an attribute assigned on, or ``setattr`` of, what can be seen to be a
      row of one of the models (``row = await db.get(AuditLog, i)``;
      ``row.detail = None``): the session writes the change at the commit;
    - a call that hands one of the models to a function that deletes or
      updates the model it is given (*helpers*, from
      :func:`generic_writers` of every module; the module's own are added);
    - raw SQL: ``DELETE FROM`` / ``UPDATE`` / ``TRUNCATE`` / ``DROP TABLE``
      / ``ALTER TABLE`` on a table name, with or without a schema and
      ``ONLY``, across line breaks and implicitly joined string pieces.

    What it cannot see: a table or model name built at run time
    (``"audit" + "_logs"``, ``getattr(models, name)``), and a row handed in
    from another module in a module that imports none of the models.
    """
    tree = ast.parse(source)
    dml = _dml_names(tree)
    known = dict(helpers or {})
    known.update(generic_writers(source))
    aliases: set[str] = set(_RECORD_MODELS)
    tables: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for name in node.names:
                if name.name in _RECORD_MODELS:
                    aliases.add(name.asname or name.name)
    imports_a_model = any(
        (
            isinstance(node, ast.ImportFrom)
            and any(n.name in _RECORD_MODELS for n in node.names)
        )
        or (isinstance(node, ast.Attribute) and node.attr in _RECORD_MODELS)
        for node in ast.walk(tree)
    )
    # ``Model = AuditLog``, ``t = Base.metadata.tables["audit_logs"]``,
    # followed until nothing new is bound.
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                continue
            bound = node.targets[0].id
            if bound not in tables and (
                _is_table(node.value, aliases)
                or (isinstance(node.value, ast.Name) and node.value.id in tables)
            ):
                tables.add(bound)
                changed = True
            elif bound not in aliases and _is_record_model(node.value, aliases):
                aliases.add(bound)
                changed = True

    def is_table(node: ast.expr) -> bool:
        return _is_table(node, aliases) or (isinstance(node, ast.Name) and node.id in tables)

    hits: list[str] = []
    raw_lines: set[int] = set()
    for match in _RAW_WRITE.finditer(source):
        raw_lines.add(source.count("\n", 0, match.start()) + 1)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _RAW_WRITE.search(node.value)
        ):
            raw_lines.add(node.lineno)
    hits.extend(f"{lineno}: raw SQL on a record table" for lineno in sorted(raw_lines))

    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]

    def scope_of(node: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        """The innermost function *node* is in."""
        return max(
            (f for f in functions if any(n is node for n in ast.walk(f))),
            key=lambda f: f.lineno,
            default=None,
        )

    def is_a_record(node: ast.expr, at: ast.AST) -> bool:
        return _what_it_is(node, scope_of(at), aliases) == "record"

    for node in ast.walk(tree):
        # ``row.detail = None`` on a loaded record.
        if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            hits.extend(
                f"{node.lineno}: attribute set on a record"
                for target in targets
                if isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and is_a_record(target.value, node)
            )
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        callee = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if callee == "setattr" and node.args and is_a_record(node.args[0], node):
            hits.append(f"{node.lineno}: setattr() on a record")
            continue
        if callee in _BULK and node.args and _is_record_model(node.args[0], aliases):
            hits.append(f"{node.lineno}: {callee}() of a record table")
            continue
        if callee in known:
            reached = known[callee]
            handed = [arg for i, arg in enumerate(node.args) if i in reached] + [
                kw.value for kw in node.keywords if kw.arg in reached
            ]
            if any(_is_record_model(arg, aliases, table=True) or is_table(arg) for arg in handed):
                hits.append(f"{node.lineno}: {callee}() is handed a record table")
                continue
        verb = _verb_of(func, dml)
        if verb is None:
            continue
        if (
            node.args
            and (_is_record_model(node.args[0], aliases, table=True) or is_table(node.args[0]))
        ) or (isinstance(func, ast.Attribute) and is_table(func.value)):
            hits.append(f"{node.lineno}: {verb}() of a record table")
        elif (
            verb == "delete"
            and imports_a_model
            and isinstance(func, ast.Attribute)
            and len(node.args) == 1
            and not node.keywords
            # ``@router.delete("/path")``
            and not isinstance(node.args[0], ast.Constant)
            and _what_it_is(node.args[0], scope_of(node), aliases) != "other"
        ):
            hits.append(f"{node.lineno}: session.delete() of what may be a record")
    return hits


def _is_table(node: ast.expr, aliases: set[str]) -> bool:
    """``X.__table__`` of a model, or ``....tables["audit_logs"]``."""
    if isinstance(node, ast.Attribute) and node.attr == "__table__":
        return _is_record_model(node.value, aliases)
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value in _RECORD_TABLES
    )


def _is_record_model(node: ast.expr, aliases: set[str], *, table: bool = False) -> bool:
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Attribute):
        if node.attr in _RECORD_MODELS:
            return True
        if table and node.attr == "__table__":
            return _is_record_model(node.value, aliases)
        return False
    if isinstance(node, ast.Constant):
        return False
    return table and _is_table(node, aliases)


def _what_it_is(
    node: ast.expr, scope: ast.FunctionDef | ast.AsyncFunctionDef | None, aliases: set[str]
) -> str | None:
    """``"record"``, ``"other"`` (another model), or ``None`` (cannot tell)."""
    if scope is None:
        return None
    seen: set[str] = set()

    def judge(expr: ast.AST | None, depth: int) -> str | None:
        if expr is None or depth > 6:
            return None
        verdict: str | None = None
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Name | ast.Attribute) and _is_record_model(sub, aliases):
                return "record"
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if sub.value in _RECORD_MODELS:
                    return "record"
                if sub.value[:1].isupper():
                    verdict = "other"
            if isinstance(sub, ast.Name):
                if sub.id[:1].isupper():
                    verdict = verdict or "other"
                elif sub.id not in seen:
                    seen.add(sub.id)
                    inner = judge_name(sub.id, depth + 1)
                    if inner == "record":
                        return "record"
                    verdict = verdict or inner
        return verdict

    def judge_name(name: str, depth: int) -> str | None:
        verdict: str | None = None
        args = scope.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if arg.arg == name and arg.annotation is not None:
                verdict = judge(arg.annotation, depth)
        for sub in ast.walk(scope):
            value: ast.AST | None = None
            if isinstance(sub, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in sub.targets
            ):
                value = sub.value
            elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                if sub.target.id == name:
                    value = sub.annotation
            elif isinstance(sub, ast.For | ast.AsyncFor | ast.comprehension) and any(
                isinstance(t, ast.Name) and t.id == name for t in ast.walk(sub.target)
            ):
                value = sub.iter
            if value is not None:
                inner = judge(value, depth)
                if inner == "record":
                    return "record"
                verdict = verdict or inner
        return verdict

    return judge(node, 0)


# Reading a credential, deciding on fallback, writing a call record or
# building a connector / MCP tool: none of it in the entry points.
ENTRY_POINT_GATE = (
    "allow_fallback",
    "decrypt_credential(",
    "decrypt_db_config(",
    "resolve_connector_credentials(",
    "load_connector_credential(",
    "ConnectorCallLog(",
    "ConnectorCredential",
    "MCPServerCredential",
    # the fields of a personal MCP credential
    "env_blob",
    "headers_blob",
    "ConnectorToolAdapter(",
    "build_connector_meta_tool(",
    "build_database_meta_tool(",
    "build_mcp_meta_tool(",
)
# Reading the secrets stored on an MCP server row (``server.env``,
# ``_srv.headers``): the row's owner may be someone else.
MCP_SERVER_SECRET_READ = re.compile(r"\b\w*(?:server|srv|mcp)\w*\.(?:env|headers)\b", re.I)
# The workflow's own env vars (not a connector or MCP credential) are
# decrypted where the run identity is decided.
ENTRY_POINT_EXCEPTIONS = frozenset({("core/workflow/run_service.py", "decrypt_credential(")})

# constructor -> the files that may call it, besides core/assembly/toolset.py
# (its own definition, and callers that are not a user's run).
TOOL_CONSTRUCTORS: dict[str, frozenset[str]] = {
    "ConnectorToolAdapter(": frozenset(
        {
            "core/tool/connector/adapter.py",
            # the connector meta-tool builds one adapter per executed action
            "core/tool/connector/meta_tool.py",
        }
    ),
    "build_connector_meta_tool(": frozenset({"core/tool/connector/meta_tool.py"}),
    "build_database_meta_tool(": frozenset({"core/tool/connector/database/meta_tool.py"}),
    "DatabaseToolAdapter.create_tools(": frozenset(),
    "build_mcp_meta_tool(": frozenset({"core/mcp/meta_tool.py"}),
    "MCPClient(": frozenset(
        {
            "core/mcp/client.py",
            "web/api/mcp_servers.py",  # the owner's "test connection"
        }
    ),
}


class TestCredentialsAndCallRecordsLiveInTheAssemblyLayer:
    """Chat and workflow nodes ask ``core.assembly``; they do not carry copies.

    S9 was a second, incomplete copy of the connector assembly (no
    credentials, no call record), and N1 two copies of the MCP credential
    rule that were wrong in different ways. These text gates keep the rule
    and the record in one place: comments count, so a rule is not described
    where it is not implemented.
    """

    def test_entry_points_hold_no_copy(self) -> None:
        entry_points = [SRC / "web" / "api" / "chat.py", *sorted((CORE / "workflow").rglob("*.py"))]
        offenders = [
            hit
            for needle in ENTRY_POINT_GATE
            for hit in _occurrences(needle, entry_points)
            if (hit.split(":", 1)[0], needle) not in ENTRY_POINT_EXCEPTIONS
        ]
        offenders += [
            f"{py.relative_to(SRC)}:{lineno}: {match.group(0)}"
            for py in entry_points
            for lineno, line in _source_lines(py)
            if (match := MCP_SERVER_SECRET_READ.search(line))
        ]
        assert not offenders, (
            "chat.py and core/workflow must go through fim_one.core.assembly "
            "(credentials, toolset, calls):\n  " + "\n  ".join(offenders)
        )

    def test_user_run_tools_are_built_only_by_the_toolset(self) -> None:
        files = [py for py in sorted(SRC.rglob("*.py")) if "migrations" not in py.parts]
        offenders: list[str] = []
        for needle, callers in TOOL_CONSTRUCTORS.items():
            allowed = callers | {"core/assembly/toolset.py"}
            offenders += [
                hit for hit in _occurrences(needle, files) if hit.split(":", 1)[0] not in allowed
            ]
        assert not offenders, (
            "connector, database and MCP tools for a user's run are built in "
            "core/assembly/toolset.py, where the credential is resolved and the "
            "call record wired:\n  " + "\n  ".join(offenders)
        )

    def test_call_records_have_one_writer(self) -> None:
        files = [py for py in sorted(SRC.rglob("*.py")) if "migrations" not in py.parts]
        allowed = {"core/assembly/calls.py", "db/models/connector_call_log.py"}
        offenders = [
            hit
            for hit in _occurrences("ConnectorCallLog(", files)
            if hit.split(":", 1)[0] not in allowed
        ]
        assert not offenders, (
            "call records are written by core/assembly/calls.py:\n  " + "\n  ".join(offenders)
        )

    def test_database_configs_are_decrypted_for_the_owner_only(self) -> None:
        files = [py for py in sorted(SRC.rglob("*.py")) if "migrations" not in py.parts]
        allowed = {
            "core/security/encryption.py",
            "core/assembly/credentials.py",
            # owner-only configuration paths, not a user's run
            "core/tool/builtin/db_builder.py",
            "web/api/db_connectors.py",
        }
        offenders = [
            hit
            for hit in _occurrences("decrypt_db_config(", files)
            if hit.split(":", 1)[0] not in allowed
        ]
        assert not offenders, (
            "a database connector's config is decrypted by resolve_credential(), "
            "which gives it to the owner only:\n  " + "\n  ".join(offenders)
        )


# ---------------------------------------------------------------------------
# Confirmation and approval: one service (C1 phase 3)
# ---------------------------------------------------------------------------

# needle -> the files (relative to src/fim_one) that may contain it.
CONFIRMATION_GATE: dict[str, frozenset[str]] = {
    # The gate is ApprovalGateHook; the old name lives on only as a re-export.
    "FeishuGateHook": frozenset({"core/hooks/feishu_gate_hook.py", "core/hooks/__init__.py"}),
    # Requests are written by the service alone (the model defines the class).
    "ConfirmationRequest(": frozenset({"core/assembly/confirmation.py", "db/models/channel.py"}),
    # The second approvals table and its model are gone (T10 P5).
    "WorkflowApproval(": frozenset(),
    "workflow_approvals": frozenset(),
    # The two former copies of "who may decide".
    "user_may_decide": frozenset(),
    "_get_decidable_approval": frozenset(),
}
# The service asks whether a channel can carry approvals, never which platform it is.
PLATFORM_CHECK = re.compile(r"isinstance\(.*FeishuChannel")


class TestConfirmationsHaveOneService:
    """Asking, waiting and deciding live in ``core/assembly/confirmation.py``.

    The approval gate, the portal endpoints, the Feishu callback, the
    Approval Playground, ``ask_user_question`` and the workflow approval
    step all go through ``ConfirmationService``; none writes a request row
    or decides who may answer on its own.
    """

    def test_one_writer_and_one_decider(self) -> None:
        files = [py for py in sorted(SRC.rglob("*.py")) if "migrations" not in py.parts]
        offenders = [
            hit
            for needle, allowed in CONFIRMATION_GATE.items()
            for hit in _occurrences(needle, files)
            if hit.split(":", 1)[0] not in allowed
        ]
        assert not offenders, (
            "confirmation requests are created and decided by "
            "core/assembly/confirmation.py:\n  " + "\n  ".join(offenders)
        )

    def test_the_assembly_layer_does_not_name_a_platform(self) -> None:
        offenders = [
            f"{py.relative_to(SRC)}:{lineno}: {line.strip()}"
            for py in sorted((CORE / "assembly").rglob("*.py"))
            for lineno, line in _source_lines(py)
            if PLATFORM_CHECK.search(line)
        ]
        assert not offenders, (
            "the assembly layer asks for ApprovalCarrier, not for a channel class:\n  "
            + "\n  ".join(offenders)
        )

    def test_the_gate_is_registered_under_its_new_key_and_its_old_one(self) -> None:
        from fim_one.web.hooks_bootstrap import HOOK_FACTORIES

        assert HOOK_FACTORIES["approval_gate"] is HOOK_FACTORIES["feishu_gate"]


# The quota chain's former homes: chat's private helpers and the gate that
# wrapped them. Comments count, so nothing points at a name that is gone.
QUOTA_FORMER_COPIES = (
    "_check_token_quota",
    "_get_quota_status",
    "_get_plan_quota_for_user",
    "ChatRunGate",
)


class TestQuotaHasOneGate:
    """Plan and quota are checked by ``core/assembly/quota.py``.

    Chat, the AI-assisted endpoints and the workflow run service all call
    it; the numbers come from ``web/services/quota_backend.py`` through the
    registered port.
    """

    def test_the_former_copies_are_gone(self) -> None:
        files = [py for py in sorted(SRC.rglob("*.py")) if "migrations" not in py.parts]
        offenders = [hit for needle in QUOTA_FORMER_COPIES for hit in _occurrences(needle, files)]
        assert not offenders, (
            "quota is checked by fim_one.core.assembly.quota:\n  " + "\n  ".join(offenders)
        )


# What chat's endpoints and tools used to call directly to assemble an agent.
# In chat.py each name may appear once: where it is defined.
CHAT_ASSEMBLY_HELPERS = (
    "_resolve_tools(",
    "_connect_pending_mcp_servers(",
    "_resolve_llm(",
)


class TestAgentsAreAssembledByTheRuntime:
    """Chat turns, ``call_agent`` delegates and workflow Agent steps get an
    agent's models, tools and hooks from ``web/services/agent_runtime.py``
    (through the ``core.assembly.runtime`` port outside the web layer)."""

    def test_chat_calls_the_runtime_stages_not_its_own_helpers(self) -> None:
        chat = SRC / "web" / "api" / "chat.py"
        # The vision model falls back to the main one inside its own resolver.
        vision_fallback = "return await _resolve_llm(agent_cfg, db)"
        offenders = [
            f"{chat.relative_to(SRC)}:{lineno}: {line.strip()}"
            for needle in CHAT_ASSEMBLY_HELPERS
            for lineno, line in _source_lines(chat)
            if needle in line and f"def {needle}" not in line and vision_fallback not in line
        ]
        offenders += _occurrences("build_hook_registry_for_agent", [chat])
        assert not offenders, (
            "chat.py assembles through fim_one.web.services.agent_runtime:\n  "
            + "\n  ".join(offenders)
        )

    def test_call_agent_holds_no_resolver_of_its_own(self) -> None:
        tool = CORE / "tool" / "builtin" / "call_agent.py"
        offenders = [
            hit
            for needle in ("fim_one.web", "_resolver", "get_llm_from_config", "get_model_registry")
            for hit in _occurrences(needle, [tool])
        ]
        assert not offenders, (
            "call_agent builds its delegate with core.assembly.runtime:\n  "
            + "\n  ".join(offenders)
        )


class TestWorkflowStepsHoldNoRuleOfTheirOwn:
    """T10 section 2 and the P4 gate: ``core/workflow`` asks ``core.assembly``.

    Nothing under ``core/workflow`` decides who may see a resource or reads
    the fallback switch: no visibility query, no subscription lookup, no
    share-state clause, no ``allow_fallback``, no owner-only predicate for
    raw SQL. That covers the steps and the import check, which
    used to read the subscription table itself. Credentials and call
    records are gated by ``TestCredentialsAndCallRecordsLiveInTheAssemblyLayer``
    (with the workflow's own env vars as the one exception), and no step
    imports the web layer for an agent's runtime.
    """

    def test_no_visibility_rule_in_core_workflow(self) -> None:
        files = sorted((CORE / "workflow").rglob("*.py"))
        offenders = [
            hit
            for needle in (
                "resolve_visibility",
                "build_visibility_filter",
                "visibility_clause",
                "visible_clause_for",
                "use_clause_for",
                "ResourceSubscription",
                "allow_fallback",
                # whether a subscribed resource is still shared
                "is_shared_clause",
                # who may run raw SQL on a database connector (its owner)
                "db_raw_sql_tool_allowed",
            )
            for hit in _occurrences(needle, files)
        ]
        assert not offenders, (
            "core/workflow asks fim_one.core.assembly.visibility.authorize_use:\n  "
            + "\n  ".join(offenders)
        )


REPO = SRC.parent.parent
# Every tree that holds Python importing the package.
PYTHON_TREES = ("src", "tests", "evals", "scripts", "examples")
REMOVED_NOTIFICATION_PACKAGE = "fim_one.core.notification"
INSTANCE_WEBHOOK_VARIABLES = ("LARK_WEBHOOK_URL", "SLACK_WEBHOOK_URL", "WECOM_WEBHOOK_URL")
INSTANCE_CHANNELS_MODULE = "core/channels/instance.py"


def _repo_py_files() -> list[Path]:
    return sorted(
        py
        for tree in PYTHON_TREES
        if (REPO / tree).is_dir()
        for py in (REPO / tree).rglob("*.py")
        if ".venv" not in py.parts and "node_modules" not in py.parts
    )


def _imported_modules(path: Path) -> list[tuple[int, str]]:
    """``(line, dotted module)`` of every import in *path*, relative ones resolved.

    ``from pkg import name`` yields both ``pkg`` and ``pkg.name``: the name
    may be a submodule. A file that does not parse yields nothing.
    """
    package: list[str] = []
    if SRC in path.parents:
        package = ["fim_one", *path.relative_to(SRC).parts[:-1]]
    found: list[tuple[int, str]] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        # A fixture that is broken on purpose imports nothing.
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)] if package else []
                module = ".".join([*base, *(node.module.split(".") if node.module else [])])
            else:
                module = node.module or ""
            found.append((node.lineno, module))
            found.extend((node.lineno, f"{module}.{alias.name}") for alias in node.names)
    return found


class TestOneOutboundStack:
    """Phase 5: everything that leaves for a chat or a mailbox is a channel.

    The notification providers became channel types under
    ``core/channels``; the package they lived in is gone and must not come
    back, and the instance-wide webhook variables (deprecated) are read in
    one module so that removing them is one deletion.
    """

    def test_the_notification_package_is_not_imported_anywhere(self) -> None:
        removed = REMOVED_NOTIFICATION_PACKAGE
        pattern = re.compile(re.escape(removed) + r"\b")
        offenders: list[str] = []
        for py in _repo_py_files():
            if py == Path(__file__).resolve():
                continue
            where = py.relative_to(REPO)
            offenders.extend(
                f"{where}:{lineno}: imports {module}"
                for lineno, module in _imported_modules(py)
                if module == removed or module.startswith(removed + ".")
            )
            # importlib / patch targets name the module in a string.
            offenders.extend(
                f"{where}:{lineno}: names {removed}"
                for lineno, line in _source_lines(py)
                if pattern.search(line)
            )
        # A checkout that had the package keeps an ignored ``__pycache__``
        # there after the pull that deletes it; only source brings it back.
        revived = sorted(p.name for p in (CORE / "notification").glob("**/*.py"))
        assert not revived, (
            "core/notification is back; a new outbound type belongs in "
            f"core/channels: {revived}"
        )
        assert not offenders, (
            f"{removed} was removed; use fim_one.core.channels:\n  "
            + "\n  ".join(sorted(set(offenders)))
        )

    def test_the_instance_webhook_variables_are_read_in_one_module(self) -> None:
        files = [
            py
            for py in sorted(SRC.rglob("*.py"))
            if py.relative_to(SRC).as_posix() != INSTANCE_CHANNELS_MODULE
        ]
        offenders = [
            hit for name in INSTANCE_WEBHOOK_VARIABLES for hit in _occurrences(name, files)
        ]
        assert not offenders, (
            f"only {INSTANCE_CHANNELS_MODULE} may name the instance-wide webhook "
            "variables; ask it for the channel (instance_channel / instance_webhooks):\n  "
            + "\n  ".join(offenders)
        )
        # The gate looks at something: the one module does read all three.
        instance = (SRC / INSTANCE_CHANNELS_MODULE).read_text(encoding="utf-8")
        assert all(name in instance for name in INSTANCE_WEBHOOK_VARIABLES)


class TestAuditRowsHaveOneWriter:
    """``core/audit/writer.py`` constructs every audit-class row.

    Three tables hold audit-class records. A row built anywhere else skips
    what the writer adds for all of them: the request's address, the action
    registry, the redaction of secrets.
    """

    ROWS = ("AuditLog(", "LoginHistory(", "ReviewLog(")
    MODELS = frozenset(
        {"db/models/audit_log.py", "db/models/login_history.py", "db/models/review_log.py"}
    )

    def test_rows_are_built_only_by_the_writer(self) -> None:
        files = [py for py in sorted(SRC.rglob("*.py")) if "migrations" not in py.parts]
        allowed = self.MODELS | {"core/audit/writer.py"}
        offenders = [
            hit
            for needle in self.ROWS
            for hit in _occurrences(needle, files)
            if hit.split(":", 1)[0] not in allowed
        ]
        assert not offenders, (
            "audit, sign-in and review rows are written by core/audit/writer.py:\n  "
            + "\n  ".join(offenders)
        )

    def test_audit_rows_are_never_changed_or_deleted(self) -> None:
        """Append-only: no code path edits or removes an audit record, a
        sign-in, a review entry or a call record. Two exceptions: the
        retention sweep, which removes what is older than its period, and
        user deletion, which takes the deleted user's identity out of the
        rows that stay. See :func:`_append_only_offences` for what is read."""
        allowed = {"core/audit/retention.py", "web/services/user_deletion.py"}
        files = [py for py in sorted(SRC.rglob("*.py")) if "migrations" not in py.parts]
        # A function anywhere (the sweep's own included) that deletes or
        # updates the model it is handed may not be handed a record table.
        helpers: dict[str, frozenset[int | str]] = {}
        for py in files:
            for name, reached in generic_writers(py.read_text(encoding="utf-8")).items():
                helpers[name] = helpers.get(name, frozenset()) | reached
        offenders = [
            f"{py.relative_to(SRC)}:{hit}"
            for py in files
            if str(py.relative_to(SRC)) not in allowed
            for hit in _append_only_offences(py.read_text(encoding="utf-8"), helpers)
        ]
        assert not offenders, "audit records are append-only:\n  " + "\n  ".join(offenders)

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param(
                'from sqlalchemy import text\n'
                'async def _g(db):\n    await db.execute(text("DELETE FROM audit_logs"))\n',
                id="raw-sql",
            ),
            pytest.param(
                "from fim_one.db.models import AuditLog\n"
                "async def _g(db):\n    await db.execute(AuditLog.__table__.delete())\n",
                id="table-delete",
            ),
            pytest.param(
                "from sqlalchemy import update\nfrom fim_one.db.models import AuditLog\n"
                "async def _g(db):\n    await db.execute(update(\n        AuditLog\n"
                "    ).values(detail=None))\n",
                id="update-split-over-lines",
            ),
            pytest.param(
                "from sqlalchemy import delete\nfrom fim_one.db.models import AuditLog as Log\n"
                "async def _g(db):\n    await db.execute(delete(Log))\n",
                id="aliased-model",
            ),
            pytest.param(
                "from fim_one.db.models import AuditLog\n"
                "async def _g(db, row):\n    await db.delete(row)\n",
                id="orm-delete-of-an-unknown-row",
            ),
            pytest.param(
                "from sqlalchemy import delete\nfrom fim_one.db.models import AuditLog\n"
                "async def _g(db):\n    await db.execute(delete(AuditLog))\n",
                id="plain",
            ),
            pytest.param(
                "import sqlalchemy as sa\nfrom fim_one.db import models\n"
                "async def _g(db):\n    await db.execute(sa.update(models.LoginHistory))\n",
                id="module-attribute",
            ),
            pytest.param(
                "from fim_one.db.models import ReviewLog\nModel = ReviewLog\n"
                "async def _g(db):\n    await db.execute(Model.__table__.update())\n",
                id="model-assigned-to-a-name",
            ),
            pytest.param(
                "from fim_one.db.models import ConnectorCallLog\n"
                "async def _g(db, call_id):\n"
                "    row = await db.get(ConnectorCallLog, call_id)\n    await db.delete(row)\n",
                id="orm-delete-of-a-call-record",
            ),
            pytest.param(
                "from sqlalchemy import text\n"
                "async def _g(db):\n"
                "    await db.execute(text('update \"review_log\" set actor_username = 1'))\n",
                id="raw-update",
            ),
            pytest.param(
                "from sqlalchemy import update as sa_update\n"
                "from fim_one.db.models import AuditLog\n"
                "async def _g(db):\n"
                "    await db.execute(sa_update(AuditLog).values(detail=None))\n",
                id="update-imported-under-another-name",
            ),
            pytest.param(
                "from sqlalchemy import delete as sa_delete\n"
                "from fim_one.db.models import LoginHistory\n"
                "async def _g(db):\n    await db.execute(sa_delete(LoginHistory))\n",
                id="delete-imported-under-another-name",
            ),
            pytest.param(
                "from fim_one.db.models.audit_log import AuditLog\n"
                "async def _g(db, i):\n    row = await db.get(AuditLog, i)\n"
                "    row.detail = None\n    await db.commit()\n",
                id="attribute-set-on-a-loaded-row",
            ),
            pytest.param(
                "from sqlalchemy import select\nfrom fim_one.db.models import ReviewLog\n"
                "async def _g(db):\n"
                "    for entry in (await db.execute(select(ReviewLog))).scalars():\n"
                "        entry.note += '!'\n",
                id="attribute-changed-on-each-selected-row",
            ),
            pytest.param(
                "from fim_one.db.models.audit_log import AuditLog\n"
                "async def _g(db, i):\n    row = await db.get(AuditLog, i)\n"
                "    setattr(row, 'detail', None)\n",
                id="setattr-on-a-loaded-row",
            ),
            pytest.param(
                'from sqlalchemy import text\n'
                'async def _g(db):\n    await db.execute(text("DELETE FROM public.audit_logs"))\n',
                id="raw-sql-with-a-schema",
            ),
            pytest.param(
                'from sqlalchemy import text\n'
                'async def _g(db):\n'
                '    await db.execute(text(\'DELETE FROM "public"."login_history"\'))\n',
                id="raw-sql-with-a-quoted-schema",
            ),
            pytest.param(
                'from sqlalchemy import text\n'
                'async def _g(db):\n    await db.execute(text("""DELETE FROM\n'
                '        audit_logs WHERE 1 = 1"""))\n',
                id="raw-sql-over-two-lines",
            ),
            pytest.param(
                'from sqlalchemy import text\n'
                'async def _g(db):\n    await db.execute(text("DELETE FROM "\n'
                '                          "review_log"))\n',
                id="raw-sql-in-joined-pieces",
            ),
            pytest.param(
                'from sqlalchemy import text\n'
                'async def _g(db):\n'
                '    await db.execute(text("UPDATE ONLY audit_logs SET detail = NULL"))\n',
                id="raw-update-only",
            ),
            pytest.param(
                'from sqlalchemy import text\n'
                'async def _g(db):\n    await db.execute(text("DROP TABLE login_history"))\n',
                id="raw-drop-table",
            ),
            pytest.param(
                'from sqlalchemy import text\n'
                'async def _g(db):\n'
                '    await db.execute(text("TRUNCATE connector_call_logs"))\n',
                id="raw-truncate",
            ),
            pytest.param(
                "from fim_one.db.base import Base\n"
                "async def _g(db):\n    t = Base.metadata.tables['audit_logs']\n"
                "    await db.execute(t.delete())\n",
                id="table-from-metadata-bound-to-a-name",
            ),
            pytest.param(
                "from fim_one.db.models.audit_log import AuditLog\n"
                "async def _g(db):\n"
                "    await db.run_sync(\n"
                "        lambda s: s.bulk_update_mappings(AuditLog, [{'id': '1'}])\n"
                "    )\n",
                id="bulk-update-mappings",
            ),
            pytest.param(
                "from sqlalchemy import delete\n"
                "async def _wipe(db, model):\n    await db.execute(delete(model))\n"
                "async def _g(db):\n"
                "    from fim_one.db.models.audit_log import AuditLog\n"
                "    await _wipe(db, AuditLog)\n",
                id="generic-helper-handed-a-record-model",
            ),
            pytest.param(
                "from sqlalchemy import update\n"
                "async def _touch(db, *, model):\n    await db.execute(update(model))\n"
                "async def _g(db):\n"
                "    from fim_one.db.models import ConnectorCallLog\n"
                "    await _touch(db, model=ConnectorCallLog)\n",
                id="generic-helper-handed-a-record-model-by-keyword",
            ),
        ],
    )
    def test_the_gate_sees_each_spelling(self, source: str) -> None:
        assert _append_only_offences(source), source

    @pytest.mark.parametrize(
        "source",
        [
            "from sqlalchemy import delete, select\n"
            "from fim_one.db.models import AuditLog, ApiKey\n"
            "async def _g(db, key_id):\n"
            "    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))\n"
            "    key = result.scalar_one()\n    await db.delete(key)\n"
            "    await db.execute(delete(ApiKey))\n",
            "async def _g(db, row):\n    await db.delete(row)\n",
            "from fim_one.db.models import AuditLog\n"
            "async def _g(db, row: 'Agent'):\n    values = {}\n    values.update(a=1)\n"
            "    await db.delete(row)\n",
            # Building a new record, and editing another model's row.
            "from fim_one.db.models import AuditLog, ApiKey\n"
            "from sqlalchemy import update as sa_update\n"
            "async def _g(db, i):\n    key = await db.get(ApiKey, i)\n    key.name = 'x'\n"
            "    setattr(key, 'name', 'y')\n    await db.execute(sa_update(ApiKey))\n",
            # A helper that deletes what it is given, handed another model.
            "from sqlalchemy import delete\n"
            "async def _wipe(db, model):\n    await db.execute(delete(model))\n"
            "async def _g(db):\n    from fim_one.db.models import ApiKey\n"
            "    await _wipe(db, ApiKey)\n",
            # Prose that mentions a table.
            "# the sweep removes rows from audit_logs and login_history\n"
            "def _g():\n    return 'records of audit_logs'\n",
        ],
    )
    def test_the_gate_lets_other_tables_be(self, source: str) -> None:
        assert _append_only_offences(source) == []

    def test_the_sweep_is_the_only_generic_delete(self) -> None:
        """The sweep deletes through one helper that takes the table as an
        argument; the gate follows such helpers (``generic_writers``), and
        the sweep is the module that hands it the record tables."""
        retention = (SRC / "core" / "audit" / "retention.py").read_text(encoding="utf-8")
        assert "_delete_older" in generic_writers(retention)
        for model in ("AuditLog", "LoginHistory", "ReviewLog", "ConnectorCallLog"):
            assert f"_delete_older({model}," in retention, model
        # Imported elsewhere and handed a record table, it is an offence.
        borrowed = (
            "from fim_one.core.audit.retention import _delete_older\n"
            "from fim_one.db.models import AuditLog\n"
            "async def _g(db, cut):\n    await _delete_older(AuditLog, AuditLog.created_at, cut)\n"
        )
        assert _append_only_offences(borrowed, generic_writers(retention))

    def test_write_audit_is_defined_once(self) -> None:
        files = [py for py in sorted(SRC.rglob("*.py")) if "migrations" not in py.parts]
        definitions = [hit.split(":", 1)[0] for hit in _occurrences("def write_audit(", files)]
        assert definitions == ["web/api/admin_utils.py"]

    def test_no_security_event_is_only_logged(self) -> None:
        """Generating or revoking a workflow trigger key used to be a log
        line and nothing else; such a line would mean the record is missing."""
        files = sorted((SRC / "web" / "api").rglob("*.py"))
        offenders = [
            f"{py.relative_to(SRC)}:{lineno}"
            for py in files
            for lineno, line in _source_lines(py)
            if re.search(r"trigger key %s (generated|revoked)", line)
        ]
        assert not offenders, (
            "a trigger key change is an audit event (core/audit/writer.py), not a log line:\n  "
            + "\n  ".join(offenders)
        )
