#!/usr/bin/env python3
"""Incremental translation script for FIM One i18n JSON, docs MDX, and README files.

Uses the project's Fast LLM via LiteLLM (supports OpenAI, Anthropic, and any
OpenAI-compatible relay). Provider is auto-detected from the base URL.

Usage:
    uv run scripts/translate.py                     # translate only git-staged EN files
    uv run scripts/translate.py --all               # retranslate everything (ignore cache)
    uv run scripts/translate.py --files a b c       # specific files
    uv run scripts/translate.py --locale zh ja      # override target locales
    uv run scripts/translate.py --concurrency 10    # max parallel API calls (default: 3)

Environment (read from .env + os.environ):
    FAST_LLM_API_KEY   → fallback LLM_API_KEY
    FAST_LLM_BASE_URL  → fallback LLM_BASE_URL  → default https://api.openai.com/v1
    FAST_LLM_MODEL     → fallback LLM_MODEL      → default gpt-4o-mini

Git hook mode (GIT_HOOK=1): after translating, runs `git add` on all output files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import litellm

# Align with project's LiteLLM config
litellm.num_retries = 0  # We handle retries ourselves
litellm.drop_params = True
litellm.suppress_debug_info = True


# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Thread-safe print
# ---------------------------------------------------------------------------

_print_lock = threading.Lock()
# Global semaphore limiting concurrent API calls (not file workers).
# Prevents overwhelming API proxies. Set via --concurrency flag.
_api_semaphore: threading.Semaphore | None = None


def tprint(*args: Any, **kwargs: Any) -> None:
    with _print_lock:
        print(*args, **kwargs, flush=True)


# ---------------------------------------------------------------------------
# Section-based translation cache (thread-safe)
# ---------------------------------------------------------------------------

_CACHE_PATH = ROOT / ".translation-cache.json"
_cache: dict | None = None
_cache_lock = threading.Lock()


def _get_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8")) if _CACHE_PATH.exists() else {}
        except Exception:
            _cache = {}
    return _cache


def _flush_cache() -> None:
    with _cache_lock:
        if _cache is not None:
            _CACHE_PATH.write_text(
                json.dumps(_cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )


def _cache_get(cache_key: str, locale: str, h: str) -> str | None:
    with _cache_lock:
        return _get_cache().get(cache_key, {}).get(locale, {}).get(h)


def _cache_put(cache_key: str, locale: str, updates: dict[str, str]) -> None:
    with _cache_lock:
        cache = _get_cache()
        cache.setdefault(cache_key, {}).setdefault(locale, {}).update(updates)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _split_sections(content: str) -> list[str]:
    """Split markdown/MDX into sections at each heading line."""
    sections: list[str] = []
    current: list[str] = []
    for line in content.splitlines(keepends=True):
        if line.startswith("#") and current:
            sections.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("".join(current))
    return sections


# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------

def load_dotenv(env_path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not env_path.exists():
        return result
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        for quote in ('"', "'"):
            if value.startswith(quote) and value.endswith(quote) and len(value) >= 2:
                value = value[1:-1]
                break
        else:
            if " #" in value:
                value = value[: value.index(" #")].strip()
        result[key] = value
    return result


def _env(dotenv: dict[str, str], *keys: str, default: str = "") -> str:
    for key in keys:
        val = os.environ.get(key) or dotenv.get(key, "")
        if val:
            return val
    return default


def load_llm_config() -> dict[str, str]:
    dotenv = load_dotenv(ROOT / ".env")
    return {
        "api_key": _env(dotenv, "FAST_LLM_API_KEY", "LLM_API_KEY"),
        "base_url": _env(dotenv, "FAST_LLM_BASE_URL", "LLM_BASE_URL", default="https://api.openai.com/v1"),
        "model": _env(dotenv, "FAST_LLM_MODEL", "LLM_MODEL", default="gpt-4o-mini"),
    }


# ---------------------------------------------------------------------------
# Provider resolution (mirrors src/fim_one/core/model/openai_compatible.py)
# ---------------------------------------------------------------------------

KNOWN_DOMAINS: dict[str, str] = {
    "api.openai.com": "openai",
    "anthropic.com": "anthropic",
    "generativelanguage.googleapis.com": "gemini",
    "api.deepseek.com": "deepseek",
    "api.mistral.ai": "mistral",
}

PATH_PROVIDER_HINTS: dict[str, str] = {
    "/claude": "anthropic",
    "/anthropic": "anthropic",
    "/gemini": "gemini",
}


def _resolve_litellm_model(base_url: str, model: str) -> tuple[str, str | None]:
    """Map (base_url, model) to (litellm_model_string, optional api_base)."""
    for domain, prov in KNOWN_DOMAINS.items():
        if domain in base_url:
            return f"{prov}/{model}", None
    for path_segment, prov in PATH_PROVIDER_HINTS.items():
        if path_segment in base_url:
            return f"{prov}/{model}", base_url
    return f"openai/{model}", base_url


# ---------------------------------------------------------------------------
# LLM call via LiteLLM (handles Anthropic, OpenAI, etc. automatically)
# ---------------------------------------------------------------------------

def llm_chat(config: dict[str, str], system_prompt: str, user_content: str, temperature: float = 0.3) -> str:
    if _api_semaphore is not None:
        _api_semaphore.acquire()
    try:
        return _llm_chat_inner(config, system_prompt, user_content, temperature)
    finally:
        if _api_semaphore is not None:
            _api_semaphore.release()


def _llm_chat_inner(config: dict[str, str], system_prompt: str, user_content: str, temperature: float = 0.3) -> str:
    import random

    litellm_model, api_base = _resolve_litellm_model(config["base_url"], config["model"])

    kwargs: dict[str, Any] = {
        "model": litellm_model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    if config.get("api_key"):
        kwargs["api_key"] = config["api_key"]
    if api_base:
        kwargs["api_base"] = api_base

    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(4):
        if attempt > 0:
            backoff = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(backoff)
        try:
            response = litellm.completion(**kwargs)
            return response.choices[0].message.content
        except Exception as exc:
            last_exc = exc
            continue

    raise last_exc


# ---------------------------------------------------------------------------
# JSON flattening helpers
# ---------------------------------------------------------------------------

def flatten_json(obj: Any, prefix: str = "") -> dict[str, str]:
    """Flatten nested dict/list to dotted-key → string-value pairs.

    Arrays are indexed with [0], [1], etc.:
        examples.react[0].text → "Find the top 5..."
    """
    result: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                result.update(flatten_json(v, full_key))
            elif isinstance(v, str):
                result[full_key] = v
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            full_key = f"{prefix}[{i}]"
            if isinstance(v, (dict, list)):
                result.update(flatten_json(v, full_key))
            elif isinstance(v, str):
                result[full_key] = v
    return result


import re as _re

_ARRAY_INDEX_RE = _re.compile(r"\[(\d+)\]")


def unflatten_json(flat: dict[str, str]) -> dict[str, Any]:
    """Reconstruct nested dict/list from dotted-key → value pairs.

    Keys like 'a.b[0].c' are split into path segments: ['a', 'b', '[0]', 'c'].
    Numeric [N] segments create/extend lists; others create dicts.
    """
    root: dict[str, Any] = {}

    def _set(path_parts: list[str], value: str) -> None:
        node: Any = root
        for i, part in enumerate(path_parts[:-1]):
            next_part = path_parts[i + 1]
            next_is_index = next_part.startswith("[")
            m = _ARRAY_INDEX_RE.fullmatch(part)
            if m:
                idx = int(m.group(1))
                while len(node) <= idx:
                    node.append([] if next_is_index else {})
                node = node[idx]
            else:
                if part not in node:
                    node[part] = [] if next_is_index else {}
                node = node[part]
        # Set the leaf
        last = path_parts[-1]
        m = _ARRAY_INDEX_RE.fullmatch(last)
        if m:
            idx = int(m.group(1))
            while len(node) <= idx:
                node.append(None)
            node[idx] = value
        else:
            node[last] = value

    for dotted_key, value in flat.items():
        # Split "a.b[0].c[1]" → ["a", "b", "[0]", "c", "[1]"]
        parts: list[str] = []
        for segment in dotted_key.split("."):
            # Split "react[0]" → ["react", "[0]"]
            tokens = _ARRAY_INDEX_RE.split(segment)
            for j, tok in enumerate(tokens):
                if tok:
                    parts.append(f"[{tok}]" if j % 2 == 1 else tok)
        _set(parts, value)

    return root


# ---------------------------------------------------------------------------
# Locale discovery
# ---------------------------------------------------------------------------

def discover_locales() -> list[str]:
    messages_dir = ROOT / "frontend" / "messages"
    if not messages_dir.exists():
        return []
    return sorted(d.name for d in messages_dir.iterdir() if d.is_dir() and d.name != "en")


# ---------------------------------------------------------------------------
# Staged file detection
# ---------------------------------------------------------------------------

def get_staged_en_files() -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, check=True, cwd=ROOT,
        )
    except subprocess.CalledProcessError:
        return []

    locale_dirs = discover_locales()
    docs_locale_prefixes = tuple(f"docs/{loc}/" for loc in locale_dirs) + ("docs/en/",)

    paths: list[Path] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        p = Path(line)
        if str(p).startswith("frontend/messages/en/") and p.suffix == ".json":
            paths.append(ROOT / p)
        elif str(p).startswith("docs/") and p.suffix == ".mdx":
            if not str(p).startswith(docs_locale_prefixes):
                paths.append(ROOT / p)
        elif line == "README.md":
            paths.append(ROOT / p)
    return paths


# ---------------------------------------------------------------------------
# JSON translation
# ---------------------------------------------------------------------------

_JSON_SYSTEM_PROMPT = """\
You are a professional software localisation expert. \
Your task is to translate UI string values from English to {locale}. \
Rules:
- Translate ONLY the values, never the keys.
- Preserve all placeholder tokens literally, e.g. {{name}}, {{count}}, {{0}}.
- Keep HTML tags intact.
- Keep these terms in English: API, MCP, DAG, FIM One, OAuth, SSO, JWT, SSE, LLM, RAG.
- Translate domain terms naturally: "agent" → localised equivalent (e.g. 智能体 in zh), \
  "connector" → localised equivalent (e.g. 连接器 in zh). Do NOT keep them in English.
- Return ONLY a valid JSON object mapping the same keys to their translated values. \
  No markdown fences, no extra text — pure JSON only.
"""


def translate_json_file(src_path: Path, locale: str, config: dict[str, str], force: bool = False, batch_size: int = 4000) -> Path | None:
    """Translate a JSON i18n file with full change detection: add, modify, delete.

    Uses .translation-cache.json to store EN source hashes per key.
    - Added key: EN key not in target → translate
    - Modified key: EN value hash differs from cached hash → retranslate
    - Deleted key: target key not in EN source → remove from target
    - Unchanged: EN hash matches cache → skip
    """
    filename = src_path.name
    target_path = ROOT / "frontend" / "messages" / locale / filename
    cache_key = f"json_hashes/{filename}"

    try:
        src_data: Any = json.loads(src_path.read_text(encoding="utf-8"))
    except Exception as exc:
        tprint(f"  [{locale}] {filename}: ERROR reading source — {exc}")
        return None

    src_flat = flatten_json(src_data)

    # Load existing target translations
    existing_flat: dict[str, str] = {}
    if target_path.exists():
        try:
            existing_flat = flatten_json(json.loads(target_path.read_text(encoding="utf-8")))
        except Exception:
            pass

    # Load cached EN source hashes: {key: hash_of_en_value}
    with _cache_lock:
        cache = _get_cache()
        stored_hashes: dict[str, str] = cache.get(cache_key, {}).get(locale, {})

    # Classify each key
    to_translate: dict[str, str] = {}  # keys needing (re)translation
    added = modified = deleted = unchanged = 0

    for key, en_value in src_flat.items():
        en_hash = _hash(en_value)
        if force:
            to_translate[key] = en_value
        elif key not in existing_flat:
            to_translate[key] = en_value
            added += 1
        elif stored_hashes.get(key) != en_hash:
            # EN value changed since last translation → retranslate
            to_translate[key] = en_value
            modified += 1
        else:
            unchanged += 1

    # Detect deleted keys (in target but not in EN source)
    deleted_keys = set(existing_flat.keys()) - set(src_flat.keys())
    deleted = len(deleted_keys)

    if not to_translate and not deleted_keys and not force:
        tprint(f"  [{locale}] {filename}: up to date ({unchanged} keys)")
        return None

    # Log what's happening
    parts = []
    if force:
        parts.append(f"force {len(to_translate)} keys")
    else:
        if added:
            parts.append(f"+{added} new")
        if modified:
            parts.append(f"~{modified} modified")
        if deleted:
            parts.append(f"-{deleted} deleted")
        if unchanged:
            parts.append(f"{unchanged} unchanged")
    tprint(f"  [{locale}] {filename}: {', '.join(parts)}")

    # Translate new/modified keys in batches, sized by character length.
    # ~4000 chars ≈ ~1500 tokens input → ~2000 tokens output (safe for most models).
    MAX_BATCH_CHARS = batch_size
    translated: dict[str, str] = {}
    if to_translate:
        system = _JSON_SYSTEM_PROMPT.format(locale=locale)

        def _split_into_batches(items: list[tuple[str, str]]) -> list[dict[str, str]]:
            batches: list[dict[str, str]] = []
            current: dict[str, str] = {}
            current_chars = 0
            for key, value in items:
                entry_chars = len(key) + len(value) + 10
                if current and current_chars + entry_chars > MAX_BATCH_CHARS:
                    batches.append(current)
                    current = {}
                    current_chars = 0
                current[key] = value
                current_chars += entry_chars
            if current:
                batches.append(current)
            return batches

        def _parse_response(response_text: str) -> dict[str, str]:
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                inner = lines[1:] if lines[0].startswith("```") else lines
                if inner and inner[-1].strip() == "```":
                    inner = inner[:-1]
                cleaned = "\n".join(inner)
            return json.loads(cleaned)

        def _translate_batch(batch: dict[str, str], depth: int = 0) -> dict[str, str]:
            """Translate a batch; on JSON parse failure, split in half and retry."""
            try:
                response_text = llm_chat(config, system, json.dumps(batch, ensure_ascii=False))
            except Exception as exc:
                tprint(f"  [{locale}] {filename}: WARNING — LLM error ({len(batch)} keys): {exc}")
                return {}
            try:
                return _parse_response(response_text)
            except json.JSONDecodeError:
                if len(batch) <= 2:
                    tprint(f"  [{locale}] {filename}: WARNING — {len(batch)} key(s) failed JSON parse, skipping")
                    return {}
                # Split in half and retry
                items = list(batch.items())
                mid = len(items) // 2
                tprint(f"  [{locale}] {filename}: batch parse failed, splitting {len(batch)} → {mid}+{len(items)-mid} and retrying")
                left = _translate_batch(dict(items[:mid]), depth + 1)
                right = _translate_batch(dict(items[mid:]), depth + 1)
                return {**left, **right}

        batches = _split_into_batches(list(to_translate.items()))
        total_batches = len(batches)

        for batch_idx, batch in enumerate(batches):
            if total_batches > 1:
                tprint(f"  [{locale}] {filename}: batch {batch_idx + 1}/{total_batches} ({len(batch)} keys)")
            result = _translate_batch(batch)
            translated.update(result)

    # If nothing was successfully translated at all, don't write a broken file
    if not translated and not existing_flat:
        tprint(f"  [{locale}] {filename}: all batches failed, skipping file (will retry next run)")
        return None

    # Merge: existing + successful translations, remove deleted keys
    merged_flat = {**existing_flat, **translated}
    for key in deleted_keys:
        merged_flat.pop(key, None)
    # Fill missing keys with EN fallback (these won't be cached as "done")
    for k, v in src_flat.items():
        if k not in merged_flat:
            merged_flat[k] = v

    # Cache hashes ONLY for keys that have real translations (not EN fallback)
    successfully_translated_keys = set(existing_flat.keys()) | set(translated.keys())
    new_hashes = {key: _hash(src_flat[key]) for key in successfully_translated_keys if key in src_flat}
    if new_hashes:
        _cache_put(cache_key, locale, new_hashes)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(unflatten_json(merged_flat), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failed_count = len(src_flat) - len(successfully_translated_keys)
    if failed_count > 0:
        tprint(f"  [{locale}] {filename}: saved ({failed_count} keys kept EN — will retry next run)")
    else:
        tprint(f"  [{locale}] {filename}: saved")
    return target_path


# ---------------------------------------------------------------------------
# Section-based translation (shared by MDX + README)
# Sections within a file are translated concurrently.
# ---------------------------------------------------------------------------

def _translate_sections(
    src_path: Path,
    content: str,
    locale: str,
    config: dict[str, str],
    system_prompt: str,
    cache_key: str,
    inner_workers: int = 5,
    force: bool = False,
) -> str:
    sections = _split_sections(content)
    hashes = [_hash(s) for s in sections]

    # Separate cached vs. needs translation
    # force=True skips cache lookup entirely (used by --all)
    cached: dict[int, str] = {}
    to_translate: dict[int, str] = {}
    for i, (section, h) in enumerate(zip(sections, hashes)):
        hit = None if force else _cache_get(cache_key, locale, h)
        if hit is not None:
            cached[i] = hit
        else:
            to_translate[i] = section

    hits = len(cached)
    total = len(sections)
    new_translations: dict[str, str] = {}  # hash -> translated (only successful)
    failed_indices: set[int] = set()

    if to_translate:
        tprint(f"  [{locale}] {src_path.name}: {len(to_translate)}/{total} sections to translate, {hits} cached")

        def _translate_one(idx: int, section: str) -> tuple[int, str | None]:
            try:
                translated = llm_chat(config, system_prompt, section)
                # Preserve trailing whitespace from original section.
                # LLMs often strip trailing newlines, which causes sections
                # to be glued together (e.g. "...value).## Next Heading").
                orig_stripped = section.rstrip()
                trailing = section[len(orig_stripped):]
                if trailing and not translated.endswith(trailing):
                    translated = translated.rstrip() + trailing
                return idx, translated
            except Exception as exc:
                tprint(f"  [{locale}] {src_path.name}: WARNING section {idx} failed — {exc}")
                return idx, None  # None = failure, do NOT cache

        results: dict[int, str] = dict(cached)
        with ThreadPoolExecutor(max_workers=min(inner_workers, len(to_translate))) as ex:
            futures = {ex.submit(_translate_one, i, s): i for i, s in to_translate.items()}
            for future in as_completed(futures):
                idx, translated = future.result()
                if translated is not None:
                    results[idx] = translated
                    new_translations[hashes[idx]] = translated  # cache success only
                else:
                    results[idx] = sections[idx]  # fallback to EN, but NOT cached
                    failed_indices.add(idx)

        if new_translations:
            _cache_put(cache_key, locale, new_translations)
        if failed_indices:
            tprint(f"  [{locale}] {src_path.name}: {len(failed_indices)} section(s) kept EN (not cached — will retry next run)")
    else:
        tprint(f"  [{locale}] {src_path.name}: all {total} sections cached")
        results = dict(cached)

    return "".join(results[i] for i in range(total))


# ---------------------------------------------------------------------------
# MDX translation
# ---------------------------------------------------------------------------

_MDX_SYSTEM_PROMPT = """\
You are a professional technical documentation translator. \
Translate the following MDX documentation section from English to {locale}.

STRICT rules — violating any of these will break the documentation site:
1. NEVER translate or modify content inside code blocks (``` ... ``` or ` ... `).
2. NEVER translate import/export statements (lines starting with import or export).
3. NEVER translate JSX component names, prop keys, or prop values that are paths or identifiers.
4. NEVER translate variable placeholders like {{name}}, {{count}}, {{0}}.
5. NEVER translate these technical terms: API, MCP, DAG, FIM One, OAuth, SSO, JWT, SSE, \
   FastAPI, SQLAlchemy, Pydantic, Alembic, LLM, RAG, ReAct, JSON, HTTP, HTTPS, URL, UUID, \
   Python, TypeScript, JavaScript, Node.js, npm, pnpm, uv, Docker, PostgreSQL, SQLite.
6. Translate domain terms naturally: "agent" → localised equivalent (e.g. 智能体 in zh), \
   "connector" → localised equivalent (e.g. 连接器 in zh). Do NOT keep them in English.
7. For frontmatter (--- ... ---): translate ONLY the VALUES of title, description, \
   and sidebarTitle. Leave all other frontmatter keys and values untouched.
8. Preserve ALL blank lines, heading levels, list markers, and MDX structure exactly.
9. Return ONLY the translated content — no extra commentary, no markdown fences wrapping the output.
"""


def translate_mdx_file(src_path: Path, locale: str, config: dict[str, str], force: bool = False) -> Path | None:
    try:
        rel = src_path.relative_to(ROOT / "docs")
    except ValueError:
        tprint(f"  [{locale}] {src_path.name}: ERROR — not under docs/")
        return None

    target_path = ROOT / "docs" / locale / rel

    try:
        content = src_path.read_text(encoding="utf-8")
    except Exception as exc:
        tprint(f"  [{locale}] {rel}: ERROR reading source — {exc}")
        return None

    system = _MDX_SYSTEM_PROMPT.format(locale=locale)
    translated_content = _translate_sections(src_path, content, locale, config, system, cache_key=f"docs/{rel}", force=force)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(translated_content, encoding="utf-8")
    tprint(f"  [{locale}] {rel}: saved")
    return target_path


# ---------------------------------------------------------------------------
# README translation
# ---------------------------------------------------------------------------

_README_SYSTEM_PROMPT = """\
You are a professional technical writer and translator. \
Translate the following GitHub README section from English to {locale}.

STRICT rules:
1. NEVER translate or modify content inside code blocks (``` ... ``` or ` ... `).
2. NEVER translate shell commands, file paths, or environment variable names.
3. NEVER translate these technical terms: API, MCP, DAG, FIM One, OAuth, SSO, JWT, SSE, \
   FastAPI, SQLAlchemy, Pydantic, Alembic, LLM, RAG, ReAct, JSON, HTTP, HTTPS, URL, UUID, \
   Python, TypeScript, JavaScript, Node.js, npm, pnpm, uv, Docker, PostgreSQL, SQLite.
4. Translate domain terms naturally: "agent" → localised equivalent (e.g. 智能体 in zh), \
   "connector" → localised equivalent (e.g. 连接器 in zh). Do NOT keep them in English.
5. NEVER translate badge markdown ([![...](...)]) or any markdown image/link URLs.
6. NEVER translate HTML tags or attributes.
7. Preserve ALL markdown structure: headings, lists, tables, bold, italic, links.
8. Translate heading text, paragraph text, table cell text, and list item text.
9. Return ONLY the translated content — no extra commentary.
"""


def translate_readme_file(src_path: Path, locale: str, config: dict[str, str], force: bool = False) -> Path | None:
    target_path = src_path.parent / f"README.{locale}.md"

    try:
        content = src_path.read_text(encoding="utf-8")
    except Exception as exc:
        tprint(f"  [{locale}] README.md: ERROR reading source — {exc}")
        return None

    system = _README_SYSTEM_PROMPT.format(locale=locale)
    translated = _translate_sections(src_path, content, locale, config, system, cache_key="README.md", force=force)

    target_path.write_text(translated, encoding="utf-8")
    tprint(f"  [{locale}] README.md: saved → {target_path.name}")
    return target_path


# ---------------------------------------------------------------------------
# Source file enumeration helpers
# ---------------------------------------------------------------------------

def all_en_json_files() -> list[Path]:
    en_dir = ROOT / "frontend" / "messages" / "en"
    return sorted(en_dir.glob("*.json")) if en_dir.exists() else []


def all_en_mdx_files() -> list[Path]:
    docs_dir = ROOT / "docs"
    if not docs_dir.exists():
        return []
    locale_dirs = set(discover_locales()) | {"en"}
    results: list[Path] = []
    for mdx in docs_dir.rglob("*.mdx"):
        try:
            rel = mdx.relative_to(docs_dir)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] in locale_dirs:
            continue
        results.append(mdx)
    return sorted(results)


def resolve_explicit_files(file_args: list[str]) -> list[Path]:
    results: list[Path] = []
    for f in file_args:
        p = Path(f)
        if not p.is_absolute():
            p = ROOT / p
        if p.exists():
            results.append(p)
        else:
            tprint(f"  WARNING: file not found, skipping: {f}")
    return results


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Translate EN i18n JSON, docs MDX, and README to all target locales.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--all", action="store_true", help="Retranslate ALL EN source files.")
    mode_group.add_argument("--files", nargs="+", metavar="FILE", help="Translate specific files only.")
    parser.add_argument("--locale", nargs="+", metavar="LOCALE", help="Override target locale(s).")
    parser.add_argument("--force", action="store_true", help="Force retranslation (JSON: all keys; MDX/README: ignore cache).")
    parser.add_argument("--concurrency", type=int, default=3, help="Max concurrent API calls to the LLM (default: 3).")
    parser.add_argument("--batch-size", type=int, default=4000, help="Max chars per JSON batch sent to LLM (default: 4000, lower if output truncates).")
    args = parser.parse_args()

    global _api_semaphore
    _api_semaphore = threading.Semaphore(args.concurrency)

    target_locales = args.locale or discover_locales()
    if not target_locales:
        print("No target locales found. Create a subdirectory under frontend/messages/ to add one.")
        sys.exit(0)

    config = load_llm_config()
    if not config["api_key"]:
        print("ERROR: No API key found. Set FAST_LLM_API_KEY or LLM_API_KEY in .env or environment.")
        sys.exit(1)

    print(f"Translating to: {', '.join(target_locales)} via {config['model']} (concurrency={args.concurrency})")

    readme_src = ROOT / "README.md"

    if args.all:
        json_files = all_en_json_files()
        mdx_files = all_en_mdx_files()
        readme_files = [readme_src] if readme_src.exists() else []
        force = True
    elif args.files:
        explicit = resolve_explicit_files(args.files)
        json_files = [p for p in explicit if p.suffix == ".json"]
        mdx_files = [p for p in explicit if p.suffix == ".mdx"]
        readme_files = [p for p in explicit if p.name == "README.md"]
        force = args.force
    else:
        staged = get_staged_en_files()
        json_files = [p for p in staged if p.suffix == ".json"]
        mdx_files = [p for p in staged if p.suffix == ".mdx"]
        readme_files = [p for p in staged if p.name == "README.md"]
        force = args.force

    if not json_files and not mdx_files and not readme_files:
        print("No EN source files to translate.")
        sys.exit(0)

    print(f"Files: {len(json_files)} JSON, {len(mdx_files)} MDX, {len(readme_files)} README  ×  {len(target_locales)} locale(s)")

    # Build task list: each (locale, file) pair is an independent task
    Task = tuple[str, str, Path]  # (type, locale, src_path)
    tasks: list[Task] = []
    for locale in target_locales:
        for src in json_files:
            tasks.append(("json", locale, src))
        for src in mdx_files:
            tasks.append(("mdx", locale, src))
        for src in readme_files:
            tasks.append(("readme", locale, src))

    output_files: list[Path] = []

    with ThreadPoolExecutor(max_workers=50) as executor:  # high ceiling; actual limit is _api_semaphore
        future_map = {}
        for task_type, locale, src in tasks:
            if task_type == "json":
                f = executor.submit(translate_json_file, src, locale, config, force, args.batch_size)
            elif task_type == "mdx":
                f = executor.submit(translate_mdx_file, src, locale, config, force)
            else:
                f = executor.submit(translate_readme_file, src, locale, config, force)
            future_map[f] = (task_type, locale, src)

        for future in as_completed(future_map):
            task_type, locale, src = future_map[future]
            try:
                out = future.result()
                if out:
                    output_files.append(out)
            except Exception as exc:
                tprint(f"  [{locale}] {src.name}: UNEXPECTED ERROR — {exc}")

    # Flush cache to disk once at the end
    _flush_cache()

    # Git hook mode: stage all output files
    git_hook = os.environ.get("GIT_HOOK") == "1"
    if git_hook and output_files:
        try:
            subprocess.run(["git", "add", "--"] + [str(p) for p in output_files], check=True, cwd=ROOT)
            print(f"Staged {len(output_files)} translated file(s)")
        except subprocess.CalledProcessError as exc:
            print(f"WARNING: git add failed: {exc}")
    elif output_files:
        print(f"\nDone. Wrote {len(output_files)} translated file(s).")


if __name__ == "__main__":
    main()
