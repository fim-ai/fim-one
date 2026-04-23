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
from typing import Any, Callable

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
    """Split markdown/MDX into sections at each heading line.

    Code-fence-aware: lines starting with '#' inside fenced code blocks
    (``` ... ```) are NOT treated as section boundaries. This prevents
    bash comments like '# Configure' from fragmenting code blocks and
    causing the LLM to mangle their structure.
    """
    sections: list[str] = []
    current: list[str] = []
    in_code_fence = False
    for line in content.splitlines(keepends=True):
        stripped = line.rstrip("\n\r")
        # Track code fence state (``` or ~~~, with optional language tag)
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_fence = not in_code_fence
        if not in_code_fence and line.startswith("#") and current:
            sections.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("".join(current))
    return sections


def _shield_code_blocks(text: str) -> tuple[str, list[str]]:
    """Extract fenced code blocks, replacing with <!--CODE_BLOCK_N--> placeholders.

    This prevents the LLM from translating or breaking code block content
    (e.g. turning bash comments like '# Configure' into markdown headings).
    """
    blocks: list[str] = []
    lines = text.splitlines(keepends=True)
    result: list[str] = []
    in_block = False
    block_lines: list[str] = []
    fence_char = ""

    for line in lines:
        stripped = line.strip()
        if not in_block:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_block = True
                fence_char = stripped[0]
                block_lines = [line]
            else:
                result.append(line)
        else:
            block_lines.append(line)
            # Closing fence: same char, at least 3, no other content
            if stripped.startswith(fence_char * 3) and stripped.rstrip(fence_char) == "":
                blocks.append("".join(block_lines))
                result.append(f"<!--CODE_BLOCK_{len(blocks) - 1}-->\n")
                in_block = False
                block_lines = []

    # Handle unclosed code block (shouldn't happen but be safe)
    if block_lines:
        blocks.append("".join(block_lines))
        result.append(f"<!--CODE_BLOCK_{len(blocks) - 1}-->\n")

    return "".join(result), blocks


def _restore_code_blocks(text: str, blocks: list[str]) -> str:
    """Re-insert original code blocks from placeholders."""
    for i, block in enumerate(blocks):
        placeholder = f"<!--CODE_BLOCK_{i}-->"
        restored = block.rstrip("\n")
        if placeholder in text:
            text = text.replace(placeholder, restored, 1)
            continue
        # Try common LLM mutations (added spaces inside HTML comment)
        for variant in [
            f"<!-- CODE_BLOCK_{i} -->",
            f"<!--CODE_BLOCK_{i} -->",
            f"<!-- CODE_BLOCK_{i}-->",
        ]:
            if variant in text:
                text = text.replace(variant, restored, 1)
                break
    return text


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

_GLOSSARY_PATH = ROOT / "scripts" / "translation-glossary.md"


def _load_glossary() -> str:
    """Load the translation glossary. Returns a prompt-injectable section.

    The glossary is the single source of truth for translation rules — terms
    that stay English, canonical translations per locale, and style rules.
    Edit translation-glossary.md to change behavior; never edit locale files
    directly.

    Braces are doubled so the returned string is safe to pass through
    `str.format(locale=...)` alongside the rest of the system prompt — the
    glossary is free to contain JSON-ish `{key}` snippets or placeholder
    examples like `{variable}` without colliding with format substitution.
    """
    try:
        content = _GLOSSARY_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    escaped = content.replace("{", "{{").replace("}", "}}")
    return (
        "\n\n---\n\n"
        "TRANSLATION GLOSSARY (authoritative — follow every rule strictly):\n\n"
        f"{escaped}\n"
    )


_GLOSSARY = _load_glossary()


_JSON_SYSTEM_PROMPT = (
    "You are a professional software localisation expert for an AI platform. "
    "Your task is to translate UI string values from English to {locale}.\n"
    "\n"
    "Structural rules (these are orthogonal to the glossary below):\n"
    "- Translate ONLY the values, never the keys.\n"
    "- Preserve all placeholder tokens literally, e.g. {{name}}, {{count}}, {{0}}.\n"
    "- Keep HTML tags intact.\n"
    "- Return ONLY a valid JSON object mapping the same keys to their translated values. "
    "No markdown fences, no extra text — pure JSON only.\n"
    + _GLOSSARY
)


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
    added = modified = stale = deleted = unchanged = 0

    for key, en_value in src_flat.items():
        en_hash = _hash(en_value)
        existing_value = existing_flat.get(key)
        if force:
            to_translate[key] = en_value
        elif key not in existing_flat:
            to_translate[key] = en_value
            added += 1
        elif stored_hashes.get(key) != en_hash:
            # EN value changed since last translation → retranslate
            to_translate[key] = en_value
            modified += 1
        elif (
            locale in ('zh', 'ja', 'ko')
            and existing_value is not None
            and len(existing_value) > 3
            and _check_untranslated(existing_value, locale)
        ):
            # Target value is still English (no CJK) despite hash match →
            # stale cache or earlier fallback; force retranslation.
            to_translate[key] = en_value
            stale += 1
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
        if stale:
            parts.append(f"!{stale} stale-en")
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

        # CJK detection: retry keys that appear untranslated (no CJK chars)
        if locale in ('zh', 'ja', 'ko'):
            untranslated_keys: dict[str, str] = {}
            for key, value in translated.items():
                # Skip very short values (≤3 chars) — may legitimately be ASCII (e.g. "OK")
                if len(value) <= 3:
                    continue
                if _check_untranslated(value, locale):
                    untranslated_keys[key] = to_translate[key]
            if untranslated_keys:
                tprint(f"  [{locale}] {filename}: {len(untranslated_keys)} key(s) appear untranslated, retrying with stronger prompt")
                stronger_system = system + (
                    f"\n\nCRITICAL: You MUST translate ALL values to {locale}. "
                    f"Do NOT keep English text. Every value must contain {locale} characters. "
                    f"For example, 'KB Assistant' should be translated to '知识库助手' in zh, "
                    f"'Knowledge Base' → '知识库', 'Settings' → '设置', 'Assistant' → '助手'."
                )
                retry_batches = _split_into_batches(list(untranslated_keys.items()))
                for rb_idx, rb in enumerate(retry_batches):
                    try:
                        response_text = llm_chat(config, stronger_system, json.dumps(rb, ensure_ascii=False))
                        retry_result = _parse_response(response_text)
                        # Only accept retried values that actually contain CJK now
                        accepted = 0
                        for rk, rv in retry_result.items():
                            if _CJK_PATTERN.search(rv):
                                translated[rk] = rv
                                accepted += 1
                        if accepted:
                            tprint(f"  [{locale}] {filename}: retry fixed {accepted}/{len(rb)} key(s)")
                    except Exception as exc:
                        tprint(f"  [{locale}] {filename}: WARNING — retry failed: {exc}")

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
# MDX structural validation — catches LLM hallucinations before they ship
# ---------------------------------------------------------------------------

def _validate_mdx_section(en: str, translated: str) -> list[str]:
    """Validate a translated MDX section against its EN source.

    Catches three classes of LLM translation errors:
    1. Broken code fences (extra/missing ```) — causes { in code to leak as JSX
    2. Hallucinated headings (extra ## sections not in EN)
    3. Bare { outside code blocks (MDX parses these as JSX expressions)

    Returns a list of issue descriptions; empty list = OK.
    """
    issues: list[str] = []

    # 1. Code fence balance: EN and translation must have the same count
    en_fences = sum(1 for line in en.splitlines() if line.startswith("```"))
    tr_fences = sum(1 for line in translated.splitlines() if line.startswith("```"))
    if en_fences != tr_fences:
        issues.append(f"fence count: EN={en_fences} vs translated={tr_fences}")

    # 2. Heading count: translation must not add extra headings
    en_headings = len(_re.findall(r"^#{1,6}\s", en, _re.MULTILINE))
    tr_headings = len(_re.findall(r"^#{1,6}\s", translated, _re.MULTILINE))
    if tr_headings > en_headings:
        issues.append(f"extra headings: EN={en_headings} vs translated={tr_headings}")

    # 3. Bare { outside code blocks must not exceed EN count
    def _count_bare_braces(text: str) -> int:
        in_code = False
        count = 0
        for line in text.splitlines():
            if line.startswith("```"):
                in_code = not in_code
            elif not in_code and "{" in line:
                stripped = line.lstrip()
                if not stripped.startswith(("import ", "export ", "<")):
                    count += 1
        return count

    en_braces = _count_bare_braces(en)
    tr_braces = _count_bare_braces(translated)
    if tr_braces > en_braces:
        issues.append(f"extra bare braces: EN={en_braces} vs translated={tr_braces}")

    return issues


# CJK character detection — catches LLM returning English verbatim for CJK locales
_CJK_PATTERN = _re.compile(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]')

def _check_untranslated(text: str, locale: str) -> bool:
    """Return True if the text appears untranslated for the given locale.

    For CJK locales (zh, ja, ko), checks whether the non-code prose contains
    any target-script characters.  If zero CJK characters are found, the LLM
    likely echoed the English source back unchanged — a common failure mode
    when sections are dense with technical terms.
    """
    if locale not in ('zh', 'ja', 'ko'):
        return False
    # Strip code blocks and inline code before checking
    stripped = _re.sub(r'```[\s\S]*?```', '', text)
    stripped = _re.sub(r'`[^`]+`', '', stripped)
    # Strip markdown table alignment rows (e.g. |---|---|)
    stripped = _re.sub(r'^\|[-:\s|]+\|$', '', stripped, flags=_re.MULTILINE)
    # If there is meaningful prose left, it should contain CJK characters
    prose = stripped.strip()
    if not prose:
        return False  # section is all code — nothing to translate
    return not _CJK_PATTERN.search(prose)


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
    validate_fn: Callable[[str, str], list[str]] | None = None,
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

        max_attempts = 3 if validate_fn else 1

        def _translate_one(idx: int, section: str) -> tuple[int, str | None]:
            # Shield code blocks: extract them before LLM sees the content,
            # so the LLM can't translate or break bash comments / fence structure.
            shielded, code_blocks = _shield_code_blocks(section)
            content_to_translate = shielded
            if code_blocks:
                content_to_translate = (
                    "IMPORTANT: <!--CODE_BLOCK_N--> placeholders represent pre-extracted "
                    "code blocks. Preserve each placeholder exactly as-is on its own line. "
                    "Do NOT translate, modify, or remove them.\n\n"
                    + shielded
                )

            for attempt in range(max_attempts):
                try:
                    # On retry after untranslated detection, prepend a stronger hint
                    prompt_for_attempt = content_to_translate
                    if attempt > 0 and locale in ('zh', 'ja', 'ko'):
                        prompt_for_attempt = (
                            f"CRITICAL: You MUST translate ALL natural-language text into {locale}. "
                            "Technical terms like MCP, JSON-RPC, SSE, API, CLI can stay in English, "
                            "but descriptions, labels, bullet text, and connecting phrases MUST be "
                            f"translated into {locale}. Do NOT return the English text unchanged.\n\n"
                            + content_to_translate
                        )
                    translated = llm_chat(config, system_prompt, prompt_for_attempt)
                    # Restore code blocks from placeholders
                    if code_blocks:
                        translated = _restore_code_blocks(translated, code_blocks)
                    # Preserve trailing whitespace from original section.
                    # LLMs often strip trailing newlines, which causes sections
                    # to be glued together (e.g. "...value).## Next Heading").
                    orig_stripped = section.rstrip()
                    trailing = section[len(orig_stripped):]
                    if trailing and not translated.endswith(trailing):
                        translated = translated.rstrip() + trailing
                    # Check if the LLM returned the English source untranslated
                    # (common with CJK locales on technical-term-dense sections)
                    if _check_untranslated(translated, locale):
                        if attempt < max_attempts - 1:
                            tprint(f"  [{locale}] {src_path.name}: section {idx} returned untranslated (attempt {attempt + 1}/{max_attempts}) — retrying")
                            continue
                        tprint(f"  [{locale}] {src_path.name}: WARNING section {idx} returned untranslated after {max_attempts} attempts — using EN fallback")
                        return idx, None
                    # Validate structural integrity if validator provided
                    if validate_fn:
                        issues = validate_fn(section, translated)
                        if issues:
                            detail = "; ".join(issues)
                            if attempt < max_attempts - 1:
                                tprint(f"  [{locale}] {src_path.name}: section {idx} validation failed (attempt {attempt + 1}/{max_attempts}): {detail} — retrying")
                                continue
                            tprint(f"  [{locale}] {src_path.name}: WARNING section {idx} failed validation after {max_attempts} attempts: {detail} — using EN fallback")
                            return idx, None
                    return idx, translated
                except Exception as exc:
                    tprint(f"  [{locale}] {src_path.name}: WARNING section {idx} failed — {exc}")
                    return idx, None  # None = failure, do NOT cache
            return idx, None  # unreachable, but satisfies type checker

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

_MDX_SYSTEM_PROMPT = (
    "You are a professional technical documentation translator for an AI platform. "
    "Translate the following MDX documentation section from English to {locale}.\n"
    "\n"
    "Structural rules (these are orthogonal to the glossary below — violating any "
    "of these will break the documentation site):\n"
    "1. NEVER translate or modify content inside code blocks (``` ... ``` or ` ... `).\n"
    "2. NEVER translate import/export statements (lines starting with import or export).\n"
    "3. NEVER translate JSX component names, prop keys, or prop values that are paths or identifiers.\n"
    "4. NEVER translate variable placeholders like {{name}}, {{count}}, {{0}}.\n"
    "5. For frontmatter (--- ... ---): translate ONLY the VALUES of title, description, "
    "and sidebarTitle. Leave all other frontmatter keys and values untouched.\n"
    "6. Preserve ALL blank lines, heading levels, list markers, and MDX structure exactly.\n"
    "7. Return ONLY the translated content — no extra commentary, no markdown fences "
    "wrapping the output.\n"
    + _GLOSSARY
)


# MDX files that cause Mintlify build failures when translated.
# These are kept as manually maintained placeholders linking to the EN version.
# Format: set of relative paths under docs/ (e.g. "configuration/model-management.mdx")
_MDX_TRANSLATE_SKIP: set[str] = set()


def translate_mdx_file(src_path: Path, locale: str, config: dict[str, str], force: bool = False) -> Path | None:
    try:
        rel = src_path.relative_to(ROOT / "docs")
    except ValueError:
        tprint(f"  [{locale}] {src_path.name}: ERROR — not under docs/")
        return None

    if str(rel) in _MDX_TRANSLATE_SKIP:
        tprint(f"  [{locale}] {rel}: skipped (in _MDX_TRANSLATE_SKIP)")
        return None

    target_path = ROOT / "docs" / locale / rel

    try:
        content = src_path.read_text(encoding="utf-8")
    except Exception as exc:
        tprint(f"  [{locale}] {rel}: ERROR reading source — {exc}")
        return None

    system = _MDX_SYSTEM_PROMPT.format(locale=locale)
    translated_content = _translate_sections(
        src_path, content, locale, config, system,
        cache_key=f"docs/{rel}", force=force,
        validate_fn=_validate_mdx_section,
    )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(translated_content, encoding="utf-8")
    tprint(f"  [{locale}] {rel}: saved")
    return target_path


# ---------------------------------------------------------------------------
# README translation
# ---------------------------------------------------------------------------

_README_SYSTEM_PROMPT = (
    "You are a professional technical writer and translator for an AI platform. "
    "Translate the following GitHub README section from English to {locale}.\n"
    "\n"
    "Structural rules (orthogonal to the glossary below):\n"
    "1. NEVER translate or modify content inside code blocks (``` ... ``` or ` ... `).\n"
    "2. NEVER translate shell commands, file paths, or environment variable names.\n"
    "3. NEVER translate badge markdown ([![...](...)]) or any markdown image/link URLs.\n"
    "4. NEVER translate HTML tags or attributes.\n"
    "5. Preserve ALL markdown structure: headings, lists, tables, bold, italic, links.\n"
    "6. Translate heading text, paragraph text, table cell text, and list item text.\n"
    "7. Return ONLY the translated content — no extra commentary.\n"
    + _GLOSSARY
)


def translate_readme_file(src_path: Path, locale: str, config: dict[str, str], force: bool = False) -> Path | None:
    target_path = src_path.parent / f"README.{locale}.md"

    try:
        content = src_path.read_text(encoding="utf-8")
    except Exception as exc:
        tprint(f"  [{locale}] README.md: ERROR reading source — {exc}")
        return None

    system = _README_SYSTEM_PROMPT.format(locale=locale)
    translated = _translate_sections(
        src_path, content, locale, config, system,
        cache_key="README.md", force=force,
        validate_fn=_validate_mdx_section,
    )

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


import re as _re

_ENDPOINT_RE = _re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/")


def _is_openapi_only_group(group: dict) -> bool:
    """Return True if this group is auto-generated from an openapi spec.

    Detects both:
    - Group-level openapi config: {"group": "...", "openapi": {"source": "..."}}
    - Page-level openapi entries: dicts or "METHOD /path" strings
    """
    # Group-level openapi auto-generation
    if "openapi" in group:
        return True
    pages = group.get("pages", [])
    if not pages:
        return True  # Empty group = nothing translatable
    return all(
        isinstance(p, dict) or (isinstance(p, str) and _ENDPOINT_RE.match(p))
        for p in pages
    )


def sync_docs_navigation() -> bool:
    """Sync docs.json navigation: ensure every locale mirrors the EN page structure.

    - Groups where ALL pages are openapi dicts (e.g. Chat, Conversations) are
      skipped for locale blocks — Mintlify auto-generates those only for the
      default language.
    - Groups containing string pages (e.g. Getting Started with api/overview)
      are kept and string pages get locale-prefixed.
    - Tabs are included for locales if they have at least one non-openapi group
      after filtering. Tabs with zero eligible groups are skipped entirely.

    Returns True if docs.json was modified.
    """
    docs_json_path = ROOT / "docs" / "docs.json"
    if not docs_json_path.exists():
        return False

    try:
        data = json.loads(docs_json_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    languages = data.get("navigation", {}).get("languages", [])
    if not languages:
        return False

    # Find the EN language block (source of truth)
    en_block = None
    locale_blocks: list[tuple[str, dict]] = []
    for lang in languages:
        lang_code = lang.get("language", "")
        if lang_code == "en":
            en_block = lang
        else:
            locale_blocks.append((lang_code, lang))

    if en_block is None or not locale_blocks:
        return False

    modified = False
    en_tabs = en_block.get("tabs", [])

    # Build filtered EN tabs: each entry is (en_tab, eligible_groups)
    # where eligible_groups excludes openapi-only groups.
    # Tabs with zero eligible groups are dropped entirely.
    en_filtered: list[tuple[dict, list[dict]]] = []
    for en_tab in en_tabs:
        eligible = [g for g in en_tab.get("groups", []) if not _is_openapi_only_group(g)]
        if eligible:
            en_filtered.append((en_tab, eligible))

    for locale, locale_lang in locale_blocks:
        locale_tabs = locale_lang.get("tabs", [])

        # ── Remove any locale group that is openapi-only (stale from prior sync) ──
        for lt in locale_tabs:
            before = len(lt.get("groups", []))
            lt["groups"] = [g for g in lt.get("groups", []) if not _is_openapi_only_group(g)]
            if len(lt["groups"]) != before:
                modified = True

        # Remove empty tabs (all groups were openapi-only)
        cleaned = [t for t in locale_tabs if t.get("groups")]
        if len(cleaned) != len(locale_tabs):
            locale_tabs = cleaned
            modified = True

        # ── Structural alignment: match locale tab count to filtered EN tabs ──
        expected_count = len(en_filtered)

        while len(locale_tabs) < expected_count:
            en_tab, _ = en_filtered[len(locale_tabs)]
            locale_tabs.append({"tab": en_tab.get("tab", ""), "groups": []})
            tprint(f"  [docs.json] {locale}: adding missing tab '{en_tab.get('tab', '')}'")
            modified = True

        if len(locale_tabs) > expected_count:
            locale_tabs[:] = locale_tabs[:expected_count]
            tprint(f"  [docs.json] {locale}: trimmed extra tabs")
            modified = True

        locale_lang["tabs"] = locale_tabs

        # ── Sync each filtered EN tab with its locale counterpart ──
        for lt_idx, (en_tab, eligible_groups) in enumerate(en_filtered):
            locale_groups = locale_tabs[lt_idx].get("groups", [])

            # Structural alignment: ensure same number of groups
            while len(locale_groups) < len(eligible_groups):
                en_group = eligible_groups[len(locale_groups)]
                new_group: dict[str, Any] = {
                    "group": en_group.get("group", ""),
                    "pages": [],
                }
                if "icon" in en_group:
                    new_group["icon"] = en_group["icon"]
                locale_groups.append(new_group)
                tprint(
                    f"  [docs.json] {locale}: adding missing group "
                    f"'{en_group.get('group', '')}' in tab '{locale_tabs[lt_idx].get('tab', '')}'"
                )
                modified = True

            if len(locale_groups) > len(eligible_groups):
                locale_groups[:] = locale_groups[: len(eligible_groups)]
                tprint(
                    f"  [docs.json] {locale}: trimmed extra groups in "
                    f"tab '{locale_tabs[lt_idx].get('tab', '')}'"
                )
                modified = True

            locale_tabs[lt_idx]["groups"] = locale_groups

            # Page alignment within each group (only string pages)
            for g_idx, en_group in enumerate(eligible_groups):
                en_pages = [p for p in en_group.get("pages", []) if isinstance(p, str)]
                locale_pages = locale_groups[g_idx].get("pages", [])

                new_pages = [f"{locale}/{p}" for p in en_pages]

                if new_pages != locale_pages:
                    old_strs = set(locale_pages) if all(isinstance(p, str) for p in locale_pages) else set()
                    new_strs = set(new_pages)
                    for p in sorted(new_strs - old_strs):
                        tprint(f"  [docs.json] {locale}: adding missing page '{p}'")
                    for p in sorted(old_strs - new_strs):
                        tprint(f"  [docs.json] {locale}: removing stale page '{p}'")
                    locale_groups[g_idx]["pages"] = new_pages
                    modified = True

    if modified:
        docs_json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        tprint("  [docs.json] navigation synced")

    return modified


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

    # Git hook mode flag (must be set before first use)
    git_hook = os.environ.get("GIT_HOOK") == "1"

    # Sync docs.json navigation (ensure all locales mirror EN page list)
    docs_json_path = ROOT / "docs" / "docs.json"
    nav_modified = sync_docs_navigation()
    if nav_modified and git_hook:
        try:
            subprocess.run(["git", "add", "--", str(docs_json_path)], check=True, cwd=ROOT)
            tprint("  [docs.json] staged")
        except subprocess.CalledProcessError:
            pass

    # Flush cache to disk once at the end
    _flush_cache()

    # Git hook mode: stage all output files
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
