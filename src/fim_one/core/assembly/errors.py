"""The one shape every refusal from the assembly layer takes."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ACCOUNT_DISABLED",
    "AGENT_UNAVAILABLE",
    "APPROVAL_ASSIGNEE_NOT_IN_ORG",
    "CONFIRMATION_ALREADY_DECIDED",
    "CONFIRMATION_FORBIDDEN",
    "CONFIRMATION_NOT_FOUND",
    "CONFIRMATION_UNDELIVERABLE",
    "CREDENTIAL_REQUIRED",
    "CREDENTIAL_UNAVAILABLE",
    "OWNER_ONLY",
    "QUOTA_UNAVAILABLE",
    "RESOURCE_INACTIVE",
    "RESOURCE_NOT_FOUND",
    "SUBSCRIPTION_REQUIRED",
    "TOKEN_QUOTA_EXCEEDED",
    "WORKFLOW_OWNER_INACTIVE",
    "AssemblyRefusal",
]

# Error codes share the API's ``AppError`` namespace; the frontend's
# ``errors.json`` carries the message for each.
ACCOUNT_DISABLED = "account_disabled"
RESOURCE_NOT_FOUND = "resource_not_found"
RESOURCE_INACTIVE = "resource_inactive"
OWNER_ONLY = "owner_only"
CREDENTIAL_REQUIRED = "credential_required"
CREDENTIAL_UNAVAILABLE = "credential_unavailable"
CONFIRMATION_UNDELIVERABLE = "confirmation_undeliverable"
CONFIRMATION_NOT_FOUND = "confirmation_not_found"
CONFIRMATION_FORBIDDEN = "confirmation_forbidden"
CONFIRMATION_ALREADY_DECIDED = "confirmation_already_decided"
APPROVAL_ASSIGNEE_NOT_IN_ORG = "approval_assignee_not_in_org"
AGENT_UNAVAILABLE = "agent_unavailable"
SUBSCRIPTION_REQUIRED = "subscription_required"
TOKEN_QUOTA_EXCEEDED = "token_quota_exceeded"
QUOTA_UNAVAILABLE = "quota_unavailable"
WORKFLOW_OWNER_INACTIVE = "workflow_owner_inactive"


class AssemblyRefusal(Exception):  # noqa: N818 - a refusal is an outcome, not a fault
    """The assembly layer refused something, and why.

    ``error_code`` and ``status_code`` follow the API's ``AppError``
    conventions: an HTTP entry point lets the refusal propagate and the web
    exception handler renders it; a tool or workflow node turns ``detail``
    into its result text.
    """

    def __init__(
        self,
        error_code: str,
        status_code: int,
        detail: str | None = None,
        detail_args: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or error_code)
        self.error_code = error_code
        self.status_code = status_code
        self.detail = detail or error_code.replace("_", " ").capitalize()
        self.detail_args = detail_args
