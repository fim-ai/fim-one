"""Structured application errors with machine-readable error codes.

Usage::

    from fim_agent.web.exceptions import AppError

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

from fastapi import HTTPException


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
        detail_args: dict | None = None,
    ) -> None:
        if detail is None:
            detail = error_code.replace("_", " ").capitalize()
        self.error_code = error_code
        self.detail_args = detail_args or {}
        super().__init__(status_code=status_code, detail=detail)
