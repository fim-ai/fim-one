"""The one shape every refusal from the assembly layer takes."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ACCOUNT_DISABLED",
    "CONFIRMATION_UNDELIVERABLE",
    "CREDENTIAL_REQUIRED",
    "CREDENTIAL_UNAVAILABLE",
    "OWNER_ONLY",
    "RESOURCE_INACTIVE",
    "RESOURCE_NOT_FOUND",
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
