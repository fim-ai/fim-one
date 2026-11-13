"""Every audited route is shown to write its record by a test that calls it.

The coverage tables say which routes record what, and a text check finds
the action's name in the route's module. Neither shows that a request to
the route ends with the row. This does: the tests listed in
``coverage.PROOF_TESTS`` are run with the harness observing every request
they make (the route reached, the status, the audit, sign-in and review
rows written by that request), and each route in ``AUDITED`` and
``AUDITED_READS`` must have been reached by a request that succeeded and
wrote a row of each action listed for it.

A route added to ``AUDITED`` with no test that calls it fails here, and so
does a test that stops reaching its route.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from . import coverage as c

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def observed(tmp_path_factory: pytest.TempPathFactory) -> list[dict[str, object]]:
    """What the proof tests' requests wrote, from a run of them in a process of its own."""
    out = tmp_path_factory.mktemp("observe") / "requests.jsonl"
    env = {**os.environ, "AUDIT_OBSERVE_OUT": str(out)}
    env.pop("FIM_TEST_PG_URL", None)  # the HTTP tests run on SQLite; nothing here needs more
    run = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", *c.PROOF_TESTS, "-q", "-p", "no:cacheprovider", "-x"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=840,
        check=False,
    )
    assert run.returncode == 0, "the proof tests did not pass:\n" + run.stdout[-3000:]
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]


def _proofs(observed: list[dict[str, object]]) -> dict[tuple[c.Route, str], str]:
    """(route, action written) -> the first test whose successful request wrote it."""
    proofs: dict[tuple[c.Route, str], str] = {}
    for request in observed:
        status = request["status"]
        if request["route"] is None or not isinstance(status, int) or status >= 400:
            continue
        route = (str(request["method"]), str(request["route"]))
        written = request["written"]
        assert isinstance(written, list)
        for action in written:
            proofs.setdefault((route, str(action)), str(request["test"]))
    return proofs


# Covered only by the billing smoke test, which is skipped without Stripe keys.
_BILLING_SMOKE_ROUTES = {
    ("POST", "/api/admin/billing/plans"),
    ("DELETE", "/api/admin/billing/plans/{plan_id}"),
    ("PATCH", "/api/admin/billing/plans/{plan_id}"),
    ("POST", "/api/admin/system/billing/activate"),
    ("POST", "/api/admin/system/billing/toggle"),
}


def _stripe_keys_set() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY", "").strip()) and bool(
        os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    )


@pytest.mark.timeout(900)
def test_every_audited_route_has_a_behaviour_proof(observed: list[dict[str, object]]) -> None:
    proofs = _proofs(observed)
    wanted = {**c.AUDITED, **c.AUDITED_READS}
    if not _stripe_keys_set():
        wanted = {
            route: names
            for route, names in wanted.items()
            if route not in _BILLING_SMOKE_ROUTES
        }

    unproven = [
        f"{method} {path}: {action}"
        for (method, path), names in sorted(wanted.items(), key=lambda item: item[0][::-1])
        for action in names
        if ((method, path), action) not in proofs
    ]
    assert not unproven, (
        "audited routes with no test that calls them and finds the row. Add a test "
        "(or a request in tests/audit/test_route_smoke.py):\n  " + "\n  ".join(unproven)
    )

    # How each route is proven, for whoever reads the run.
    by_means: Counter[str] = Counter()
    for route, names in wanted.items():
        tests = {proofs[(route, action)] for action in names}
        smoke_only = all(test.split("::")[0] in c.SMOKE_TESTS for test in tests)
        by_means["smoke request" if smoke_only else "dedicated test"] += 1
    print(f"\naudited routes proven: {dict(by_means)} of {len(wanted)}")


@pytest.mark.timeout(900)
def test_the_observer_sees_what_the_coverage_tables_do_not_claim(
    observed: list[dict[str, object]],
) -> None:
    """A route listed as exempt that wrote an audit row when called would be
    mislisted: the tables and the behaviour must agree in that direction too."""
    proofs = _proofs(observed)
    exempt_but_recording = sorted(
        f"{route[0]} {route[1]}: {action}"
        for (route, action) in proofs
        if route in c.EXEMPT and not action.startswith(c.REVIEW_LOG) and action != c.LOGIN
    )
    assert exempt_but_recording == []
