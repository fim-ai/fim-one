"""No API route may be unreachable behind an earlier path-parameter route.

Starlette matches routes in registration order. A static path such as
``GET /api/connectors/config-template`` registered *after*
``GET /api/connectors/{connector_id}`` is never reached: the parameter route
captures ``config-template`` as an id and answers 404. Three endpoints were
dead this way (connector config template, circuit-breaker status, DB schema
bulk update). This test walks the assembled app so any new instance fails
CI instead of shipping.
"""

from __future__ import annotations

from starlette.routing import Route

from fim_one.web import create_app


def _segments(path: str) -> list[str]:
    return path.strip("/").split("/")


def _shadows(earlier: str, later: str) -> bool:
    """True when every request for *later* is captured by *earlier*.

    Same segment count, every static segment of *earlier* equal to the one in
    *later*, and at least one place where *earlier* has a parameter while
    *later* has a static segment (otherwise the two paths are distinct).
    """
    a, b = _segments(earlier), _segments(later)
    if len(a) != len(b):
        return False
    captures_static = False
    for x, y in zip(a, b, strict=True):
        if x.startswith("{"):
            if not y.startswith("{"):
                captures_static = True
            continue
        if x != y:
            return False
    return captures_static


def _find_shadowed(routes: list[Route]) -> list[str]:
    found: list[str] = []
    for i, route in enumerate(routes):
        for earlier in routes[:i]:
            shared = (earlier.methods or set()) & (route.methods or set())
            if shared and _shadows(earlier.path, route.path):
                found.append(
                    f"{sorted(shared)} {route.path} is shadowed by {earlier.path}"
                )
    return found


class TestRouteShadowing:
    def test_detector_flags_static_after_param(self) -> None:
        routes = [
            Route("/api/x/{item_id}", endpoint=lambda r: None, methods=["GET"]),
            Route("/api/x/status", endpoint=lambda r: None, methods=["GET"]),
        ]
        assert _find_shadowed(routes) == [
            "['GET', 'HEAD'] /api/x/status is shadowed by /api/x/{item_id}"
        ]

    def test_detector_ignores_other_methods_and_order(self) -> None:
        routes = [
            Route("/api/x/status", endpoint=lambda r: None, methods=["GET"]),
            Route("/api/x/{item_id}", endpoint=lambda r: None, methods=["GET"]),
            Route("/api/y/{item_id}", endpoint=lambda r: None, methods=["PUT"]),
            Route("/api/y/bulk", endpoint=lambda r: None, methods=["POST"]),
        ]
        assert _find_shadowed(routes) == []

    def test_app_has_no_shadowed_routes(self) -> None:
        app = create_app()
        routes = [r for r in app.routes if isinstance(r, Route)]
        assert _find_shadowed(routes) == []
