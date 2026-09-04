#!/usr/bin/env python3
"""Markdown link gate for ``docs/`` and ``dev/``.

Rejects two failure modes that otherwise surface only after a rename:

- a relative or site-absolute link whose target file does not exist
- a ``#fragment`` that matches no heading in the target file

Link forms understood:

- ``[text](/quickstart)`` / ``href="/quickstart"`` — Mintlify root-absolute
  links, resolved against ``docs/`` (``.mdx`` implied). A locale copy under
  ``docs/<locale>/`` may also resolve against its own directory.
- ``[text](audit.md#section)`` — relative links, resolved against the
  linking file's directory (``.md`` / ``.mdx`` implied when missing).
- ``[text](#section)`` — same-file anchors.
- ``src="/images/x.png"`` — static assets under ``docs/``.

Fragments are matched against GitHub/Mintlify-style heading slugs plus any
explicit ``id="..."`` / ``{#id}`` anchors. Headings inside fenced code blocks
are ignored, and so are links inside code fences or inline code.

Fragment checks are skipped for files under a locale directory: their
headings are translated, so the slugs differ from the EN source by design.
Target existence is still checked there.

Usage::

    python3 scripts/check_md_links.py            # docs/ + dev/ (if present)
    python3 scripts/check_md_links.py docs dev/foo.md

Exit status is non-zero when any link is broken.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DEV = ROOT / "dev"

DEFAULT_LOCALES = ("zh", "ja", "ko", "de", "fr")

MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HTML_ATTR_RE = re.compile(r"""(?:href|src)=["']([^"']+)["']""")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
JSX_COMMENT_RE = re.compile(r"\{/\*.*?\*/\}")
EXPLICIT_ID_RE = re.compile(r"""\bid=["']([^"']+)["']""")
HEADING_ID_SUFFIX_RE = re.compile(r"\s*\{#([^}]+)\}\s*$")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "tel:", "{", "data:")


def locales() -> tuple[str, ...]:
    messages = ROOT / "frontend" / "messages"
    if messages.is_dir():
        found = tuple(
            sorted(p.name for p in messages.iterdir() if p.is_dir() and p.name != "en")
        )
        if found:
            return found
    return DEFAULT_LOCALES


LOCALES = locales()


def slugify(text: str) -> str:
    """GitHub-style heading slug; Mintlify produces the same for plain text."""
    text = JSX_COMMENT_RE.sub("", text)
    text = HEADING_ID_SUFFIX_RE.sub("", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text)


def iter_lines_outside_fences(text: str):
    """Yield ``(lineno, line)`` for lines not inside a fenced code block."""
    in_fence = False
    fence_marker = ""
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = FENCE_RE.match(line)
        if m:
            marker = m.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence = False
            continue
        if not in_fence:
            yield lineno, line


def anchors_of(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    if path in cache:
        return cache[path]
    found: set[str] = set()
    seen: dict[str, int] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        cache[path] = found
        return found
    for _, line in iter_lines_outside_fences(text):
        m = HEADING_RE.match(line)
        if m:
            raw = m.group(2)
            suffix = HEADING_ID_SUFFIX_RE.search(raw)
            if suffix:
                found.add(suffix.group(1))
            slug = slugify(raw)
            n = seen.get(slug, 0)
            seen[slug] = n + 1
            found.add(slug if n == 0 else f"{slug}-{n}")
            # Tolerate slug variants that collapse repeated hyphens.
            found.add(re.sub(r"-{2,}", "-", slug))
        for explicit in EXPLICIT_ID_RE.findall(line):
            found.add(explicit)
    cache[path] = found
    return found


def locale_of(path: Path) -> str | None:
    try:
        rel = path.relative_to(DOCS)
    except ValueError:
        return None
    return rel.parts[0] if rel.parts and rel.parts[0] in LOCALES else None


def resolve(source: Path, target: str) -> Path | None:
    """Return the existing file a link points at, or ``None``."""
    if target.startswith("/"):
        stripped = target.lstrip("/")
        bases = [DOCS]
        loc = locale_of(source)
        if loc:
            bases.append(DOCS / loc)
        for base in bases:
            if not stripped:
                candidates = [base / "index.mdx"]
            else:
                candidates = [
                    base / f"{stripped}.mdx",
                    base / f"{stripped}.md",
                    base / stripped,
                    base / stripped / "index.mdx",
                ]
            for c in candidates:
                if c.is_file():
                    return c
        return None
    base = source.parent
    candidates = [base / target, base / f"{target}.md", base / f"{target}.mdx"]
    for c in candidates:
        if c.is_file():
            return c
    return None


def check_file(path: Path, cache: dict[Path, set[str]]) -> list[str]:
    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: unreadable ({exc})"]
    rel = path.relative_to(ROOT)
    skip_fragments = locale_of(path) is not None

    for lineno, line in iter_lines_outside_fences(text):
        scan = INLINE_CODE_RE.sub("", line)
        targets = MD_LINK_RE.findall(scan) + HTML_ATTR_RE.findall(scan)
        for raw in targets:
            if raw.startswith(SKIP_PREFIXES) or raw.startswith("//"):
                continue
            target, _, fragment = raw.partition("#")
            if target == "" and fragment == "":
                continue
            if target:
                resolved = resolve(path, target)
                if resolved is None:
                    problems.append(f"{rel}:{lineno}: missing target '{raw}'")
                    continue
            else:
                resolved = path
            if fragment and not skip_fragments and resolved.suffix in (".md", ".mdx"):
                if fragment not in anchors_of(resolved, cache):
                    problems.append(
                        f"{rel}:{lineno}: no heading '#{fragment}' in "
                        f"{resolved.relative_to(ROOT)}"
                    )
    return problems


def collect(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(q for q in p.rglob("*") if q.suffix in (".md", ".mdx")))
        elif p.is_file():
            files.append(p)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("paths", nargs="*", help="files or directories (default: docs/ and dev/)")
    parser.add_argument("-q", "--quiet", action="store_true", help="print only the problems")
    args = parser.parse_args(argv)

    if args.paths:
        roots = [Path(p).resolve() for p in args.paths]
    else:
        roots = [d for d in (DOCS, DEV) if d.is_dir()]

    files = collect(roots)
    cache: dict[Path, set[str]] = {}
    problems: list[str] = []
    for f in files:
        problems.extend(check_file(f, cache))

    for line in problems:
        print(line)
    if problems:
        print(f"\n{len(problems)} broken link(s) across {len(files)} file(s)", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"md-links: {len(files)} file(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
