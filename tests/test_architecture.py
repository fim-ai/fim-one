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
            "web/deps.py",  # instance-level servers from the MCP_SERVERS env var
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
