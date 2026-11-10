"""The coverage tables against the application (see ``coverage.py``)."""

from __future__ import annotations

import inspect
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fim_one.core.audit import actions

from . import coverage as c

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def routes() -> dict[c.Route, Any]:
    from fim_one.web.app import create_app

    return dict(c.all_routes(create_app()))


def _mutating(routes: dict[c.Route, Any]) -> set[c.Route]:
    return {route for route in routes if route[0] in c.MUTATING}


def _listing(items: Any) -> str:
    return "\n  " + "\n  ".join(sorted(f"{method} {path}" for method, path in items))


class TestEveryStateChangingRouteIsAccountedFor:
    def test_the_three_tables_are_the_routes_exactly(self, routes: dict[c.Route, Any]) -> None:
        actual = _mutating(routes)
        tabled = set(c.AUDITED) | set(c.EXEMPT) | set(c.KNOWN_GAPS)

        unlisted = actual - tabled
        assert not unlisted, (
            "state-changing routes in none of AUDITED / EXEMPT / KNOWN_GAPS "
            "(tests/audit/coverage.py): record what they do with "
            "fim_one.core.audit.writer.record and list them under AUDITED, or list "
            "them under EXEMPT with a reason:" + _listing(unlisted)
        )
        gone = tabled - actual
        assert not gone, "listed routes the application no longer has:" + _listing(gone)

    def test_no_route_is_in_two_tables(self) -> None:
        audited, exempt, gaps = set(c.AUDITED), set(c.EXEMPT), set(c.KNOWN_GAPS)
        assert not audited & exempt, _listing(audited & exempt)
        assert not audited & gaps, _listing(audited & gaps)
        assert not exempt & gaps, _listing(exempt & gaps)

    def test_the_application_has_state_changing_routes(self, routes: dict[c.Route, Any]) -> None:
        """The walk found them: an empty set would make the tables pass vacuously."""
        assert len(_mutating(routes)) > 250

    def test_read_routes_that_record_exist_and_are_reads(self, routes: dict[c.Route, Any]) -> None:
        for route in c.AUDITED_READS:
            assert route[0] == "GET" and route in routes, route


class TestWhatTheTablesSay:
    def test_an_exemption_names_one_of_the_reasons(self) -> None:
        assert set(c.EXEMPT.values()) <= set(c.EXEMPT_REASONS)

    def test_every_recorded_name_is_a_registered_action_a_review_entry_or_a_sign_in(self) -> None:
        for route, names in {**c.AUDITED, **c.AUDITED_READS}.items():
            assert names, route
            for name in names:
                if name == c.LOGIN:
                    continue
                if name.startswith(c.REVIEW_LOG):
                    assert name.removeprefix(c.REVIEW_LOG) in c.REVIEW_ACTIONS, (route, name)
                else:
                    assert actions.is_registered(name), (route, name)

    def test_every_registered_action_is_accounted_for(self) -> None:
        """A name in the registry that no route or entry point is listed as writing."""
        listed = {
            name for names in {**c.AUDITED, **c.AUDITED_READS}.values() for name in names
        } | set(c.NON_ROUTE_ACTIONS)
        assert actions.ACTIONS - listed == set()

    def test_a_gap_is_not_yet_a_registered_action_of_its_route(self) -> None:
        """Once a route records, it moves to AUDITED."""
        assert not set(c.KNOWN_GAPS) & set(c.AUDITED)
        for gap in (*c.KNOWN_GAPS.values(), *c.KNOWN_NON_ROUTE_GAPS.values()):
            assert gap.owner and gap.action


def _constant_names(action: str) -> Iterator[str]:
    for name, value in vars(actions).items():
        if name.isupper() and value == action:
            yield name


def _module_sources(routes: dict[c.Route, Any], route: c.Route) -> str:
    modules = [routes[route].__module__, *c.RECORDED_IN.get(route, ())]
    return "\n".join(inspect.getsource(sys.modules[name]) for name in modules)


class TestTheRecordIsWhereTheTableSaysItIs:
    """Each listed action is written in the module of its endpoint (or the
    modules ``RECORDED_IN`` names): as the literal, or as its constant."""

    def test_audited_routes(self, routes: dict[c.Route, Any]) -> None:
        import importlib

        for modules in c.RECORDED_IN.values():
            for name in modules:
                importlib.import_module(name)
        missing: list[str] = []
        for route, names in {**c.AUDITED, **c.AUDITED_READS}.items():
            source = _module_sources(routes, route)
            for name in names:
                if name == c.LOGIN or name.startswith(c.REVIEW_LOG):
                    continue
                written = f'"{name}"' in source or any(
                    f".{constant}" in source for constant in _constant_names(name)
                )
                if not written:
                    missing.append(f"{route[0]} {route[1]}: {name}")
        assert not missing, "no trace of the action where its route is handled:\n  " + "\n  ".join(
            missing
        )

    def test_non_route_actions_point_at_a_test(self) -> None:
        for action, node in c.NON_ROUTE_ACTIONS.items():
            assert actions.is_registered(action), action
            file_name, *names = node.split("::")
            path = REPO / file_name
            assert path.is_file(), node
            assert names and f"def {names[-1]}(" in path.read_text(encoding="utf-8"), node
