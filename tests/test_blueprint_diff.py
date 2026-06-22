"""Tests for the blueprint diff function."""

from __future__ import annotations

import pytest

from fim_one.core.workflow.blueprint_diff import compute_blueprint_diff


# ---------------------------------------------------------------------------
# Helpers — tiny blueprint builders
# ---------------------------------------------------------------------------


def _node(
    nid: str,
    ntype: str = "LLM",
    label: str | None = None,
    config: dict | None = None,
    position: dict | None = None,
) -> dict:
    data: dict = {"type": ntype}
    if label:
        data["label"] = label
    if config:
        data.update(config)
    return {
        "id": nid,
        "data": data,
        "position": position or {"x": 0, "y": 0},
    }


def _edge(source: str, target: str, eid: str | None = None) -> dict:
    return {
        "id": eid or f"{source}->{target}",
        "source": source,
        "target": target,
    }


def _bp(nodes: list[dict] | None = None, edges: list[dict] | None = None) -> dict:
    return {
        "nodes": nodes or [],
        "edges": edges or [],
        "viewport": {},
    }


# ---------------------------------------------------------------------------
# Tests — node changes
# ---------------------------------------------------------------------------


class TestAddedNodes:
    def test_single_added_node(self) -> None:
        old = _bp(nodes=[_node("1", "START")])
        new = _bp(nodes=[_node("1", "START"), _node("2", "LLM")])
        result = compute_blueprint_diff(old, new)
        assert "Added 1 node (LLM)" in result

    def test_multiple_added_nodes(self) -> None:
        old = _bp(nodes=[_node("1", "START")])
        new = _bp(
            nodes=[
                _node("1", "START"),
                _node("2", "LLM"),
                _node("3", "CONNECTOR", label="Connector"),
            ]
        )
        result = compute_blueprint_diff(old, new)
        assert "Added 2 nodes" in result
        assert "LLM" in result
        assert "Connector" in result


class TestRemovedNodes:
    def test_single_removed_node(self) -> None:
        old = _bp(
            nodes=[_node("1", "START"), _node("2", "CONNECTOR", label="Connector")]
        )
        new = _bp(nodes=[_node("1", "START")])
        result = compute_blueprint_diff(old, new)
        assert "Removed 1 node (Connector)" in result

    def test_multiple_removed_nodes(self) -> None:
        old = _bp(
            nodes=[
                _node("1", "START"),
                _node("2", "LLM"),
                _node("3", "AGENT"),
            ]
        )
        new = _bp(nodes=[_node("1", "START")])
        result = compute_blueprint_diff(old, new)
        assert "Removed 2 nodes" in result


class TestModifiedNodes:
    def test_config_change_detected(self) -> None:
        old = _bp(
            nodes=[_node("1", "LLM", config={"model": "gpt-4"})]
        )
        new = _bp(
            nodes=[_node("1", "LLM", config={"model": "claude-3"})]
        )
        result = compute_blueprint_diff(old, new)
        assert "Modified 1 node (LLM)" in result

    def test_label_change_detected(self) -> None:
        old = _bp(nodes=[_node("1", "LLM", label="Old Name")])
        new = _bp(nodes=[_node("1", "LLM", label="New Name")])
        result = compute_blueprint_diff(old, new)
        assert "Modified 1 node" in result

    def test_multiple_config_changes(self) -> None:
        old = _bp(
            nodes=[
                _node("1", "START", config={"schema": {"a": 1}}),
                _node("2", "AGENT", config={"agent_id": "x"}),
                _node("3", "END", config={"schema": {"b": 2}}),
            ]
        )
        new = _bp(
            nodes=[
                _node("1", "START", config={"schema": {"a": 99}}),
                _node("2", "AGENT", config={"agent_id": "y"}),
                _node("3", "END", config={"schema": {"b": 99}}),
            ]
        )
        result = compute_blueprint_diff(old, new)
        assert "Modified 3 nodes" in result


class TestPositionOnlyChanges:
    def test_position_only_change(self) -> None:
        old = _bp(nodes=[_node("1", "LLM", position={"x": 0, "y": 0})])
        new = _bp(nodes=[_node("1", "LLM", position={"x": 100, "y": 200})])
        result = compute_blueprint_diff(old, new)
        assert "Repositioned 1 node" in result
        assert "Modified" not in result

    def test_position_plus_config_counts_as_modified(self) -> None:
        old = _bp(
            nodes=[
                _node("1", "LLM", config={"model": "gpt-4"}, position={"x": 0, "y": 0})
            ]
        )
        new = _bp(
            nodes=[
                _node(
                    "1",
                    "LLM",
                    config={"model": "claude-3"},
                    position={"x": 100, "y": 200},
                )
            ]
        )
        result = compute_blueprint_diff(old, new)
        assert "Modified 1 node" in result
        assert "Repositioned" not in result


# ---------------------------------------------------------------------------
# Tests — edge changes
# ---------------------------------------------------------------------------


class TestEdgeChanges:
    def test_added_edge(self) -> None:
        old = _bp(edges=[])
        new = _bp(edges=[_edge("1", "2")])
        result = compute_blueprint_diff(old, new)
        assert "Added 1 edge" in result

    def test_removed_edge(self) -> None:
        old = _bp(edges=[_edge("1", "2"), _edge("2", "3")])
        new = _bp(edges=[_edge("1", "2")])
        result = compute_blueprint_diff(old, new)
        assert "Removed 1 edge" in result

    def test_multiple_edge_changes(self) -> None:
        old = _bp(edges=[_edge("1", "2"), _edge("2", "3")])
        new = _bp(edges=[_edge("1", "3"), _edge("3", "4")])
        result = compute_blueprint_diff(old, new)
        assert "Added 2 edges" in result
        assert "Removed 2 edges" in result

    def test_edge_handle_change(self) -> None:
        """Changing sourceHandle or targetHandle counts as add+remove."""
        old = _bp(
            edges=[
                {
                    "id": "e1",
                    "source": "1",
                    "target": "2",
                    "sourceHandle": "true",
                    "targetHandle": None,
                }
            ]
        )
        new = _bp(
            edges=[
                {
                    "id": "e1",
                    "source": "1",
                    "target": "2",
                    "sourceHandle": "false",
                    "targetHandle": None,
                }
            ]
        )
        result = compute_blueprint_diff(old, new)
        assert "Added 1 edge" in result
        assert "Removed 1 edge" in result


# ---------------------------------------------------------------------------
# Tests — no changes
# ---------------------------------------------------------------------------


class TestNoChanges:
    def test_identical_blueprints(self) -> None:
        bp = _bp(
            nodes=[_node("1", "START"), _node("2", "END")],
            edges=[_edge("1", "2")],
        )
        assert compute_blueprint_diff(bp, bp) == "No changes"

    def test_both_empty(self) -> None:
        assert compute_blueprint_diff({}, {}) == "No changes"

    def test_empty_blueprints_with_keys(self) -> None:
        assert compute_blueprint_diff(_bp(), _bp()) == "No changes"


# ---------------------------------------------------------------------------
# Tests — empty/missing blueprint fields
# ---------------------------------------------------------------------------


class TestEmptyBlueprints:
    def test_old_empty_new_has_nodes(self) -> None:
        old: dict = {}
        new = _bp(nodes=[_node("1", "LLM")])
        result = compute_blueprint_diff(old, new)
        assert "Added 1 node (LLM)" in result

    def test_new_empty_old_has_nodes(self) -> None:
        old = _bp(nodes=[_node("1", "LLM")])
        new: dict = {}
        result = compute_blueprint_diff(old, new)
        assert "Removed 1 node (LLM)" in result

    def test_missing_nodes_key(self) -> None:
        old = {"edges": [], "viewport": {}}
        new = {"nodes": [_node("1", "START")], "edges": [], "viewport": {}}
        result = compute_blueprint_diff(old, new)
        assert "Added 1 node" in result

    def test_missing_edges_key(self) -> None:
        old = {"nodes": [_node("1", "START")], "viewport": {}}
        new = {"nodes": [_node("1", "START")], "edges": [_edge("1", "2")], "viewport": {}}
        result = compute_blueprint_diff(old, new)
        assert "Added 1 edge" in result


# ---------------------------------------------------------------------------
# Tests — complex diffs
# ---------------------------------------------------------------------------


class TestComplexDiff:
    def test_mixed_changes(self) -> None:
        """Add nodes, remove nodes, modify nodes, change edges all at once."""
        old = _bp(
            nodes=[
                _node("start", "START"),
                _node("llm1", "LLM", config={"model": "gpt-4"}),
                _node("http1", "CONNECTOR", label="ConnectorA"),
                _node("end", "END"),
            ],
            edges=[
                _edge("start", "llm1"),
                _edge("llm1", "http1"),
                _edge("http1", "end"),
            ],
        )
        new = _bp(
            nodes=[
                _node("start", "START"),
                _node("llm1", "LLM", config={"model": "claude-3"}),
                _node("agent1", "AGENT", label="Agent"),
                _node("code1", "CONNECTOR", label="ConnectorB"),
                _node("end", "END"),
            ],
            edges=[
                _edge("start", "llm1"),
                _edge("llm1", "agent1"),
                _edge("agent1", "code1"),
                _edge("code1", "end"),
            ],
        )
        result = compute_blueprint_diff(old, new)

        # Added: agent1, code1
        assert "Added 2 nodes" in result
        assert "Agent" in result
        assert "Connector" in result

        # Removed: http1
        assert "Removed 1 node (ConnectorA)" in result

        # Modified: llm1 config changed
        assert "Modified 1 node (LLM)" in result

        # Edge changes: removed (llm1->http1, http1->end), added (llm1->agent1, agent1->code1, code1->end)
        assert "Added 3 edges" in result
        assert "Removed 2 edges" in result

    def test_order_of_parts(self) -> None:
        """Verify the summary follows a consistent order: added, removed, modified, repositioned, edges."""
        old = _bp(
            nodes=[
                _node("1", "START"),
                _node("2", "LLM", config={"model": "old"}),
                _node("3", "AGENT"),
            ],
            edges=[_edge("1", "2"), _edge("2", "3")],
        )
        new = _bp(
            nodes=[
                _node("1", "START"),
                _node("2", "LLM", config={"model": "new"}),
                _node("4", "END"),
            ],
            edges=[_edge("1", "2"), _edge("2", "4")],
        )
        result = compute_blueprint_diff(old, new)

        # Should contain all these parts
        assert "Added" in result
        assert "Removed" in result
        assert "Modified" in result

        # "Added" should come before "Removed" which comes before "Modified"
        added_pos = result.index("Added 1 node")
        removed_pos = result.index("Removed 1 node")
        modified_pos = result.index("Modified 1 node")
        assert added_pos < removed_pos < modified_pos


# ---------------------------------------------------------------------------
# Tests — node label resolution
# ---------------------------------------------------------------------------


class TestNodeLabel:
    def test_label_preferred_over_type(self) -> None:
        old = _bp()
        new = _bp(nodes=[_node("1", "LLM", label="My Custom LLM")])
        result = compute_blueprint_diff(old, new)
        assert "My Custom LLM" in result

    def test_type_used_when_no_label(self) -> None:
        old = _bp()
        new = _bp(nodes=[_node("1", "CONNECTOR")])
        result = compute_blueprint_diff(old, new)
        assert "CONNECTOR" in result

    def test_node_without_data(self) -> None:
        """Node with no data dict still works — falls back to id."""
        old = _bp()
        new = _bp(nodes=[{"id": "x"}])
        result = compute_blueprint_diff(old, new)
        assert "Added 1 node" in result

    def test_node_with_empty_data(self) -> None:
        old = _bp()
        new = _bp(nodes=[{"id": "x", "data": {}}])
        result = compute_blueprint_diff(old, new)
        assert "Added 1 node" in result


# ---------------------------------------------------------------------------
# Tests — singular vs plural grammar
# ---------------------------------------------------------------------------


class TestGrammar:
    def test_singular_node(self) -> None:
        old = _bp()
        new = _bp(nodes=[_node("1", "LLM")])
        result = compute_blueprint_diff(old, new)
        assert "1 node" in result
        assert "nodes" not in result

    def test_plural_nodes(self) -> None:
        old = _bp()
        new = _bp(nodes=[_node("1", "LLM"), _node("2", "AGENT")])
        result = compute_blueprint_diff(old, new)
        assert "2 nodes" in result

    def test_singular_edge(self) -> None:
        old = _bp()
        new = _bp(edges=[_edge("1", "2")])
        result = compute_blueprint_diff(old, new)
        assert "1 edge" in result
        assert "edges" not in result

    def test_plural_edges(self) -> None:
        old = _bp()
        new = _bp(edges=[_edge("1", "2"), _edge("2", "3")])
        result = compute_blueprint_diff(old, new)
        assert "2 edges" in result
