"""``./start.sh portal`` is documented as production mode: it must build and
run ``next start``, not the Turbopack dev server."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _spy(path: Path, log: Path) -> None:
    path.write_text(f'#!/bin/sh\necho "$(basename "$0") $*" >> "{log}"\nexit 0\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_portal_runs_production_frontend(tmp_path: Path) -> None:
    log = tmp_path / "calls.log"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("pnpm", "uv", "lsof"):
        _spy(bindir / name, log)

    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    env.pop("LOG_LEVEL", None)
    subprocess.run(
        ["bash", str(ROOT / "start.sh"), "portal"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=60,
    )

    pnpm_calls = [c for c in log.read_text().splitlines() if c.startswith("pnpm ")]
    assert "pnpm dev" not in pnpm_calls
    assert "pnpm start" in pnpm_calls
    assert "pnpm build" in pnpm_calls
    assert pnpm_calls.index("pnpm build") < pnpm_calls.index("pnpm start")
