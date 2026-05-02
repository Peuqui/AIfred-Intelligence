#!/usr/bin/env python3
"""Verify completeness of downloaded Sefaria texts.

For every file produced by download_judaica.py, query Sefaria's
``/api/index/<slug>`` to learn the expected number of top-level
sections, count the actual ``## `` headers in our local file, and
report mismatches.

Threshold: anything below 80% of expected is treated as incomplete.

Run from repo root:
    venv/bin/python scripts/verify_judaica.py
"""

from __future__ import annotations

import sys
import time
import urllib.parse
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from scripts.download_judaica import TEXTS  # noqa: E402

SEFARIA_INDEX = "https://www.sefaria.org/api/index/{ref}"
SEFARIA_TEXT = "https://www.sefaria.org/api/texts/{ref}.{section}?lang=en&context=0&pad=0"
USER_AGENT = "AIfred-Intelligence/1.0 (+research)"
REQUEST_DELAY_SEC = 1.0
COMPLETENESS_THRESHOLD = 0.8  # < 80% → incomplete


def section_has_content(slug: str, section: int, session: requests.Session) -> bool:
    """Probe a single chapter/daf — returns False when Sefaria has zero
    paragraphs for it (used to detect schema inflation: a slug with
    declared length 17 but only 11 actually populated)."""
    url = SEFARIA_TEXT.format(
        ref=urllib.parse.quote(slug, safe="_"),
        section=section,
    )
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return False
        text = resp.json().get("text", [])
    except (requests.RequestException, ValueError):
        return False
    if isinstance(text, list):
        return any(t for t in text)
    return bool(text)


def expected_top_sections(slug: str, session: requests.Session) -> int | None:
    """Fetch index schema and return the expected number of top-level
    sections (chapters, daf etc.)."""
    url = SEFARIA_INDEX.format(ref=urllib.parse.quote(slug, safe="_"))
    resp = session.get(url, timeout=60)
    if resp.status_code != 200:
        return None
    data = resp.json()
    schema = data.get("schema") or {}
    lengths = schema.get("lengths")
    if isinstance(lengths, list) and lengths:
        return int(lengths[0])

    # Complex schemas (e.g. Chovot HaLevavot): sum the leaf nodes
    nodes = schema.get("nodes")
    if isinstance(nodes, list):
        total = 0
        for n in nodes:
            n_lengths = n.get("lengths")
            if isinstance(n_lengths, list) and n_lengths:
                total += int(n_lengths[0])
        if total > 0:
            return total
    return None


def count_local_sections(path: Path) -> int:
    """Count ``## `` headers in the local file (top-level sections)."""
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if line.startswith("## "))


def main() -> int:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    incomplete: list[tuple[str, str, int, int, float]] = []
    missing_index: list[str] = []
    ok_count = 0

    for spec in TEXTS:
        if not spec.output_path.exists():
            print(f"[SKIP] {spec.title}: file missing")
            continue

        print(f"[CHECK] {spec.title}", end=" ", flush=True)
        try:
            expected = expected_top_sections(spec.title, session)
        except Exception as exc:
            print(f"  index error: {exc}")
            missing_index.append(spec.title)
            time.sleep(REQUEST_DELAY_SEC)
            continue

        if expected is None or expected == 0:
            print("  (no expected count from index)")
            missing_index.append(spec.title)
            time.sleep(REQUEST_DELAY_SEC)
            continue

        actual = count_local_sections(spec.output_path)
        ratio = actual / expected if expected else 0.0

        # Detect language from file header
        first_lines = spec.output_path.read_text(encoding="utf-8").splitlines()[:5]
        lang = "DE" if any("Sprache: Deutsch" in line for line in first_lines) else "EN"

        if ratio < COMPLETENESS_THRESHOLD:
            # Schema-inflation check: probe the missing sections to see
            # if Sefaria actually has any data there. If the gap is empty
            # for everyone, our local file already mirrors Sefaria's full
            # holdings — nothing more to download.
            print(f" [{lang}] {actual}/{expected} sections ({ratio:.0%}) — probing gap... ",
                  end="", flush=True)
            empty_count = 0
            checked = 0
            for sec in range(actual + 1, expected + 1):
                if section_has_content(spec.title, sec, session):
                    break
                empty_count += 1
                checked += 1
                time.sleep(0.3)
            if empty_count == expected - actual:
                print(f"all {checked} missing sections empty in Sefaria — file is complete ✓")
                ok_count += 1
            else:
                print(f"section {actual + checked + 1} has data — TRULY INCOMPLETE ⚠️")
                incomplete.append((spec.title, lang, actual, expected, ratio))
        else:
            print(f" [{lang}] {actual}/{expected} sections ({ratio:.0%}) ok")
            ok_count += 1

        time.sleep(REQUEST_DELAY_SEC)

    print()
    print(f"Summary: {ok_count} ok, {len(incomplete)} incomplete, "
          f"{len(missing_index)} no-index")
    if incomplete:
        print()
        print("Incomplete texts (consider re-downloading in English):")
        for title, lang, actual, expected, ratio in incomplete:
            print(f"  - {title} [{lang}]: {actual}/{expected} ({ratio:.0%})")
    return 0 if not incomplete else 1


if __name__ == "__main__":
    sys.exit(main())
