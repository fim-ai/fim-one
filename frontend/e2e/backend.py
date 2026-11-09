"""Start a throwaway backend for the workflow smoke.

Run from the repository root (``playwright.config.ts`` does this):

    python frontend/e2e/backend.py

What it sets up, and why each piece is here:

* A fresh SQLite database, uploads and vector store under ``E2E_STATE_DIR``
  (wiped on every start), and that directory as the working directory, so
  the smoke never writes into the checkout or touches the developer's data.
* Every model endpoint (general, fast, reasoning, embeddings) pointed at the
  stub in ``e2e/stubs/server.mjs``. ``.env`` loading is switched off and
  model-related variables from the shell are dropped first, so a real key in
  the developer's environment cannot reach a real provider.
* Loopback allowed through the SSRF guard, in this process only: the
  connector the smoke creates targets the stub on 127.0.0.1, which the guard
  refuses in a normal deployment.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Variables that select or authenticate an outside service. Anything set in
#: the shell under these prefixes is removed before the app starts.
_SCRUBBED_PREFIXES = (
    "LLM_",
    "FAST_LLM_",
    "REASONING_LLM_",
    "EMBEDDING_",
    "RERANKER_",
    "JINA_",
    "COHERE_",
    "OPENAI_",
    "ANTHROPIC_",
    "TAVILY_",
    "BRAVE_",
    "REDIS_",
    "SMTP_",
    "STRIPE_",
    "MCP_SERVERS",
    "DATABASE_URL",
    "JWT_SECRET_KEY",
    "CREDENTIAL_ENCRYPTION_KEY",
)


def _configure_environment() -> tuple[Path, int]:
    state_dir = Path(os.environ.get("E2E_STATE_DIR") or REPO_ROOT / "frontend" / ".e2e-state")
    api_port = int(os.environ.get("E2E_API_PORT", "8765"))
    stub_port = int(os.environ.get("E2E_STUB_PORT", "8766"))
    stub = f"http://127.0.0.1:{stub_port}/v1"

    shutil.rmtree(state_dir, ignore_errors=True)
    state_dir.mkdir(parents=True)

    for key in list(os.environ):
        if key.startswith(_SCRUBBED_PREFIXES):
            del os.environ[key]

    # The stub lives on loopback; a proxy configured in the shell must not
    # be asked to reach it.
    no_proxy = [h for h in os.environ.get("NO_PROXY", "").split(",") if h]
    os.environ["NO_PROXY"] = ",".join(dict.fromkeys([*no_proxy, "127.0.0.1", "localhost"]))
    os.environ["no_proxy"] = os.environ["NO_PROXY"]

    os.environ.update(
        {
            # litellm calls load_dotenv() on import; keep the repo's .env out.
            "PYTHON_DOTENV_DISABLED": "1",
            "DATABASE_URL": f"sqlite+aiosqlite:///{state_dir / 'fim_one.db'}",
            "UPLOADS_DIR": str(state_dir / "uploads"),
            "VECTOR_STORE_DIR": str(state_dir / "vector_store"),
            # Set explicitly: an unset key is generated and written to the
            # repository's .env, which the smoke must leave alone.
            "JWT_SECRET_KEY": "e2e-smoke-secret-not-for-any-real-deployment",
            "CREDENTIAL_ENCRYPTION_KEY": "e2e-smoke-credential-key-not-for-real-use",
            "FIM_BCRYPT_COST": "4",
            "LLM_API_KEY": "sk-e2e-stub",
            "LLM_BASE_URL": stub,
            "LLM_MODEL": "stub-model",
            "FAST_LLM_MODEL": "stub-model",
            "LLM_MAX_RETRIES": "0",
            "EMBEDDING_API_KEY": "sk-e2e-stub",
            "EMBEDDING_BASE_URL": stub,
            "EMBEDDING_MODEL": "stub-embedding",
            "EMBEDDING_DIMENSION": "8",
            "ALLOW_STDIO_MCP": "true",
            "ALLOWED_STDIO_COMMANDS": "node",
            # The approval node stays behind its switch, off as shipped.
            "WORKFLOW_HUMAN_INTERVENTION_ENABLED": os.environ.get(
                "WORKFLOW_HUMAN_INTERVENTION_ENABLED", "false"
            ),
            "LOG_LEVEL": os.environ.get("E2E_LOG_LEVEL", "WARNING"),
        }
    )
    return state_dir, api_port


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "src" / "fim_one" / "migrations"))
    command.upgrade(cfg, "head")


def _allow_loopback() -> None:
    """Let this process call 127.0.0.1, where the stub connector target lives."""
    from fim_one.core.security import ssrf

    loopback = ipaddress.IPv4Network("127.0.0.0/8")
    ssrf._BLOCKED_IPV4_NETWORKS[:] = [
        net for net in ssrf._BLOCKED_IPV4_NETWORKS if net != loopback
    ]


def main() -> None:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    state_dir, api_port = _configure_environment()
    # The app resolves several defaults against the working directory
    # (uploads/kb, data/workspaces, data/dag_checkpoints) and some of them
    # ignore UPLOADS_DIR. Run inside the state directory so that all of it
    # lands there and nothing is written into the checkout.
    os.chdir(state_dir)
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
