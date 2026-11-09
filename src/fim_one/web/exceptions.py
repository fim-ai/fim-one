"""Structured application errors with machine-readable error codes.

Usage::

    from fim_one.web.exceptions import AppError

    raise AppError("agent_not_found", status_code=404)

    # With interpolation arguments for the frontend:
    raise AppError(
        "unsupported_file_type",
        status_code=422,
        detail=f"Unsupported file type: {ext}",
        detail_args={"ext": ext},
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

if TYPE_CHECKING:
    from fim_one.core.assembly.errors import AssemblyRefusal


class AppError(HTTPException):
    """HTTPException subclass that carries a structured ``error_code``.

    The global exception handler in ``app.py`` serialises this into a JSON
    response containing ``{detail, error_code, error_args}`` so that the
    frontend can look up translated messages via ``next-intl``.
    """

    def __init__(
        self,
        error_code: str,
        *,
        status_code: int = 400,
        detail: str | None = None,
        detail_args: dict[str, Any] | None = None,
    ) -> None:
        if detail is None:
            detail = error_code.replace("_", " ").capitalize()
        self.error_code = error_code
        self.detail_args = detail_args or {}
        super().__init__(status_code=status_code, detail=detail)

    @classmethod
    def from_refusal(cls, refusal: AssemblyRefusal) -> AppError:
        """The same code, status and detail as a refusal from the assembly layer.

        For endpoints that catch the refusal themselves (to add to it, or
        because they run under a router without the app's handlers); one
        that lets it propagate gets the same response from ``app.py``.
        """
        return cls(
            refusal.error_code,
            status_code=refusal.status_code,
            detail=refusal.detail,
            detail_args=refusal.detail_args,
        )
