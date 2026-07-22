#!/usr/bin/env python3
"""Generate star-history SVGs for README (light + dark, GitHub themes).

Self-owned replacement for api.star-history.com embeds, which break after
GitHub's Jul 2026 stargazer restriction (server-side token pools get rate
limited; their /chart hard-timeouts at 10s).

Writes two files for <picture prefers-color-scheme> embeds:
  assets/star-history.svg       (light — default <img> fallback)
  assets/star-history-dark.svg  (dark)

Auth (first match wins):
  1. GITHUB_TOKEN / GH_TOKEN env (GitHub Actions, or a fine-grained PAT)
  2. `gh` CLI (local dev)

Usage:
    python3 scripts/gen_star_history.py
    GITHUB_TOKEN=ghp_xxx python3 scripts/gen_star_history.py --repo owner/name
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TypedDict


class Theme(TypedDict):
    line: str
    fill: str
    grid: str
    text: str
    title: str
    bg: str


# GitHub Primer-ish palette so charts match the site chrome in each mode.
THEMES: dict[str, Theme] = {
    "light": {
        "line": "#0969da",
        "fill": "#0969da33",
        "grid": "#d0d7de",
        "text": "#656d76",
        "title": "#1f2328",
        "bg": "#ffffff",
    },
    "dark": {
        "line": "#4493f8",
        "fill": "#4493f833",
        "grid": "#30363d",
        "text": "#8b949e",
        "title": "#e6edf3",
        "bg": "#0d1117",
    },
}


def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None


def fetch_star_times_http(repo: str, token: str) -> list[datetime]:
    """Page stargazers via REST with Accept: application/vnd.github.star+json."""
    times: list[datetime] = []
    page = 1
    per_page = 100
    while True:
        url = (
            f"https://api.github.com/repos/{repo}/stargazers"
            f"?per_page={per_page}&page={page}"
        )
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github.star+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "fim-one-star-history",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
                link = resp.headers.get("Link", "")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            raise SystemExit(
                f"GitHub API {e.code} for {repo} page={page}: {detail}"
            ) from e

        rows = json.loads(body)
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            starred = row.get("starred_at")
            if starred:
                times.append(
                    datetime.fromisoformat(starred.replace("Z", "+00:00"))
                )
        if 'rel="next"' not in link:
            break
        page += 1
        if page > 500:  # safety
            break
    return times


def fetch_star_times_gh(repo: str) -> list[datetime]:
    out = subprocess.check_output(
        [
            "gh",
            "api",
            "--paginate",
            "-H",
            "Accept: application/vnd.github.star+json",
            f"repos/{repo}/stargazers?per_page=100",
            "--jq",
            ".[].starred_at",
        ]
    )
    times: list[datetime] = []
    for line in out.decode().splitlines():
        line = line.strip()
        if not line:
            continue
        times.append(datetime.fromisoformat(line.replace("Z", "+00:00")))
    return times


def fetch_star_times(repo: str) -> list[datetime]:
    token = _token()
    if token:
        times = fetch_star_times_http(repo, token)
    else:
        times = fetch_star_times_gh(repo)
    times.sort()
    if not times:
        raise SystemExit(f"no stargazers returned for {repo}")
    return times


def build_series(times: list[datetime]) -> tuple[list[date], list[int]]:
    by_day = Counter(t.date() for t in times)
    start, end = times[0].date(), times[-1].date()
    days: list[date] = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    cum: list[int] = []
    running = 0
    for d in days:
        running += by_day.get(d, 0)
        cum.append(running)
    return days, cum


def downsample(
    days: list[date], cum: list[int], max_pts: int = 200
) -> tuple[list[date], list[int]]:
    if len(days) <= max_pts:
        return days, cum
    step = math.ceil(len(days) / max_pts)
    idxs = list(range(0, len(days), step))
    if idxs[-1] != len(days) - 1:
        idxs.append(len(days) - 1)
    return [days[i] for i in idxs], [cum[i] for i in idxs]


def render_svg(
    repo: str,
    xs: list[date],
    ys: list[int],
    theme: Theme,
) -> str:
    w, h = 900, 360
    ml, mr, mt, mb = 56, 24, 36, 48
    pw, ph = w - ml - mr, h - mt - mb
    ymax = max(ys) or 1
    end = xs[-1]
    n = ys[-1]

    def x_at(i: int) -> float:
        if len(xs) == 1:
            return ml + pw / 2
        return ml + i * pw / (len(xs) - 1)

    def y_at(v: int) -> float:
        return mt + ph - (v / ymax) * ph

    pts = " ".join(f"{x_at(i):.1f},{y_at(v):.1f}" for i, v in enumerate(ys))
    area = f"{ml},{mt + ph} " + pts + f" {x_at(len(ys) - 1):.1f},{mt + ph}"

    yticks = 5
    y_vals = [round(ymax * k / yticks) for k in range(yticks + 1)]
    nt = min(6, len(xs))
    xtick_idxs = (
        [round(k * (len(xs) - 1) / (nt - 1)) for k in range(nt)] if nt > 1 else [0]
    )

    line, fill, grid = theme["line"], theme["fill"], theme["grid"]
    text, title_c, bg = theme["text"], theme["title"], theme["bg"]
    font = "-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" aria-label="Star history for {repo}">',
        f'<rect width="100%" height="100%" fill="{bg}"/>',
        f'<text x="{ml}" y="22" font-family="{font}" font-size="15" '
        f'font-weight="600" fill="{title_c}">{repo} · Star History</text>',
        f'<text x="{w - mr}" y="22" text-anchor="end" font-family="{font}" '
        f'font-size="12" fill="{text}">{n:,} stars · as of {end.isoformat()}</text>',
    ]
    for v in y_vals:
        y = y_at(v)
        parts.append(
            f'<line x1="{ml}" y1="{y:.1f}" x2="{w - mr}" y2="{y:.1f}" '
            f'stroke="{grid}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{ml - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-family="{font}" font-size="11" fill="{text}">{v:,}</text>'
        )
    for i in xtick_idxs:
        x = x_at(i)
        label = xs[i].strftime("%b %Y")
        parts.append(
            f'<line x1="{x:.1f}" y1="{mt + ph}" x2="{x:.1f}" y2="{mt + ph + 4}" '
            f'stroke="{grid}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{h - 18}" text-anchor="middle" '
            f'font-family="{font}" font-size="11" fill="{text}">{label}</text>'
        )
    parts.append(f'<polyline fill="{fill}" stroke="none" points="{area}"/>')
    parts.append(
        f'<polyline fill="none" stroke="{line}" stroke-width="2.5" '
        f'stroke-linejoin="round" stroke-linecap="round" points="{pts}"/>'
    )
    parts.append(
        f'<circle cx="{x_at(len(ys) - 1):.1f}" cy="{y_at(ys[-1]):.1f}" '
        f'r="4" fill="{line}"/>'
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="fim-ai/fim-one")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("assets"),
        help="Directory for star-history.svg and star-history-dark.svg",
    )
    parser.add_argument(
        "--theme",
        choices=(*THEMES.keys(), "all"),
        default="all",
        help="Which theme(s) to write (default: all)",
    )
    args = parser.parse_args()

    times = fetch_star_times(args.repo)
    days, cum = build_series(times)
    xs, ys = downsample(days, cum)

    # light → star-history.svg (default fallback); dark → star-history-dark.svg
    out_names = {
        "light": "star-history.svg",
        "dark": "star-history-dark.svg",
    }
    themes = THEMES.keys() if args.theme == "all" else (args.theme,)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name in themes:
        path = args.out_dir / out_names[name]
        svg = render_svg(args.repo, xs, ys, THEMES[name])
        path.write_text(svg)
        print(f"wrote {path} ({path.stat().st_size} bytes, {ys[-1]} stars, {name})")


if __name__ == "__main__":
    main()
