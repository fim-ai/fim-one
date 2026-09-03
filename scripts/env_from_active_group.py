#!/usr/bin/env python3
"""Sync the ``.env`` LLM fallback to the active model group in the database.

The runtime resolves models from the active model group (admin panel) and only
falls back to ``LLM_*`` / ``FAST_LLM_*`` / ``REASONING_LLM_*`` when no group is
active. Scripts that never touch the DB (``scripts/translate.py``, ``evals/``)
read the env directly. Left alone, the env drifts from what the admin panel
says and stale relays keep getting traffic from the side paths.

This script copies the active group's general / fast / reasoning models into
``.env`` so both layers name the same provider. Secrets are read through the
ORM (decrypted in-process) and written straight to the file; nothing is
printed beyond a masked suffix.

Usage:
    uv run scripts/env_from_active_group.py                # rewrite .env
    uv run scripts/env_from_active_group.py --dry-run      # show the plan only
    uv run scripts/env_from_active_group.py --image-gen    # also point IMAGE_GEN_* at the general provider
    uv run scripts/env_from_active_group.py --env /path/to/.env

The DB is located the same way the app locates it: ``DATABASE_URL`` from the
env file (default: the SQLite dev database). Run it on the host that owns the
``.env`` you want to update.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Keys this script owns. Anything else in the file is left untouched.
GENERAL_KEYS = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_CONTEXT_SIZE",
    "LLM_MAX_OUTPUT_TOKENS",
)
TIER_KEYS = {
    "FAST": ("FAST_LLM_MODEL", "FAST_LLM_API_KEY", "FAST_LLM_BASE_URL"),
    "REASONING": ("REASONING_LLM_MODEL", "REASONING_LLM_API_KEY", "REASONING_LLM_BASE_URL"),
}
IMAGE_KEYS = ("IMAGE_GEN_PROVIDER", "IMAGE_GEN_API_KEY", "IMAGE_GEN_BASE_URL", "IMAGE_GEN_MODEL")

_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _load_env_file(path: Path) -> None:
    """Populate ``os.environ`` from the env file without clobbering the shell."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = _LINE_RE.match(raw)
        if not m or raw.lstrip().startswith("#"):
            continue
        key, value = m.group(1), m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _mask(secret: str | None) -> str:
    if not secret:
        return "(empty)"
    if len(secret) <= 8:
        return "****"
    return f"{secret[:3]}…{secret[-4:]}"


async def _load_active_group() -> dict[str, dict[str, Any] | None] | None:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from fim_one.db.engine import create_session, init_db, shutdown_db
    from fim_one.db.models.model_provider import ModelGroup, ModelProviderModel

    # The default SQLite URL is relative to the repo root, like the app itself.
    os.chdir(ROOT)
    await init_db()
    session = create_session()
    try:
        stmt = (
            select(ModelGroup)
            .where(ModelGroup.is_active == True)  # noqa: E712
            .options(
                selectinload(ModelGroup.general_model).selectinload(ModelProviderModel.provider),
                selectinload(ModelGroup.fast_model).selectinload(ModelProviderModel.provider),
                selectinload(ModelGroup.reasoning_model).selectinload(ModelProviderModel.provider),
            )
            .limit(1)
        )
        group = (await session.execute(stmt)).scalar_one_or_none()
        if group is None:
            return None

        def slot(model: Any) -> dict[str, Any] | None:
            if model is None or not model.is_active:
                return None
            provider = model.provider
            if provider is None or not provider.is_active:
                return None
            return {
                "group": group.name,
                "model": model.model_name,
                "base_url": provider.base_url or "",
                "api_key": provider.api_key or "",
                "provider": provider.name,
                "context_size": model.context_size,
                "max_output_tokens": model.max_output_tokens,
            }

        return {
            "general": slot(group.general_model),
            "fast": slot(group.fast_model),
            "reasoning": slot(group.reasoning_model),
        }
    finally:
        await session.close()
        await shutdown_db()


def _plan(slots: dict[str, dict[str, Any] | None], *, image_gen: bool) -> dict[str, str | None]:
    """Return {ENV_KEY: value | None}; ``None`` means comment the line out."""
    general = slots["general"]
    if general is None:
        raise SystemExit("active group has no usable general model; refusing to write .env")

    plan: dict[str, str | None] = {
        "LLM_API_KEY": general["api_key"],
        "LLM_BASE_URL": general["base_url"],
        "LLM_MODEL": general["model"],
        "LLM_CONTEXT_SIZE": str(general["context_size"]) if general["context_size"] else None,
        "LLM_MAX_OUTPUT_TOKENS": (
            str(general["max_output_tokens"]) if general["max_output_tokens"] else None
        ),
    }
    for tier, (k_model, k_key, k_base) in TIER_KEYS.items():
        s = slots[tier.lower()] or general
        plan[k_model] = s["model"]
        same_provider = s["base_url"] == general["base_url"] and s["api_key"] == general["api_key"]
        plan[k_key] = None if same_provider else s["api_key"]
        plan[k_base] = None if same_provider else s["base_url"]
    if image_gen:
        plan["IMAGE_GEN_PROVIDER"] = "openai"
        plan["IMAGE_GEN_API_KEY"] = general["api_key"]
        plan["IMAGE_GEN_BASE_URL"] = general["base_url"]
        if "api.openai.com" in general["base_url"]:
            plan["IMAGE_GEN_MODEL"] = "gpt-image-1"
    return plan


def _rewrite(path: Path, plan: dict[str, str | None]) -> list[str]:
    """Apply the plan in place. Returns the human-readable change list."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    changes: list[str] = []
    out: list[str] = []
    for raw in lines:
        m = _LINE_RE.match(raw)
        key = m.group(1) if m else None
        commented = raw.lstrip().startswith("#")
        if key in plan and not commented:
            seen.add(key)
            new = plan[key]
            if new is None:
                out.append(f"# {raw.strip()}")
                changes.append(f"  - {key}: commented out")
            else:
                out.append(f"{key}={new}")
                changes.append(f"  ~ {key}: {_describe(key, new)}")
            continue
        out.append(raw)
    missing = [k for k, v in plan.items() if k not in seen and v is not None]
    if missing:
        out.append("")
        out.append("# --- synced from the active model group by scripts/env_from_active_group.py ---")
        for k in missing:
            out.append(f"{k}={plan[k]}")
            changes.append(f"  + {k}: {_describe(k, plan[k] or '')}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changes


def _describe(key: str, value: str) -> str:
    return _mask(value) if key.endswith("_KEY") else value


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default=str(ROOT / ".env"), help="env file to rewrite (default: repo .env)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, touch nothing")
    ap.add_argument("--image-gen", action="store_true", help="also point IMAGE_GEN_* at the general provider")
    args = ap.parse_args()

    env_path = Path(args.env).resolve()
    _load_env_file(env_path)

    slots = asyncio.run(_load_active_group())
    if slots is None:
        print("No active model group in the database; nothing to sync.")
        return 1

    plan = _plan(slots, image_gen=args.image_gen)
    general = slots["general"]
    assert general is not None
    print(f"Active group: {general['group']}")
    for tier in ("general", "fast", "reasoning"):
        s = slots[tier]
        if s is None:
            print(f"  {tier:9s} (unset → falls back to general)")
        else:
            print(f"  {tier:9s} {s['model']}  via {s['provider']} ({s['base_url']}, key {_mask(s['api_key'])})")

    if args.dry_run:
        print("\nPlanned .env values:")
        for k, v in plan.items():
            print(f"  {k}={'(comment out)' if v is None else _describe(k, v)}")
        return 0

    if env_path.exists():
        backup = env_path.with_name(env_path.name + ".bak")
        shutil.copy2(env_path, backup)
        print(f"\nBackup: {backup}")
    changes = _rewrite(env_path, plan)
    print(f"Wrote {env_path}:")
    print("\n".join(changes) or "  (no changes)")
    print("\nRestart the app (or re-run the script that reads .env) to pick this up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
