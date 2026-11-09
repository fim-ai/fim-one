"""Start a throwaway backend for the workflow smoke.

``playwright.config.ts`` runs this from the repository root:

    python frontend/e2e/backend.py

What it guarantees, and how:

* **Nothing of the developer's is touched.** The process runs with the state
  directory as its working directory, so every path the app resolves against
  the working directory (``uploads``, ``data/...``) lands there, next to the
  SQLite database, the vector store and the uploads it is pointed at
  explicitly. A state directory is wiped on start only when it is the
  launcher's own (see ``prepare_state_dir``).
* **No real provider can be reached.** The environment is rebuilt from a
  short allowlist of system variables, every model endpoint is pointed at
  the stub in ``e2e/stubs/server.mjs``, and every other variable the app
  reads that names a key, secret, endpoint or provider is set to empty.
  Loading ``.env`` is switched off twice over (``PYTHON_DOTENV_DISABLED`` and
  a no-op ``load_dotenv``); should a ``.env`` be loaded anyway, it does not
  override variables that are already set, which by then is all of them.
* **Loopback is reachable.** The connector the smoke creates targets the
  stub on 127.0.0.1, which the SSRF guard refuses in a normal deployment; it
  is allowed in this process only.

``--print-env`` prepares the state directory and the environment, prints the
environment as JSON and exits without starting anything (used by the
harness's own tests).
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"

#: The only place a non-empty state directory may live (and be wiped).
DEFAULT_STATE_ROOT = REPO_ROOT / "frontend" / ".e2e-state"
#: Written into a state directory by this launcher. A non-empty directory
#: without it was not made by the launcher and is never deleted.
MARKER = ".fim-e2e-state"

#: Variables of the surrounding shell the process keeps. Everything else is
#: dropped, so nothing configured for a real deployment comes along.
_KEPT = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "TMPDIR",
    "TEMP",
    "TMP",
    "TZ",
    "TERM",
    "CI",
    "VIRTUAL_ENV",
    "SYSTEMROOT",
}
_KEPT_PREFIXES = ("LC_", "E2E_", "UV_")

#: A name that selects or authenticates an outside service.
_PROVIDER_NAME = re.compile(
    r"""
    (?:_API_KEY|_KEY|_SECRET|_TOKEN|_PASS|_PASSWORD|_URL|_HOST|_DSN|_ENDPOINT
       |_CLIENT_ID|_APP_ID|_PROVIDER|_SERVERS|_IMAGE|_TENANT_KEYS|_DOMAIN
       |_WEBSITE_ID|_MEASUREMENT_ID)$
    | ^(?:FEISHU|LARK|SLACK|WECOM|DISCORD|GITHUB|GOOGLE|AWS|STRIPE|SMTP|IMAGE_GEN|DOCKER)_
    """,
    re.VERBOSE,
)
#: Provider-like names that are numbers or switches: emptying them would
#: break parsing, and they reach nothing on their own.
_NOT_EMPTIED = {"SMTP_PORT", "SMTP_SSL", "DOCKER_MEMORY", "DOCKER_CPUS"}
_QUOTED_UPPER_NAME = re.compile(r"""["']([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)["']""")


def fail(message: str) -> None:
    print(f"[e2e backend] {message}", file=sys.stderr)
    raise SystemExit(2)


def prepare_state_dir(path: Path) -> Path:
    """Return an empty state directory that carries the launcher's marker.

    An existing, non-empty directory is wiped only when both hold: it lies
    under ``frontend/.e2e-state`` and it contains the marker this launcher
    wrote. Any other non-empty directory is refused, so a mistyped
    ``E2E_STATE_DIR`` cannot delete someone's files.
    """
    path = path.expanduser().resolve()
    root = DEFAULT_STATE_ROOT.resolve()
    if path.exists():
        if not path.is_dir():
            fail(f"state path {path} exists and is not a directory")
        if any(path.iterdir()):
            ours = (path == root or root in path.parents) and (path / MARKER).is_file()
            if not ours:
                fail(
                    f"refusing to wipe {path}: it is not empty and is not a state "
                    f"directory this launcher made (it must be under {root} and "
                    f"contain {MARKER}). Remove it yourself, or point E2E_STATE_DIR "
                    "at an empty directory."
                )
            shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    (path / MARKER).write_text(
        "Throwaway state of the workflow smoke (frontend/e2e/backend.py). "
        "Wiped on every run.\n",
        encoding="utf-8",
    )
    return path


def _names_in_source() -> set[str]:
    """Every quoted ALL_CAPS name in the backend source: a superset of the
    environment variables the app reads."""
    names: set[str] = set()
    for file in (SRC / "fim_one").rglob("*.py"):
        try:
            names.update(_QUOTED_UPPER_NAME.findall(file.read_text(encoding="utf-8")))
        except OSError:
            continue
    return names


def configure_environment(state_dir: Path, stub_port: int) -> None:
    stub = f"http://127.0.0.1:{stub_port}/v1"

    kept = {
        key: value
        for key, value in os.environ.items()
        if key in _KEPT or key.startswith(_KEPT_PREFIXES)
    }
    os.environ.clear()
    os.environ.update(kept)

    os.environ.update(
        {
            # .env stays out: litellm calls load_dotenv() on import.
            "PYTHON_DOTENV_DISABLED": "1",
            # The stub lives on loopback; no proxy is involved.
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "DATABASE_URL": f"sqlite+aiosqlite:///{state_dir / 'fim_one.db'}",
            "UPLOADS_DIR": str(state_dir / "uploads"),
            "VECTOR_STORE_DIR": str(state_dir / "vector_store"),
            # Set explicitly: an unset key is generated and written to the
            # repository's .env, which the smoke must leave alone.
            "JWT_SECRET_KEY": "e2e-smoke-secret-not-for-any-real-deployment",
            "CREDENTIAL_ENCRYPTION_KEY": "e2e-smoke-credential-key-not-for-real-use",
            "FIM_BCRYPT_COST": "4",
            # Every model role, and embeddings, at the stub.
            "LLM_API_KEY": "sk-e2e-stub",
            "LLM_BASE_URL": stub,
            "LLM_MODEL": "stub-model",
            "FAST_LLM_API_KEY": "sk-e2e-stub",
            "FAST_LLM_BASE_URL": stub,
            "FAST_LLM_MODEL": "stub-model",
            "REASONING_LLM_API_KEY": "sk-e2e-stub",
            "REASONING_LLM_BASE_URL": stub,
            "REASONING_LLM_MODEL": "stub-model",
            "LLM_MAX_RETRIES": "0",
            "EMBEDDING_API_KEY": "sk-e2e-stub",
            "EMBEDDING_BASE_URL": stub,
            "EMBEDDING_MODEL": "stub-embedding",
            "EMBEDDING_DIMENSION": "8",
            # Tools stay on this machine; no container, no mail.
            "CODE_EXEC_BACKEND": "local",
            "EMAIL_TOOL_ENABLED": "false",
            "ALLOW_STDIO_MCP": "true",
            "ALLOWED_STDIO_COMMANDS": "node",
            # The approval node stays behind its switch, off as shipped.
            "WORKFLOW_HUMAN_INTERVENTION_ENABLED": kept.get("E2E_HUMAN_INTERVENTION", "false"),
            "LOG_LEVEL": kept.get("E2E_LOG_LEVEL", "WARNING"),
        }
    )

    # Everything else that names a key, secret, endpoint or provider: empty.
    # A variable that is set, even to nothing, is not overridden by a .env.
    for name in _names_in_source():
        if name in os.environ or name in _NOT_EMPTIED:
            continue
        if _PROVIDER_NAME.search(name):
            os.environ[name] = ""


def _disable_dotenv() -> None:
    """Make ``load_dotenv`` do nothing for whoever imports it after this."""
    try:
        import dotenv
        import dotenv.main
    except ImportError:
        return

    def _no_dotenv(*_args: object, **_kwargs: object) -> bool:
        return False

    # Both the package and the module that defines it export the function.
    name = "load_dotenv"
    for module in (dotenv, dotenv.main):
        setattr(module, name, _no_dotenv)


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(SRC / "fim_one" / "migrations"))
    command.upgrade(cfg, "head")


def _allow_loopback() -> None:
    """Let this process call 127.0.0.1, where the stub connector target lives."""
    from fim_one.core.security import ssrf

    loopback = ipaddress.IPv4Network("127.0.0.0/8")
    ssrf._BLOCKED_IPV4_NETWORKS[:] = [
        net for net in ssrf._BLOCKED_IPV4_NETWORKS if net != loopback
    ]


def main() -> None:
    api_port = int(os.environ.get("E2E_API_PORT", "8765"))
    stub_port = int(os.environ.get("E2E_STUB_PORT", "8766"))
    state_dir = prepare_state_dir(Path(os.environ.get("E2E_STATE_DIR") or DEFAULT_STATE_ROOT))
    configure_environment(state_dir, stub_port)

    if "--print-env" in sys.argv[1:]:
        print(json.dumps(dict(os.environ), sort_keys=True))
        return

    _disable_dotenv()
    # The app resolves several defaults against the working directory
    # (uploads/kb, data/workspaces, data/dag_checkpoints) and some of them
    # ignore UPLOADS_DIR. Run inside the state directory so that all of it
    # lands there and nothing is written into the checkout.
    os.chdir(state_dir)
    sys.path.insert(0, str(SRC))

    _migrate()
    _allow_loopback()

    import uvicorn

    uvicorn.run(
        "fim_one.web:create_app",
        factory=True,
        host="127.0.0.1",
        port=api_port,
        log_level=os.environ["LOG_LEVEL"].lower(),
    )


if __name__ == "__main__":
    main()
