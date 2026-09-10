#!/usr/bin/env python3
"""Comparable decode/prefill rates per model from AIfred's session files.

Single source for the speed tables in docs/de/benchmarks/performance-history.md.
Reads data/sessions/*.json and prints a Markdown table.

Comparability rules (why some records are dropped):
- llama.cpp records carry the server's own timings (predicted_per_second),
  a pure decode rate — always usable.
- vLLM records before VLLM_METRICS_FIX were wall-clock rates including the
  prefill (commit 7f870514, 2026-09-01 17:34: "Prefill und Decode aus vLLMs
  eigenen Zaehlern statt Wanduhr"). They are not comparable and are dropped.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

VLLM_METRICS_FIX = "2026-09-01 17:34:20"
# llama-swap group variants of the same main model (side models, speed split).
GROUP_SUFFIX = re.compile(r"(-vlm-qwen3vl4b|-tts-qwen3local|-speed)+$")
SOURCE_MODEL = re.compile(r"\(([^)]*)\)\s*$")


def _de(value: float, decimals: int) -> str:
    """German decimal comma for the German doc table.

    Not aifred.lib.formatting.format_number: importing aifred.lib pulls in
    the app config (API keys, debug log) and prints to stdout — that output
    ended up in the doc on 2026-09-10.
    """
    return f"{value:.{decimals}f}".replace(".", ",")


def collect(sessions: Path) -> dict[tuple[str, str], list[dict]]:
    """Group comparable answer records by (base model, backend)."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for path in sessions.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for message in data.get("data", {}).get("chat_history", []):
            meta = message.get("metadata") if isinstance(message, dict) else None
            if not meta or not meta.get("tokens_per_sec"):
                continue
            match = SOURCE_MODEL.search(meta.get("source", ""))
            model = match.group(1) if match else meta.get("source", "")
            backend = "vllm" if "-vllm" in model else "llamacpp"
            stamp = str(message.get("timestamp", ""))[:19].replace("T", " ")
            if backend == "vllm" and stamp < VLLM_METRICS_FIX:
                continue
            groups[(GROUP_SUFFIX.sub("", model), backend)].append(
                {"stamp": stamp, "decode": meta["tokens_per_sec"],
                 "prefill": meta.get("prompt_per_sec")}
            )
    return groups


def render(groups: dict[tuple[str, str], list[dict]]) -> str:
    """Markdown table: one row per (model, backend)."""
    lines = [
        "| Modell | Backend | n | Decode Median (Spanne) | Prefill Median | Zeitraum |",
        "|---|---|---:|---|---:|---|",
    ]
    for (model, backend), records in sorted(groups.items()):
        decode = sorted(r["decode"] for r in records)
        prefill = [r["prefill"] for r in records if r["prefill"]]
        stamps = sorted(r["stamp"][:10] for r in records)
        prefill_text = _de(statistics.median(prefill), 0) if prefill else "–"
        low, high = _de(decode[0], 1), _de(decode[-1], 1)
        lines.append(
            f"| {model} | {backend} | {len(decode)} "
            f"| {_de(statistics.median(decode), 1)} ({low}–{high}) "
            f"| {prefill_text} | {stamps[0]} – {stamps[-1]} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessions", type=Path, default=Path("data/sessions"))
    args = parser.parse_args()
    print(render(collect(args.sessions)))


if __name__ == "__main__":
    main()
