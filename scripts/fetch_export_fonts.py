#!/usr/bin/env python3
"""Download the CJK font used to embed text in PDF exports.

ReportLab can only embed TrueType outlines, which rules out the OpenType/CFF
CJK families most systems ship (Noto Sans CJK ``.otc``, PingFang, Hiragino).
Google Fonts serves a TrueType-flavoured build of Noto Sans SC, so that is what
this script fetches.

Usage::

    python scripts/fetch_export_fonts.py [target-dir]

The default target is ``assets/fonts/`` next to the repository root, which
``fim_one.web.export_fonts`` searches automatically.  The Dockerfile points it
at ``/usr/share/fonts/truetype/fim-one`` instead.

A failed download is not fatal: the exporter falls back to a system font and,
failing that, to ReportLab's non-embedded CID font.  Re-run this script to fix
export typography without rebuilding anything else.
"""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Pinned Google Fonts payloads (Noto Sans SC v40, TrueType flavour). Fetched
# with a legacy user agent — the modern one is served woff2, which ReportLab
# cannot read.
_FONTS: tuple[tuple[str, str], ...] = (
    (
        "NotoSansSC-Regular.ttf",
        "https://fonts.gstatic.com/s/notosanssc/v40/"
        "k3kCo84MPvpLmixcA63oeAL7Iqp5IZJF9bmaG9_FnYw.ttf",
    ),
    (
        "NotoSansSC-Bold.ttf",
        "https://fonts.gstatic.com/s/notosanssc/v40/"
        "k3kCo84MPvpLmixcA63oeAL7Iqp5IZJF9bmaGzjCnYw.ttf",
    ),
)

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_RETRIES = 3
_MIN_BYTES = 1_000_000  # a truncated response is not a usable font


def _default_target() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "fonts"


def _download(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        payload = response.read()
    if len(payload) < _MIN_BYTES:
        raise OSError(f"{url} returned only {len(payload)} bytes")
    dest.write_bytes(payload)


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else _default_target()
    target.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    for name, url in _FONTS:
        dest = target / name
        if dest.is_file() and dest.stat().st_size >= _MIN_BYTES:
            print(f"[fonts] {name} already present, skipping")
            continue
        for attempt in range(1, _RETRIES + 1):
            try:
                _download(url, dest)
                print(f"[fonts] {name} -> {dest} ({dest.stat().st_size // 1024} KiB)")
                break
            except (OSError, urllib.error.URLError) as exc:
                if attempt == _RETRIES:
                    failures.append(f"{name}: {exc}")
                else:
                    time.sleep(2 * attempt)

    if failures:
        print(
            "[fonts] WARNING: could not download the PDF export font:\n  "
            + "\n  ".join(failures)
            + "\n[fonts] PDF export will fall back to a system font, or to a "
            "non-embedded CID font if none is installed.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
