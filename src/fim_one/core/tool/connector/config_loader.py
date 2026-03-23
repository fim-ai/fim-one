"""Parse YAML/JSON connector config files into Connector + ConnectorAction ORM instances.

Supports a declarative config format that describes a connector and its actions
in a single file, suitable for version control and sharing.

Example YAML config::

    name: Salesforce CRM
    description: Manage Salesforce contacts and leads
    icon: salesforce
    base_url: https://login.salesforce.com
    auth_type: oauth2
    actions:
      - name: get_contacts
        description: Retrieve contacts
        method: GET
        path: /services/data/v58.0/query
        parameters:
          - name: query
            type: string
            required: true
            description: SOQL query string
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import yaml  # type: ignore[import-untyped]


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ParameterConfig:
    """A single action parameter definition."""

    name: str
    type: str = "string"
    required: bool = False
    description: str | None = None
    default: Any = None
    enum: list[str] | None = None
    semantic_tag: str | None = None
    pii: bool = False


@dataclass
class ActionConfig:
    """A single connector action definition."""

    name: str
    description: str | None = None
    method: str = "GET"
    path: str = "/"
    parameters: list[ParameterConfig] = field(default_factory=list)
    request_body_template: dict[str, Any] | None = None
    response_mapping: dict[str, Any] | None = None
    requires_confirmation: bool | None = None  # None = auto-detect from method


@dataclass
class ConnectorConfig:
    """Top-level connector configuration."""

    name: str
    description: str | None = None
    icon: str | None = None
    base_url: str | None = None
    auth_type: str = "none"
    auth_config: dict[str, Any] | None = None
    actions: list[ActionConfig] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class ConfigValidationError(ValueError):
    """Raised when a connector config fails validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Config validation failed: {'; '.join(errors)}")


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

_VALID_FORMATS = {"auto", "yaml", "json"}


def detect_format(config_text: str) -> str:
    """Auto-detect whether config_text is JSON or YAML.

    Returns ``"json"`` or ``"yaml"``.
    """
    stripped = config_text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "json"
    return "yaml"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_connector_config(
    config_text: str,
    format: str = "auto",
) -> ConnectorConfig:
    """Parse a YAML or JSON connector config string into a ConnectorConfig.

    Args:
        config_text: Raw YAML or JSON text.
        format: One of ``"auto"``, ``"yaml"``, ``"json"``.

    Returns:
        A validated ``ConnectorConfig`` instance.

    Raises:
        ConfigValidationError: If the config is invalid.
    """
    if format not in _VALID_FORMATS:
        raise ConfigValidationError([f"Unsupported format: {format!r}. Must be one of: {', '.join(sorted(_VALID_FORMATS))}"])

    if not config_text or not config_text.strip():
        raise ConfigValidationError(["Config text is empty"])

    if format == "auto":
        format = detect_format(config_text)

    # Parse raw text into dict
    raw = _parse_raw(config_text, format)

    # Validate and convert to dataclass
    return _validate_and_build(raw)


def _parse_raw(config_text: str, format: str) -> dict[str, Any]:
    """Parse raw text into a dict."""
    if format == "json":
        try:
            parsed = json.loads(config_text)
        except json.JSONDecodeError as exc:
            raise ConfigValidationError([f"Invalid JSON: {exc}"]) from exc
    else:
        try:
            parsed = yaml.safe_load(config_text)
        except yaml.YAMLError as exc:
            raise ConfigValidationError([f"Invalid YAML: {exc}"]) from exc

    if not isinstance(parsed, dict):
        raise ConfigValidationError(["Config must be a JSON object or YAML mapping, not a scalar or list"])

    return parsed


def _validate_and_build(raw: dict[str, Any]) -> ConnectorConfig:
    """Validate raw dict and build ConnectorConfig."""
    errors: list[str] = []

    # Required fields
    name = raw.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        errors.append("'name' is required and must be a non-empty string")
    elif len(name) > 200:
        errors.append(f"'name' must be at most 200 characters (got {len(name)})")

    # Optional string fields
    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        errors.append("'description' must be a string")

    icon = raw.get("icon")
    if icon is not None and not isinstance(icon, str):
        errors.append("'icon' must be a string")

    base_url = raw.get("base_url")
    if base_url is not None:
        if not isinstance(base_url, str):
            errors.append("'base_url' must be a string")
        elif len(base_url) > 500:
            errors.append(f"'base_url' must be at most 500 characters (got {len(base_url)})")

    auth_type = raw.get("auth_type", "none")
    if not isinstance(auth_type, str):
        errors.append("'auth_type' must be a string")

    auth_config = raw.get("auth_config")
    if auth_config is not None and not isinstance(auth_config, dict):
        errors.append("'auth_config' must be an object/mapping")

    # Actions
    raw_actions = raw.get("actions", [])
    if not isinstance(raw_actions, list):
        errors.append("'actions' must be a list")
        raw_actions = []

    action_configs: list[ActionConfig] = []
    action_names: set[str] = set()

    for i, raw_action in enumerate(raw_actions):
        if not isinstance(raw_action, dict):
            errors.append(f"actions[{i}]: must be an object/mapping")
            continue

        action, action_errors = _validate_action(raw_action, i)
        errors.extend(action_errors)

        if action is not None:
            if action.name in action_names:
                errors.append(f"actions[{i}]: duplicate action name '{action.name}'")
            else:
                action_names.add(action.name)
                action_configs.append(action)

    if errors:
        raise ConfigValidationError(errors)

    return ConnectorConfig(
        name=name.strip(),  # type: ignore[union-attr]
        description=description,
        icon=icon,
        base_url=base_url,
        auth_type=auth_type if isinstance(auth_type, str) else "none",
        auth_config=auth_config,
        actions=action_configs,
    )


def _validate_action(
    raw: dict[str, Any], index: int
) -> tuple[ActionConfig | None, list[str]]:
    """Validate a single action dict."""
    errors: list[str] = []
    prefix = f"actions[{index}]"

    action_name = raw.get("name")
    if not action_name or not isinstance(action_name, str) or not action_name.strip():
        errors.append(f"{prefix}: 'name' is required and must be a non-empty string")
        return None, errors
    if len(action_name) > 200:
        errors.append(f"{prefix}: 'name' must be at most 200 characters")

    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        errors.append(f"{prefix}: 'description' must be a string")

    method = raw.get("method", "GET")
    if not isinstance(method, str):
        errors.append(f"{prefix}: 'method' must be a string")
        method = "GET"
    method = method.upper()
    valid_methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
    if method not in valid_methods:
        errors.append(f"{prefix}: 'method' must be one of {', '.join(sorted(valid_methods))}")

    path = raw.get("path", "/")
    if not isinstance(path, str):
        errors.append(f"{prefix}: 'path' must be a string")
        path = "/"
    if len(path) > 500:
        errors.append(f"{prefix}: 'path' must be at most 500 characters")

    # Parameters
    raw_params = raw.get("parameters", [])
    if not isinstance(raw_params, list):
        errors.append(f"{prefix}: 'parameters' must be a list")
        raw_params = []

    param_configs: list[ParameterConfig] = []
    param_names: set[str] = set()
    for j, raw_param in enumerate(raw_params):
        if not isinstance(raw_param, dict):
            errors.append(f"{prefix}.parameters[{j}]: must be an object/mapping")
            continue
        param, param_errors = _validate_parameter(raw_param, f"{prefix}.parameters[{j}]")
        errors.extend(param_errors)
        if param is not None:
            if param.name in param_names:
                errors.append(f"{prefix}.parameters[{j}]: duplicate parameter name '{param.name}'")
            else:
                param_names.add(param.name)
                param_configs.append(param)

    request_body_template = raw.get("request_body_template")
    if request_body_template is not None and not isinstance(request_body_template, dict):
        errors.append(f"{prefix}: 'request_body_template' must be an object")
        request_body_template = None

    response_mapping = raw.get("response_mapping")
    if response_mapping is not None and not isinstance(response_mapping, dict):
        errors.append(f"{prefix}: 'response_mapping' must be an object")
        response_mapping = None

    requires_confirmation = raw.get("requires_confirmation")
    if requires_confirmation is not None and not isinstance(requires_confirmation, bool):
        errors.append(f"{prefix}: 'requires_confirmation' must be a boolean")
        requires_confirmation = None

    if errors:
        return None, errors

    return ActionConfig(
        name=action_name.strip(),
        description=description,
        method=method,
        path=path,
        parameters=param_configs,
        request_body_template=request_body_template,
        response_mapping=response_mapping,
        requires_confirmation=requires_confirmation,
    ), errors


def _validate_parameter(
    raw: dict[str, Any], prefix: str
) -> tuple[ParameterConfig | None, list[str]]:
    """Validate a single parameter dict."""
    errors: list[str] = []

    param_name = raw.get("name")
    if not param_name or not isinstance(param_name, str) or not param_name.strip():
        errors.append(f"{prefix}: 'name' is required and must be a non-empty string")
        return None, errors

    param_type = raw.get("type", "string")
    if not isinstance(param_type, str):
        errors.append(f"{prefix}: 'type' must be a string")
        param_type = "string"

    valid_types = {"string", "integer", "number", "boolean", "array", "object"}
    if param_type not in valid_types:
        errors.append(f"{prefix}: 'type' must be one of {', '.join(sorted(valid_types))}")

    required = raw.get("required", False)
    if not isinstance(required, bool):
        errors.append(f"{prefix}: 'required' must be a boolean")
        required = False

    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        errors.append(f"{prefix}: 'description' must be a string")

    enum_vals = raw.get("enum")
    if enum_vals is not None:
        if not isinstance(enum_vals, list):
            errors.append(f"{prefix}: 'enum' must be a list")
            enum_vals = None

    semantic_tag = raw.get("semantic_tag")
    if semantic_tag is not None and not isinstance(semantic_tag, str):
        errors.append(f"{prefix}: 'semantic_tag' must be a string")
        semantic_tag = None

    pii = raw.get("pii", False)
    if not isinstance(pii, bool):
        errors.append(f"{prefix}: 'pii' must be a boolean")
        pii = False

    if errors:
        return None, errors

    return ParameterConfig(
        name=param_name.strip(),
        type=param_type,
        required=required,
        description=description,
        default=raw.get("default"),
        enum=enum_vals,
        semantic_tag=semantic_tag,
        pii=pii,
    ), errors


# ---------------------------------------------------------------------------
# Config -> ORM converter
# ---------------------------------------------------------------------------

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _params_to_schema(params: list[ParameterConfig]) -> dict[str, Any] | None:
    """Convert a list of ParameterConfig into a JSON Schema object.

    Returns ``None`` if there are no parameters.
    """
    if not params:
        return None

    properties: dict[str, Any] = {}
    required: list[str] = []

    for p in params:
        prop: dict[str, Any] = {"type": p.type}
        if p.description:
            prop["description"] = p.description
        if p.default is not None:
            prop["default"] = p.default
        if p.enum:
            prop["enum"] = p.enum
        if p.semantic_tag:
            prop["x-semantic-tag"] = p.semantic_tag
        if p.pii:
            prop["x-pii"] = True

        properties[p.name] = prop
        if p.required:
            required.append(p.name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required

    return schema


def config_to_connector(
    config: ConnectorConfig,
    user_id: str,
) -> tuple[Any, list[Any]]:
    """Convert a ConnectorConfig into ORM model instances.

    Returns a tuple of ``(Connector, list[ConnectorAction])``.
    The caller is responsible for adding them to the session and flushing.

    Note: imports are deferred to avoid circular imports at module level.
    """
    from fim_one.web.models.connector import Connector, ConnectorAction

    connector_id = str(uuid.uuid4())

    connector = Connector(
        id=connector_id,
        user_id=user_id,
        name=config.name,
        description=config.description,
        icon=config.icon,
        type="api",
        base_url=config.base_url,
        auth_type=config.auth_type,
        auth_config=config.auth_config,
        status="draft",
        visibility="personal",
    )

    actions: list[ConnectorAction] = []
    for action_cfg in config.actions:
        # Auto-detect requires_confirmation from method
        if action_cfg.requires_confirmation is not None:
            requires_confirmation = action_cfg.requires_confirmation
        else:
            requires_confirmation = action_cfg.method in _MUTATING_METHODS

        # Build response_extract from response_mapping
        response_extract: str | None = None
        if action_cfg.response_mapping and "output" in action_cfg.response_mapping:
            response_extract = str(action_cfg.response_mapping["output"])

        action = ConnectorAction(
            id=str(uuid.uuid4()),
            connector_id=connector_id,
            name=action_cfg.name,
            description=action_cfg.description,
            method=action_cfg.method,
            path=action_cfg.path,
            parameters_schema=_params_to_schema(action_cfg.parameters),
            request_body_template=action_cfg.request_body_template,
            response_extract=response_extract,
            requires_confirmation=requires_confirmation,
        )
        actions.append(action)

    return connector, actions


# ---------------------------------------------------------------------------
# Template generation
# ---------------------------------------------------------------------------

_YAML_TEMPLATE = """\
# Connector Configuration File
# This file defines a connector and its actions declaratively.
# Save as: my-service.connector.yaml

name: My API Service
description: A brief description of what this connector does
icon: globe  # Optional icon identifier
base_url: https://api.example.com
auth_type: none  # Options: none, bearer, api_key, basic, oauth2

# Optional auth configuration (depends on auth_type)
# auth_config:
#   header_name: X-API-Key      # for api_key
#   token_prefix: Bearer         # for bearer

actions:
  - name: list_items
    description: Retrieve a paginated list of items
    method: GET
    path: /v1/items
    parameters:
      - name: page
        type: integer
        description: Page number (1-based)
        default: 1
      - name: per_page
        type: integer
        description: Number of items per page
        default: 20
      - name: search
        type: string
        description: Search query to filter items
    response_mapping:
      output: items

  - name: get_item
    description: Retrieve a single item by ID
    method: GET
    path: /v1/items/{item_id}
    parameters:
      - name: item_id
        type: string
        required: true
        description: The unique identifier of the item

  - name: create_item
    description: Create a new item
    method: POST
    path: /v1/items
    requires_confirmation: true
    parameters:
      - name: name
        type: string
        required: true
        description: Name of the item
        semantic_tag: name
      - name: description
        type: string
        description: Detailed description of the item
      - name: email
        type: string
        description: Contact email for the item owner
        semantic_tag: email
        pii: true
      - name: tags
        type: array
        description: Tags for categorization
      - name: priority
        type: string
        description: Priority level
        enum:
          - low
          - medium
          - high

  - name: update_item
    description: Update an existing item
    method: PUT
    path: /v1/items/{item_id}
    parameters:
      - name: item_id
        type: string
        required: true
        description: The unique identifier of the item
      - name: name
        type: string
        description: Updated name
      - name: description
        type: string
        description: Updated description

  - name: delete_item
    description: Delete an item permanently
    method: DELETE
    path: /v1/items/{item_id}
    requires_confirmation: true
    parameters:
      - name: item_id
        type: string
        required: true
        description: The unique identifier of the item to delete
"""

_JSON_TEMPLATE = """\
{
  "name": "My API Service",
  "description": "A brief description of what this connector does",
  "icon": "globe",
  "base_url": "https://api.example.com",
  "auth_type": "none",
  "actions": [
    {
      "name": "list_items",
      "description": "Retrieve a paginated list of items",
      "method": "GET",
      "path": "/v1/items",
      "parameters": [
        {
          "name": "page",
          "type": "integer",
          "description": "Page number (1-based)",
          "default": 1
        },
        {
          "name": "per_page",
          "type": "integer",
          "description": "Number of items per page",
          "default": 20
        },
        {
          "name": "search",
          "type": "string",
          "description": "Search query to filter items"
        }
      ],
      "response_mapping": {
        "output": "items"
      }
    },
    {
      "name": "get_item",
      "description": "Retrieve a single item by ID",
      "method": "GET",
      "path": "/v1/items/{item_id}",
      "parameters": [
        {
          "name": "item_id",
          "type": "string",
          "required": true,
          "description": "The unique identifier of the item"
        }
      ]
    },
    {
      "name": "create_item",
      "description": "Create a new item",
      "method": "POST",
      "path": "/v1/items",
      "requires_confirmation": true,
      "parameters": [
        {
          "name": "name",
          "type": "string",
          "required": true,
          "description": "Name of the item",
          "semantic_tag": "name"
        },
        {
          "name": "description",
          "type": "string",
          "description": "Detailed description of the item"
        },
        {
          "name": "email",
          "type": "string",
          "description": "Contact email for the item owner",
          "semantic_tag": "email",
          "pii": true
        },
        {
          "name": "tags",
          "type": "array",
          "description": "Tags for categorization"
        },
        {
          "name": "priority",
          "type": "string",
          "description": "Priority level",
          "enum": ["low", "medium", "high"]
        }
      ]
    },
    {
      "name": "delete_item",
      "description": "Delete an item permanently",
      "method": "DELETE",
      "path": "/v1/items/{item_id}",
      "requires_confirmation": true,
      "parameters": [
        {
          "name": "item_id",
          "type": "string",
          "required": true,
          "description": "The unique identifier of the item to delete"
        }
      ]
    }
  ]
}
"""


def get_config_template(format: str = "yaml") -> str:
    """Return a starter connector config template.

    Args:
        format: ``"yaml"`` or ``"json"``.

    Returns:
        A complete example config string with comments/annotations.
    """
    if format == "json":
        return _JSON_TEMPLATE
    return _YAML_TEMPLATE
