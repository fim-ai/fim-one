"""Blueprint v2 contract (T10 P3).

- Every node type and every built-in template validates against the v2
  models, survives a dump/validate round trip, and parses.
- Blueprints in the spellings stored before v2 (including those from before
  v0.9.3) upgrade to v2 with the same meaning.
- Migration ``t10p3c4d5e6f`` rewrites stored blueprints, keeps the original
  in ``blueprint_legacy``, skips v2 rows, and is idempotent, on SQLite and
  (with ``FIM_TEST_PG_URL``) PostgreSQL. ``t10p0a1b2c3d`` is covered too.
- Saving an invalid blueprint answers 422 with field-level errors.
- The generated TypeScript types match the models.
"""

from __future__ import annotations

import copy
import importlib
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fim_one.core.workflow.blueprint_v2 import (
    NODE_MODELS,
    NODE_TYPE_NAMES,
    BlueprintSchemaError,
    BlueprintV2,
    is_v2,
    normalize_for_save,
    upgrade_blueprint,
    validate_blueprint_v2,
)
from fim_one.core.workflow.parser import BlueprintValidationError, parse_blueprint
from fim_one.core.workflow.templates import list_templates
from fim_one.core.workflow.types import ErrorStrategy, NodeType

# ---------------------------------------------------------------------------
# Round trips
# ---------------------------------------------------------------------------

_SAMPLE_DATA: dict[str, dict[str, Any]] = {
    "start": {"variables": [{"name": "q", "type": "string", "required": True}]},
    "end": {"output_mapping": {"answer": "llm_1.output"}},
    "llm": {"prompt_template": "Hi {{input.q}}", "temperature": 0.2, "max_tokens": 200},
    "conditionBranch": {
        "mode": "expression",
        "conditions": [
            {"id": "c1", "label": "big", "variable": "input.n", "operator": ">", "value": 3}
        ],
    },
    "agent": {"agent_id": "agt-1", "prompt_template": "Do {{input.q}}"},
    "knowledgeRetrieval": {"kb_ids": ["kb-1", "kb-2"], "query_template": "{{input.q}}", "top_k": 3},
    "connector": {"connector_id": "c-1", "action_id": "a-1", "parameters": {"id": "{{input.q}}"}},
    "humanIntervention": {"title": "Approve", "assignee": "u-1", "timeout_hours": 2},
    "mcp": {"server_id": "s-1", "tool_name": "search", "parameters": {"q": "x"}},
}


def _blueprint_with(node_type: str) -> dict[str, Any]:
    nodes = [{"id": "start_1", "type": "start", "position": {"x": 0, "y": 0}, "data": {}}]
    edges = []
    last = "start_1"
    if node_type not in ("start", "end"):
        nodes.append({"id": "n1", "type": node_type, "position": {"x": 1, "y": 0},
                      "data": copy.deepcopy(_SAMPLE_DATA[node_type])})
        edges.append({"id": "e1", "source": "start_1", "target": "n1"})
        last = "n1"
    nodes.append({"id": "end_1", "type": "end", "position": {"x": 2, "y": 0},
                  "data": copy.deepcopy(_SAMPLE_DATA["end"])})
    edges.append({"id": "e2", "source": last, "target": "end_1"})
    return {"schema_version": 2, "nodes": nodes, "edges": edges,
            "viewport": {"x": 0, "y": 0, "zoom": 1}}


class TestRoundTrip:
    def test_every_engine_type_has_a_model(self) -> None:
        assert set(NODE_TYPE_NAMES.values()) == set(NODE_MODELS)
        assert set(NODE_TYPE_NAMES) == set(NodeType)

    @pytest.mark.parametrize("node_type", sorted(NODE_MODELS))
    def test_node_type_round_trip(self, node_type: str) -> None:
        raw = _blueprint_with(node_type)
        model = validate_blueprint_v2(raw)
        dumped = model.model_dump(mode="json", exclude_unset=True)
        assert validate_blueprint_v2(dumped) == model
        assert upgrade_blueprint(raw) == raw  # v2 is a fixed point
        parsed = parse_blueprint(dumped)
        assert {n.type for n in parsed.nodes} >= {NodeType.START, NodeType.END}
        for node in parsed.nodes:
            if node.id == "n1":
                assert NODE_TYPE_NAMES[node.type] == node_type
                for key, value in _SAMPLE_DATA[node_type].items():
                    assert node.data[key] == value

    @pytest.mark.parametrize("template", list_templates(), ids=lambda t: t["id"])
    def test_builtin_template_round_trip(self, template: dict[str, Any]) -> None:
        bp = template["blueprint"]
        assert is_v2(bp)
        model = validate_blueprint_v2(bp)
        dumped = model.model_dump(mode="json", exclude_unset=True)
        assert validate_blueprint_v2(dumped) == model
        assert upgrade_blueprint(bp) == bp
        assert len(parse_blueprint(dumped).nodes) == len(bp["nodes"])


# ---------------------------------------------------------------------------
# Upgrading older spellings
# ---------------------------------------------------------------------------

# A blueprint the way pre-v0.9.3 templates and imports stored it: placeholder
# node.type, backend type on data.type, field aliases, snake_case edge handles.
PRE_093 = {
    "nodes": [
        {"id": "s", "type": "custom", "data": {"type": "START", "variables": []}},
        {"id": "kb", "type": "custom",
         "data": {"type": "KNOWLEDGE_RETRIEVAL", "knowledge_base_id": "kb-9",
                  "query": "{{input.q}}"}},
        {"id": "ag", "type": "custom",
         "data": {"type": "AGENT", "agent_id": "a", "prompt": "use {{kb.output}}",
                  "error_strategy": "CONTINUE"}},
        {"id": "cb", "type": "custom",
         "data": {"type": "CONDITION_BRANCH", "conditions": [{"id": "x", "expression": "1"}]}},
        {"id": "cn", "type": "custom",
         "data": {"type": "CONNECTOR", "connector_id": "c", "action": "act", "timeout_ms": "0"}},
        {"id": "e", "type": "custom", "data": {"type": "END", "output_mapping": {}}},
    ],
    "edges": [
        {"source": "s", "target": "kb"},
        {"source": "kb", "target": "ag"},
        {"source": "ag", "target": "cb"},
        {"source": "cb", "target": "cn", "source_handle": "condition-x"},
        {"source": "cn", "target": "e"},
    ],
}

# The editor's own pre-v2 saves: React Flow type, no data.type, kb_id singular.
EDITOR_093 = {
    "nodes": [
        {"id": "start_1", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "llm_1", "type": "llm", "position": {"x": 1, "y": 0},
         "data": {"prompt": "hello", "label": "LLM", "runStatus": None}},
        {"id": "kr_1", "type": "knowledgeRetrieval", "position": {"x": 2, "y": 0},
         "data": {"kb_id": "kb-1", "query_template": "q"}},
        {"id": "h_1", "type": "humanIntervention", "position": {"x": 3, "y": 0},
         "data": {"prompt_message": "ok?"}},
        {"id": "mcp_1", "type": "mcp", "position": {"x": 4, "y": 0},
         "data": {"server_id": "s", "tool_name": "t"}},
        {"id": "end_1", "type": "end", "position": {"x": 5, "y": 0}, "data": {}},
    ],
    "edges": [
        {"id": "a", "source": "start_1", "target": "llm_1"},
        {"id": "b", "source": "llm_1", "target": "kr_1"},
        {"id": "c", "source": "kr_1", "target": "h_1"},
        {"id": "d", "source": "h_1", "target": "mcp_1"},
        {"id": "f", "source": "mcp_1", "target": "end_1"},
    ],
    "viewport": {"x": 0, "y": 0, "zoom": 1},
}


class TestUpgrade:
    def test_pre_093_blueprint(self) -> None:
        bp = upgrade_blueprint(PRE_093)
        validate_blueprint_v2(bp)
        parsed = parse_blueprint(bp)
        by_id = {n.id: n for n in parsed.nodes}
        assert by_id["kb"].type == NodeType.KNOWLEDGE_RETRIEVAL
        assert by_id["kb"].data["kb_ids"] == ["kb-9"]
        assert by_id["kb"].data["query_template"] == "{{input.q}}"
        assert by_id["ag"].data["prompt_template"] == "use {{kb.output}}"
        assert by_id["ag"].error_strategy == ErrorStrategy.CONTINUE
        assert by_id["cn"].data["action_id"] == "act"
        assert "timeout_ms" not in by_id["cn"].data
        assert all("type" not in n.data for n in parsed.nodes)
        branch_edge = next(e for e in parsed.edges if e.source == "cb")
        assert branch_edge.source_handle == "condition-x"
        assert all(e.id for e in parsed.edges)

    def test_editor_093_blueprint(self) -> None:
        bp = upgrade_blueprint(EDITOR_093)
        validate_blueprint_v2(bp)
        parsed = parse_blueprint(bp)
        by_id = {n.id: n for n in parsed.nodes}
        assert by_id["llm_1"].data["prompt_template"] == "hello"
        assert "prompt" not in by_id["llm_1"].data
        assert by_id["kr_1"].data["kb_ids"] == ["kb-1"]
        assert by_id["llm_1"].data["label"] == "LLM"  # editor fields kept

    def test_upgrade_is_idempotent(self) -> None:
        once = upgrade_blueprint(PRE_093)
        assert upgrade_blueprint(once) == once

    def test_parser_rejects_old_spellings(self) -> None:
        with pytest.raises(BlueprintValidationError, match="Unknown node type"):
            parse_blueprint(PRE_093)

    def test_unknown_type_is_left_for_validation(self) -> None:
        bp = upgrade_blueprint({"nodes": [{"id": "x", "type": "subWorkflow", "data": {}}]})
        with pytest.raises(BlueprintSchemaError) as exc:
            validate_blueprint_v2(bp)
        assert exc.value.errors[0]["loc"].startswith("nodes.0")


class TestFieldErrors:
    def test_errors_name_node_and_field(self) -> None:
        bp = _blueprint_with("knowledgeRetrieval")
        bp["nodes"][1]["data"]["top_k"] = 0
        bp["nodes"][1]["data"]["kb_ids"] = "kb-1"
        with pytest.raises(BlueprintSchemaError) as exc:
            normalize_for_save(bp)
        locs = {e["loc"]: e for e in exc.value.errors}
        assert set(locs) == {"nodes.1.data.top_k", "nodes.1.data.kb_ids"}
        assert {e["node_id"] for e in exc.value.errors} == {"n1"}


# ---------------------------------------------------------------------------
# Save endpoints
# ---------------------------------------------------------------------------


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    import fim_one.db.models  # noqa: F401
    from fim_one.db.base import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


class TestSaveEndpoints:
    @pytest.mark.usefixtures("human_intervention_enabled")
    @pytest.mark.asyncio
    async def test_create_stores_v2_and_update_rejects_bad_fields(
        self, session: AsyncSession
    ) -> None:
        from fim_one.db.models.user import User
        from fim_one.web.api import workflows as wf_api
        from fim_one.web.exceptions import AppError
        from fim_one.web.schemas.workflow import WorkflowCreate, WorkflowUpdate

        user = User(id=str(uuid.uuid4()), email="a@x.io")
        session.add(user)
        await session.commit()

        created = await wf_api.create_workflow(
            WorkflowCreate(name="w", blueprint=EDITOR_093), current_user=user, db=session
        )
        stored = created.data["blueprint"]
        assert is_v2(stored)
        assert stored["nodes"][1]["data"]["prompt_template"] == "hello"

        bad = json.loads(json.dumps(stored))
        bad["nodes"][3]["data"]["timeout_hours"] = -1
        with pytest.raises(AppError) as exc:
            await wf_api.update_workflow(
                created.data["id"], WorkflowUpdate(blueprint=bad), current_user=user, db=session
            )
        assert exc.value.status_code == 422
        assert exc.value.error_code == "blueprint_schema_invalid"
        (err,) = exc.value.detail_args["errors"]
        assert err["loc"] == "nodes.3.data.timeout_hours"
        assert err["node_id"] == "h_1"


# ---------------------------------------------------------------------------
# Migrations (SQLite always; PostgreSQL when FIM_TEST_PG_URL is set)
# ---------------------------------------------------------------------------

P3 = "fim_one.migrations.versions.t10p3c4d5e6f_workflow_blueprint_v2"
P0 = "fim_one.migrations.versions.t10p0a1b2c3d_add_workflow_run_mode"


def _engine_params() -> list[Any]:
    pg = os.environ.get("FIM_TEST_PG_URL")
    return [
        pytest.param("sqlite:///:memory:", id="sqlite"),
        pytest.param(pg or "", id="postgresql",
                     marks=pytest.mark.skipif(not pg, reason="FIM_TEST_PG_URL not set")),
    ]


_TABLES = {
    "workflows": "blueprint",
    "workflow_versions": "blueprint",
    "workflow_templates": "blueprint",
    "workflow_runs": "blueprint_snapshot",
}


@pytest.fixture(params=_engine_params())
def legacy_db(request: pytest.FixtureRequest) -> Iterator[sa.Engine]:
    engine = sa.create_engine(request.param, future=True)
    with engine.begin() as conn:
        for table, column in _TABLES.items():
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
            conn.execute(sa.text(
                f"CREATE TABLE {table} (id VARCHAR(36) PRIMARY KEY, {column} JSON NOT NULL)"
            ))
            tbl = sa.table(table, sa.column("id", sa.String), sa.column(column, sa.JSON))
            conn.execute(tbl.insert(), [
                {"id": "a-old", column: PRE_093},
                {"id": "b-editor", column: EDITOR_093},
                {"id": "c-v2", column: _blueprint_with("llm")},
            ])
    yield engine
    with engine.begin() as conn:
        for table in _TABLES:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
    engine.dispose()


def _run(engine: sa.Engine, module: str, fn: str) -> None:
    mod = importlib.import_module(module)
    with engine.connect() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(mod, fn)()
        conn.commit()


def _rows(engine: sa.Engine, table: str, column: str) -> dict[str, tuple[Any, Any]]:
    tbl = sa.table(table, sa.column("id", sa.String), sa.column(column, sa.JSON),
                   sa.column("blueprint_legacy", sa.JSON))
    with engine.connect() as conn:
        return {r[0]: (r[1], r[2]) for r in conn.execute(
            sa.select(tbl.c.id, tbl.c[column], tbl.c.blueprint_legacy))}


class TestBlueprintMigration:
    def test_rewrites_old_rows_and_keeps_originals(self, legacy_db: sa.Engine) -> None:
        _run(legacy_db, P3, "upgrade")
        for table, column in _TABLES.items():
            rows = _rows(legacy_db, table, column)
            new_old, legacy_old = rows["a-old"]
            assert is_v2(new_old) and legacy_old == PRE_093
            parse_blueprint(new_old)
            BlueprintV2.model_validate(new_old)
            new_ed, legacy_ed = rows["b-editor"]
            assert is_v2(new_ed) and legacy_ed == EDITOR_093
            assert new_ed["nodes"][2]["data"]["kb_ids"] == ["kb-1"]
            v2, legacy_v2 = rows["c-v2"]
            assert v2 == _blueprint_with("llm") and legacy_v2 is None

    def test_idempotent(self, legacy_db: sa.Engine) -> None:
        _run(legacy_db, P3, "upgrade")
        first = {t: _rows(legacy_db, t, c) for t, c in _TABLES.items()}
        _run(legacy_db, P3, "upgrade")
        assert {t: _rows(legacy_db, t, c) for t, c in _TABLES.items()} == first

    def test_downgrade_restores_originals(self, legacy_db: sa.Engine) -> None:
        _run(legacy_db, P3, "upgrade")
        _run(legacy_db, P3, "downgrade")
        for table, column in _TABLES.items():
            cols = {c["name"] for c in sa.inspect(legacy_db).get_columns(table)}
            assert "blueprint_legacy" not in cols
            tbl = sa.table(table, sa.column("id", sa.String), sa.column(column, sa.JSON))
            with legacy_db.connect() as conn:
                rows = dict(conn.execute(sa.select(tbl.c.id, tbl.c[column])).all())
            assert rows["a-old"] == PRE_093
            assert rows["b-editor"] == EDITOR_093
            # Stored as v2 before the upgrade: converted, not restored.
            v1 = rows["c-v2"]
            assert "schema_version" not in v1
            assert v1["nodes"] == _blueprint_with("llm")["nodes"]

    def test_downgrade_keeps_edits_made_after_upgrade(self, legacy_db: sa.Engine) -> None:
        _run(legacy_db, P3, "upgrade")
        edited = upgrade_blueprint(PRE_093)
        edited["nodes"][1]["data"]["query_template"] = "edited after upgrade"
        created = _blueprint_with("knowledgeRetrieval")
        connector = _blueprint_with("connector")
        for table, column in _TABLES.items():
            tbl = sa.table(table, sa.column("id", sa.String), sa.column(column, sa.JSON))
            with legacy_db.begin() as conn:
                conn.execute(
                    tbl.update().where(tbl.c.id == "a-old").values({column: edited})
                )
                conn.execute(tbl.insert(), [
                    {"id": "d-new", column: created},
                    {"id": "e-new", column: connector},
                ])

        _run(legacy_db, P3, "downgrade")

        for table, column in _TABLES.items():
            tbl = sa.table(table, sa.column("id", sa.String), sa.column(column, sa.JSON))
            with legacy_db.connect() as conn:
                rows = dict(conn.execute(sa.select(tbl.c.id, tbl.c[column])).all())
            # The edit survives; the pre-upgrade original is not restored over it.
            kb = rows["a-old"]["nodes"][1]["data"]
            assert kb["query_template"] == "edited after upgrade"
            assert kb["kb_id"] == "kb-9"  # pre-v2 editor key added back
            assert "schema_version" not in rows["a-old"]
            # Created after the upgrade: converted to the pre-v2 form.
            new_kb = rows["d-new"]["nodes"][1]["data"]
            assert new_kb["kb_ids"] == ["kb-1", "kb-2"] and new_kb["kb_id"] == "kb-1"
            assert rows["e-new"]["nodes"][1]["data"]["action"] == "a-1"
            # Untouched rows are restored exactly.
            assert rows["b-editor"] == EDITOR_093

    def test_run_mode_migration(self, legacy_db: sa.Engine) -> None:
        _run(legacy_db, P0, "upgrade")
        _run(legacy_db, P0, "upgrade")
        assert "mode" in {c["name"] for c in sa.inspect(legacy_db).get_columns("workflow_runs")}
        _run(legacy_db, P0, "downgrade")
        assert "mode" not in {c["name"] for c in sa.inspect(legacy_db).get_columns("workflow_runs")}


# ---------------------------------------------------------------------------
# Generated TypeScript types
# ---------------------------------------------------------------------------


def test_generated_typescript_is_current() -> None:
    """Edit the models, then run ``uv run python scripts/gen_workflow_types.py``."""
    root = Path(__file__).resolve().parents[1]
    import runpy

    module = runpy.run_path(str(root / "scripts" / "gen_workflow_types.py"))
    expected = module["render"]()
    current = (root / "frontend" / "src" / "types" / "workflow-blueprint.generated.ts").read_text(
        encoding="utf-8"
    )
    assert current == expected, "run: uv run python scripts/gen_workflow_types.py"


class TestViewportShape:
    """The viewport is saved by itself on every pan; it is three numbers."""

    @staticmethod
    def _with(viewport: Any) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "nodes": [{"id": "start_1", "type": "start", "position": {"x": 0, "y": 0}, "data": {}}],
            "edges": [],
            "viewport": viewport,
        }

    @pytest.mark.parametrize(
        "viewport",
        [{"x": 12.5, "y": -340, "zoom": 0.75}, {}, {"x": 0, "y": 0, "zoom": 2}],
    )
    def test_a_real_viewport_is_kept(self, viewport: dict[str, Any]) -> None:
        from fim_one.core.workflow.blueprint_v2 import normalize_for_save

        saved = normalize_for_save(self._with(viewport))["viewport"]
        assert {k: saved[k] for k in viewport} == viewport

    @pytest.mark.parametrize(
        "viewport",
        [
            {"x": 0, "y": 0, "zoom": 1, "notes": "x" * 1000},
            {"x": 0, "y": 0, "zoom": 1, "blob": {"anything": ["goes"]}},
            {"x": 0, "y": 0, "zoom": 0},
            {"x": 0, "y": 0, "zoom": -1},
            {"x": 0, "y": 0, "zoom": 1e9},
            {"x": float("inf"), "y": 0, "zoom": 1},
            {"x": 0, "y": float("nan"), "zoom": 1},
            {"x": "left", "y": 0, "zoom": 1},
            {"x": 1e12, "y": 0, "zoom": 1},
        ],
    )
    def test_anything_else_is_refused(self, viewport: dict[str, Any]) -> None:
        from fim_one.core.workflow.blueprint_v2 import BlueprintSchemaError, normalize_for_save

        with pytest.raises(BlueprintSchemaError) as refused:
            normalize_for_save(self._with(viewport))
        assert any("viewport" in str(e.get("loc")) for e in refused.value.errors)
