"""CSV exports cannot be read as formulas by a spreadsheet (``web/csv_safe.py``)."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import fim_one
from fim_one.db.models import Agent
from fim_one.db.models.conversation import Conversation
from fim_one.web.csv_safe import safe_cell, safe_writer
from tests.audit.harness import World, world  # noqa: F401

FORMULA = "=cmd|' /C calc'!A0"


class TestSafeCell:
    def test_text_a_spreadsheet_would_run_gets_an_apostrophe(self) -> None:
        for text in (FORMULA, "+1+1", "-2+3", "@SUM(1)", "\tx", "\rx"):
            assert safe_cell(text) == "'" + text

    def test_the_full_width_forms_get_one_too(self) -> None:
        """A spreadsheet in an East Asian locale reads ``＝`` as ``=``."""
        for text in ("＝cmd|' /C calc'!A0", "＋1+1", "－2+3", "＠SUM(1)"):
            assert safe_cell(text) == "'" + text
        assert safe_cell("a＝b") == "a＝b"

    def test_numbers_other_types_and_ordinary_text_are_untouched(self) -> None:
        assert [safe_cell(v) for v in ("-5", "+1,234.50", "-12%", "a=b", "", None, -5, True)] == [
            "-5", "+1,234.50", "-12%", "a=b", "", None, -5, True,
        ]  # fmt: skip

    def test_the_writer_applies_it_to_every_cell(self) -> None:
        buf = io.StringIO()
        safe_writer(buf).writerows([["name", "n"], [FORMULA, 3]])
        assert list(csv.reader(io.StringIO(buf.getvalue()))) == [["name", "n"], ["'" + FORMULA, "3"]]


class TestTheExports:
    async def test_the_audit_export(self, world: World) -> None:
        root = await world.user("root", admin=True)
        await world.add(Agent(id="ag-1", user_id=root.id, name=FORMULA))
        await world.call("DELETE", "/api/agents/ag-1", by=root)

        export = await world.call("GET", "/api/admin/audit-log/export", by=root)

        (line,) = [r for r in csv.DictReader(io.StringIO(export.text)) if r["action"] == "agent.delete"]
        assert line["target_label"] == "'" + FORMULA

    async def test_the_users_export(self, world: World) -> None:
        root = await world.user("root", admin=True)
        await world.user("bob", display_name="@SUM(1+1)")

        export = await world.call("GET", "/api/admin/export/users", by=root)

        names = [r["display_name"] for r in csv.DictReader(io.StringIO(export.text))]
        assert "'@SUM(1+1)" in names and "@SUM(1+1)" not in names

    async def test_the_conversations_export(self, world: World) -> None:
        root = await world.user("root", admin=True)
        await world.add(Conversation(id="c-1", user_id=root.id, title=FORMULA, mode="react"))

        export = await world.call("GET", "/api/admin/export/conversations", by=root)

        assert [r["title"] for r in csv.DictReader(io.StringIO(export.text))] == ["'" + FORMULA]


def test_no_csv_is_written_without_the_helper() -> None:
    """Every CSV the web layer writes goes through ``safe_writer``."""
    web = Path(fim_one.__file__).parent / "web"
    offenders = [
        str(path.relative_to(web))
        for path in sorted(web.rglob("*.py"))
        if path.name != "csv_safe.py" and "csv.writer(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
