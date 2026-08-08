#!/usr/bin/env python3
"""Render the star history chart as a self-contained SVG.

Reads the daily snapshots collected by .github/workflows/traffic.yml and
draws stars-over-time. Replaces the star-history.com embed, which stopped
working when GitHub restricted the stargazers API to repo admins on
2026-06-30 — the data has been collected locally since February anyway.

Standard library only: the workflow runner must not need a pip install.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

DATA_FILE = Path(".github/traffic/traffic.jsonl")
STARS_FILE = Path(".github/traffic/stars-daily.json")
OUTPUT_FILE = Path(".github/traffic/star-history.svg")

WIDTH = 800
HEIGHT = 320
MARGIN_LEFT = 55
MARGIN_RIGHT = 24
MARGIN_TOP = 44
MARGIN_BOTTOM = 42

# Mid-tones that stay legible on both the light and the dark README
# background. Deliberately not prefers-color-scheme: the media query
# follows the reader's OS, not the GitHub theme, so anyone running a dark
# desktop with GitHub forced to light would get pale text on white.
COLORS = {
    "line": "#2f81f7",
    "area": "#2f81f7",
    "grid": "#8b949e",
    "axis": "#8b949e",
    "text": "#6e7781",
    "title": "#6e7781",
}

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Presentation attributes rather than a <style> block: image sanitizers
# strip embedded CSS, which would leave every element at its black
# default. Attributes survive that.
FONT = ("font-family=\"system-ui, -apple-system, Segoe UI, "
        "Helvetica, Arial, sans-serif\"")


def read_snapshots(path: Path) -> list[dict]:
    """Parse the snapshot file.

    Mixed formats by history: entries written before 2026-08 span multiple
    lines (jq without -c), newer ones are a single line each. Decoding the
    objects one after another handles both — reading it line by line would
    choke on the older half.
    """
    text = path.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    objects: list[dict] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        try:
            obj, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}: malformed snapshot at offset {index}: {exc}")
        if isinstance(obj, dict):
            objects.append(obj)
    return objects


def extract_series(snapshots: list[dict]) -> list[tuple[date, int]]:
    """Return (day, stars) sorted by day.

    Snapshots reporting zero stars are dropped: the collector used to fall
    back to '0' when the API call failed, which wrote a phantom drop to
    zero into the history (2026-03-30). A repository that genuinely has no
    stars simply produces an empty chart.
    """
    series: list[tuple[date, int]] = []
    for snap in snapshots:
        raw_day, stars = snap.get("date"), snap.get("stars")
        if not raw_day or not isinstance(stars, int) or stars <= 0:
            continue
        series.append((date.fromisoformat(raw_day), stars))
    series.sort(key=lambda item: item[0])
    return series


def backfill_series(stars_file: Path, until: date) -> list[tuple[date, int]]:
    """Reconstruct the history before daily collection started.

    The stargazers endpoint carries a starred_at timestamp per star, and a
    repo admin still gets it despite the 2026-06-30 restriction. Summing
    those per day gives the curve back to the very first star.

    Caveat: it only knows stars that still exist today, so it cannot show
    a star that was later withdrawn. That is why it is used purely as the
    prefix — from the first measured snapshot onward the daily counts take
    over, and those record withdrawals correctly.
    """
    if not stars_file.exists():
        return []

    payload = json.loads(stars_file.read_text(encoding="utf-8"))
    daily: dict[str, int] = payload.get("daily_stars", {})
    if not daily:
        return []

    series: list[tuple[date, int]] = []
    created = payload.get("repo_created")
    if created:
        # Start at repository creation so the quiet opening months are visible.
        series.append((date.fromisoformat(created), 0))

    running = 0
    for day_iso in sorted(daily):
        day = date.fromisoformat(day_iso)
        if day >= until:
            break
        running += daily[day_iso]
        series.append((day, running))
    return series


def month_ticks(first: date, last: date) -> list[date]:
    """First of every month in range; the range start only if it stands alone.

    Labelling the start day as well would collide with the next month's
    label whenever collection began late in a month.
    """
    ticks: list[date] = []
    year, month = first.year, first.month
    while True:
        month += 1
        if month > 12:
            month, year = 1, year + 1
        candidate = date(year, month, 1)
        if candidate > last:
            break
        ticks.append(candidate)
    if not ticks or (ticks[0] - first).days > 12:
        ticks.insert(0, first)
    return ticks


def nice_step(span: int, target_ticks: int = 4) -> int:
    """Round tick spacing up to something a human would pick."""
    if span <= target_ticks:
        return 1
    rough = span / target_ticks
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if step >= rough:
            return step
    return 2000


def build_svg(series: list[tuple[date, int]], repo: str,
              measured_from: date | None = None) -> str:
    first_day, last_day = series[0][0], series[-1][0]
    max_stars = max(stars for _, stars in series)

    span_days = max((last_day - first_day).days, 1)
    y_step = nice_step(max_stars)
    y_max = ((max_stars // y_step) + 1) * y_step

    plot_width = WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    plot_height = HEIGHT - MARGIN_TOP - MARGIN_BOTTOM

    def x_of(day: date) -> float:
        return MARGIN_LEFT + plot_width * ((day - first_day).days / span_days)

    def y_of(stars: float) -> float:
        return MARGIN_TOP + plot_height * (1 - stars / y_max)

    # Stars only ever change in whole steps, so a step line is honest:
    # it holds the previous value until the day the count actually moved.
    points: list[str] = []
    previous_stars: int | None = None
    for day, stars in series:
        x = x_of(day)
        if previous_stars is not None:
            points.append(f"{x:.1f},{y_of(previous_stars):.1f}")
        points.append(f"{x:.1f},{y_of(stars):.1f}")
        previous_stars = stars
    line_points = " ".join(points)

    baseline = y_of(0)
    area_points = f"{x_of(first_day):.1f},{baseline:.1f} {line_points} {x_of(last_day):.1f},{baseline:.1f}"

    grid_attrs = f'stroke="{COLORS["grid"]}" stroke-width="1" opacity="0.28"'
    tick_attrs = f'{FONT} font-size="11" fill="{COLORS["text"]}"'

    parts: list[str] = []
    for value in range(0, y_max + 1, y_step):
        y = y_of(value)
        parts.append(
            f'<line {grid_attrs} x1="{MARGIN_LEFT}" y1="{y:.1f}" '
            f'x2="{WIDTH - MARGIN_RIGHT}" y2="{y:.1f}"/>'
        )
        parts.append(
            f'<text {tick_attrs} x="{MARGIN_LEFT - 10}" y="{y + 4:.1f}" '
            f'text-anchor="end">{value}</text>'
        )

    ticks = month_ticks(first_day, last_day)
    for position, tick in enumerate(ticks):
        x = x_of(tick)
        # Year on the first label and whenever it rolls over.
        show_year = position == 0 or tick.month == 1
        label = f"{MONTHS[tick.month - 1]} {tick.year}" if show_year else MONTHS[tick.month - 1]
        parts.append(
            f'<text {tick_attrs} x="{x:.1f}" y="{HEIGHT - MARGIN_BOTTOM + 20}" '
            f'text-anchor="middle">{label}</text>'
        )

    last_x, last_y = x_of(last_day), y_of(series[-1][1])
    body = "\n  ".join(parts)

    axis_attrs = f'stroke="{COLORS["axis"]}" stroke-width="1" opacity="0.55"'
    stars_now = series[-1][1]

    if measured_from and measured_from > first_day:
        provenance = (f"measured daily since {measured_from.isoformat()}, "
                      f"earlier points reconstructed from star timestamps")
    else:
        provenance = "collected daily from the GitHub API"

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="Star history for {repo}: {stars_now} stars">
  <text {FONT} font-size="15" font-weight="600" fill="{COLORS["title"]}" x="{MARGIN_LEFT}" y="24">Star History</text>
  <text {FONT} font-size="11" fill="{COLORS["text"]}" x="{MARGIN_LEFT}" y="{HEIGHT - 8}">{repo} &#183; {first_day.isoformat()} &#8211; {last_day.isoformat()} &#183; {provenance}</text>
  {body}
  <polygon fill="{COLORS["area"]}" opacity="0.12" points="{area_points}"/>
  <polyline fill="none" stroke="{COLORS["line"]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="{line_points}"/>
  <circle fill="{COLORS["line"]}" cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.5"/>
  <text {FONT} font-size="12" font-weight="600" fill="{COLORS["line"]}" x="{last_x - 8:.1f}" y="{last_y - 10:.1f}" text-anchor="end">{stars_now} stars</text>
  <line {axis_attrs} x1="{MARGIN_LEFT}" y1="{MARGIN_TOP}" x2="{MARGIN_LEFT}" y2="{HEIGHT - MARGIN_BOTTOM}"/>
  <line {axis_attrs} x1="{MARGIN_LEFT}" y1="{HEIGHT - MARGIN_BOTTOM}" x2="{WIDTH - MARGIN_RIGHT}" y2="{HEIGHT - MARGIN_BOTTOM}"/>
</svg>
'''


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not repo:
        raise SystemExit("Repository unknown: set GITHUB_REPOSITORY or pass owner/name as argument")

    measured = extract_series(read_snapshots(DATA_FILE))
    if not measured:
        raise SystemExit(f"{DATA_FILE}: no usable star counts found")

    # Daily collection only started in February; everything before that is
    # reconstructed from the stars' own timestamps.
    series = backfill_series(STARS_FILE, until=measured[0][0]) + measured

    OUTPUT_FILE.write_text(
        build_svg(series, repo, measured_from=measured[0][0]), encoding="utf-8")
    print(f"{OUTPUT_FILE}: {len(series)} data points "
          f"({len(series) - len(measured)} reconstructed), {series[-1][1]} stars "
          f"({series[0][0]} - {series[-1][0]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
