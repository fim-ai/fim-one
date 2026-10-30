"""Tests for the public OpenAPI schema built in :mod:`fim_one.web.app`.

The schema must publish exactly ``_PUBLIC_API``.  Hiding routes by flipping
``include_in_schema`` on ``app.routes`` stopped working when FastAPI began
wrapping each included router, and the schema silently grew from 16 public
operations to every internal endpoint.
"""

from __future__ import annotations

from typing import Any

import pytest

from fim_one.web.app import _PUBLIC_API, create_app, filter_openapi_to_public


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def _schema() -> dict[str, Any]:
    return {
        "paths": {
            "/api/agents": {
                "get": {"responses": {"200": {"content": {"j": {"schema": _ref("AgentList")}}}}},
                "post": {"requestBody": {"content": {"j": {"schema": _ref("AgentCreate")}}}},
            },
            "/api/admin/users": {
                "get": {"responses": {"200": {"content": {"j": {"schema": _ref("UserList")}}}}},
            },
        },
        "components": {
            "schemas": {
                "AgentList": {"properties": {"items": {"items": _ref("Agent")}}},
                "Agent": {"properties": {"owner": _ref("Owner")}},
                "Owner": {"type": "object"},
                "AgentCreate": {"type": "object"},
                "UserList": {"type": "object"},
            },
        },
    }


class TestFilterOpenapiToPublic:
    def test_keeps_only_public_operations(self) -> None:
        result = filter_openapi_to_public(_schema(), frozenset({("GET", "/api/agents")}))
        assert result["paths"] == {"/api/agents": {"get": _schema()["paths"]["/api/agents"]["get"]}}

    def test_drops_paths_with_no_public_operation(self) -> None:
        result = filter_openapi_to_public(_schema(), frozenset({("GET", "/api/agents")}))
        assert "/api/admin/users" not in result["paths"]

    def test_keeps_transitively_referenced_schemas_only(self) -> None:
        result = filter_openapi_to_public(_schema(), frozenset({("GET", "/api/agents")}))
        assert set(result["components"]["schemas"]) == {"AgentList", "Agent", "Owner"}

    def test_empty_public_set_leaves_nothing(self) -> None:
        result = filter_openapi_to_public(_schema(), frozenset())
        assert result["paths"] == {}
        assert result["components"]["schemas"] == {}

    def test_schema_without_components(self) -> None:
        schema = {"paths": {"/api/agents": {"get": {}}}}
        result = filter_openapi_to_public(schema, frozenset({("GET", "/api/agents")}))
        assert result == {"paths": {"/api/agents": {"get": {}}}}


@pytest.fixture(scope="module")
def app_schema() -> dict[str, Any]:
    return create_app().openapi()


class TestAppSchema:
    def test_publishes_exactly_the_public_api(self, app_schema: dict[str, Any]) -> None:
        published = {
            (method.upper(), path)
            for path, operations in app_schema["paths"].items()
            for method in operations
        }
        assert published == _PUBLIC_API

    def test_no_admin_endpoints(self, app_schema: dict[str, Any]) -> None:
        assert not [p for p in app_schema["paths"] if p.startswith("/api/admin")]

    def test_every_ref_resolves(self, app_schema: dict[str, Any]) -> None:
        import json

        names = set(app_schema["components"]["schemas"])
        text = json.dumps(app_schema)
        prefix = '"$ref": "#/components/schemas/'
        refs = {chunk.split('"', 1)[0] for chunk in text.split(prefix)[1:]}
        assert refs <= names

    def test_security_scheme_kept(self, app_schema: dict[str, Any]) -> None:
        assert "BearerAuth" in app_schema["components"]["securitySchemes"]
