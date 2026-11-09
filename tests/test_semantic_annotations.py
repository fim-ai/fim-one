"""Tests for semantic schema annotations on connector parameters."""

from __future__ import annotations

from typing import Any

import pytest

from fim_one.core.tool.connector.semantic_tags import (
    SEMANTIC_TAGS,
    build_annotated_description,
    enrich_parameters_schema,
    get_all_semantic_tags,
    is_valid_semantic_tag,
)
from fim_one.core.tool.connector.adapter import ConnectorToolAdapter


# ---------------------------------------------------------------------------
# SEMANTIC_TAGS dict
# ---------------------------------------------------------------------------


class TestSemanticTagsDict:
    """Verify the SEMANTIC_TAGS dictionary is well-formed."""

    def test_tags_not_empty(self) -> None:
        assert len(SEMANTIC_TAGS) > 0

    def test_all_keys_are_lowercase_strings(self) -> None:
        for key in SEMANTIC_TAGS:
            assert isinstance(key, str)
            assert key == key.lower(), f"Tag key '{key}' should be lowercase"

    def test_all_values_are_nonempty_strings(self) -> None:
        for key, value in SEMANTIC_TAGS.items():
            assert isinstance(value, str), f"Value for '{key}' must be a string"
            assert len(value) > 0, f"Value for '{key}' must not be empty"

    def test_expected_tags_present(self) -> None:
        expected = {
            "email", "phone", "name", "address", "currency", "date",
            "url", "id", "password", "api_key", "file_path", "json",
            "html", "markdown", "code", "query",
        }
        assert expected.issubset(set(SEMANTIC_TAGS.keys()))

    def test_get_all_semantic_tags_returns_copy(self) -> None:
        tags = get_all_semantic_tags()
        assert tags == SEMANTIC_TAGS
        # Mutating the returned dict must not affect the original
        tags["custom_tag"] = "Custom"
        assert "custom_tag" not in SEMANTIC_TAGS

    def test_is_valid_semantic_tag(self) -> None:
        assert is_valid_semantic_tag("email") is True
        assert is_valid_semantic_tag("nonexistent_tag_xyz") is False


# ---------------------------------------------------------------------------
# build_annotated_description
# ---------------------------------------------------------------------------


class TestBuildAnnotatedDescription:
    """Test individual description enrichment."""

    def test_no_annotations(self) -> None:
        result = build_annotated_description(None, None, None)
        assert result is None

    def test_description_only(self) -> None:
        result = build_annotated_description("Customer email", None, None)
        assert result == "Customer email"

    def test_semantic_tag_only(self) -> None:
        result = build_annotated_description(None, "email", None)
        assert result == "(type: email)"

    def test_pii_only(self) -> None:
        result = build_annotated_description(None, None, True)
        assert result == "[PII - handle with care]"

    def test_pii_false(self) -> None:
        result = build_annotated_description(None, None, False)
        assert result is None

    def test_all_annotations(self) -> None:
        result = build_annotated_description("Primary email", "email", True)
        assert result == "Primary email (type: email) [PII - handle with care]"

    def test_invalid_semantic_tag_ignored(self) -> None:
        result = build_annotated_description("Some field", "nonexistent_tag", None)
        # Invalid tag is silently ignored
        assert result == "Some field"

    def test_description_and_tag(self) -> None:
        result = build_annotated_description("The user phone", "phone", False)
        assert result == "The user phone (type: phone)"


# ---------------------------------------------------------------------------
# enrich_parameters_schema
# ---------------------------------------------------------------------------


class TestEnrichParametersSchema:
    """Test full schema enrichment."""

    def test_empty_schema(self) -> None:
        result = enrich_parameters_schema({})
        assert result == {}

    def test_schema_without_properties(self) -> None:
        schema: dict[str, Any] = {"type": "object", "required": []}
        result = enrich_parameters_schema(schema)
        assert result == schema

    def test_properties_without_annotations(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
            },
            "required": [],
        }
        result = enrich_parameters_schema(schema)
        # No annotations = no changes
        assert result["properties"]["limit"] == {"type": "integer"}
        assert result["properties"]["offset"] == {"type": "integer"}

    def test_semantic_tag_adds_type_hint(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "customer_email": {
                    "type": "string",
                    "semantic_tag": "email",
                },
            },
            "required": ["customer_email"],
        }
        result = enrich_parameters_schema(schema)
        desc = result["properties"]["customer_email"]["description"]
        assert "(type: email)" in desc

    def test_pii_adds_warning(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "ssn": {
                    "type": "string",
                    "description": "Social Security Number",
                    "pii": True,
                },
            },
            "required": [],
        }
        result = enrich_parameters_schema(schema)
        desc = result["properties"]["ssn"]["description"]
        assert "[PII - handle with care]" in desc
        assert "Social Security Number" in desc

    def test_full_annotations(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "user_email": {
                    "type": "string",
                    "description": "Primary contact email",
                    "semantic_tag": "email",
                    "pii": True,
                },
            },
            "required": ["user_email"],
        }
        result = enrich_parameters_schema(schema)
        desc = result["properties"]["user_email"]["description"]
        assert "Primary contact email" in desc
        assert "(type: email)" in desc
        assert "[PII - handle with care]" in desc

    def test_original_schema_not_mutated(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "semantic_tag": "email",
                    "pii": True,
                },
            },
            "required": [],
        }
        original_props = dict(schema["properties"]["email"])
        enrich_parameters_schema(schema)
        # Original should be untouched
        assert schema["properties"]["email"] == original_props

    def test_annotation_keys_preserved(self) -> None:
        """semantic_tag and pii keys should remain in the enriched schema."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "semantic_tag": "phone",
                    "pii": True,
                },
            },
            "required": [],
        }
        result = enrich_parameters_schema(schema)
        prop = result["properties"]["phone"]
        assert prop["semantic_tag"] == "phone"
        assert prop["pii"] is True

    def test_mixed_annotated_and_plain(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "semantic_tag": "name",
                    "pii": True,
                },
                "limit": {
                    "type": "integer",
                },
            },
            "required": [],
        }
        result = enrich_parameters_schema(schema)
        # Annotated prop should have enriched description
        assert "(type: name)" in result["properties"]["name"]["description"]
        # Plain prop should remain unchanged
        assert "description" not in result["properties"]["limit"]


# ---------------------------------------------------------------------------
# ConnectorToolAdapter integration
# ---------------------------------------------------------------------------


class TestConnectorToolAdapterAnnotations:
    """Test that ConnectorToolAdapter's parameters_schema includes annotations."""

    def _make_adapter(
        self, parameters_schema: dict[str, Any] | None = None,
    ) -> ConnectorToolAdapter:
        return ConnectorToolAdapter(
            connector_name="Test Connector",
            connector_base_url="https://api.example.com",
            connector_auth_type="none",
            connector_auth_config=None,
            action_name="get_user",
            action_description="Get a user by email",
            action_method="GET",
            action_path="/users",
            action_parameters_schema=parameters_schema,
            action_request_body_template=None,
            action_response_extract=None,
            action_requires_confirmation=False,
            on_call_complete=None,
        )

    def test_schema_without_annotations_unchanged(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
            },
            "required": ["user_id"],
        }
        adapter = self._make_adapter(schema)
        result = adapter.parameters_schema
        # No annotations, so description should not be added
        assert "description" not in result["properties"]["user_id"]

    def test_schema_with_annotations_enriched(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "User email",
                    "semantic_tag": "email",
                    "pii": True,
                },
            },
            "required": ["email"],
        }
        adapter = self._make_adapter(schema)
        result = adapter.parameters_schema
        desc = result["properties"]["email"]["description"]
        assert "User email" in desc
        assert "(type: email)" in desc
        assert "[PII - handle with care]" in desc

    def test_none_schema_returns_default(self) -> None:
        adapter = self._make_adapter(None)
        result = adapter.parameters_schema
        assert result["type"] == "object"
        assert result["properties"] == {}

    def test_backward_compatible_no_annotations(self) -> None:
        """Existing schemas without annotations work exactly as before."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "limit": {
                    "type": "integer",
                },
            },
            "required": ["query"],
        }
        adapter = self._make_adapter(schema)
        result = adapter.parameters_schema
        # Description stays the same when no annotation fields are present
        assert result["properties"]["query"]["description"] == "Search query"
        assert "description" not in result["properties"]["limit"]
