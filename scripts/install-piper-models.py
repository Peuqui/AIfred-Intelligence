#!/usr/bin/env python3
"""Interactive Piper TTS voice-model downloader.

Reads the voice catalog from aifred/lib/config.py (the single source of
truth for which voices the UI offers), presents a numbered multi-select
prompt, and downloads the .onnx + .onnx.json files from the official
Piper-Voices HuggingFace repo into piper_models/.

The voice catalog ships a filename like ``de_DE-eva_k-x_low.onnx``;
this script parses it into the URL components required by HF:

    https://huggingface.co/rhasspy/piper-voices/resolve/main/
      <lang_short>/<lang_code>/<voice>/<quality>/<filename>

Run as part of scripts/install-all.sh or standalone:

    venv/bin/python scripts/install-piper-models.py
    venv/bin/python scripts/install-piper-models.py --all     # download all without prompt
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from aifred.lib.config import PIPER_VOICES  # noqa: E402

HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
MODELS_DIR = REPO_ROOT / "piper_models"


def parse_filename(filename: str) -> tuple[str, str, str, str]:
    """Split ``de_DE-thorsten-high.onnx`` into (lang_short, lang_code, voice, quality)."""
    stem = filename.removesuffix(".onnx")
    parts = stem.split("-")
    if len(parts) < 3:
        raise ValueError(f"Cannot parse Piper filename: {filename}")
    lang_code = parts[0]                       # de_DE
    quality = parts[-1]                        # high / low / x_low / medium
    voice = "-".join(parts[1:-1])              # thorsten / eva_k / mls
    lang_short = lang_code.split("_")[0]       # de
    return lang_short, lang_code, voice, quality


def hf_url(filename: str) -> str:
    lang_short, lang_code, voice, quality = parse_filename(filename)
    return f"{HF_BASE}/{lang_short}/{lang_code}/{voice}/{quality}/{filename}"


def download(url: str, dest: Path) -> bool:
    """Download with simple progress dots. Returns True on success."""
    try:
        sys.stdout.write(f"   ↓ {dest.name} ... ")
        sys.stdout.flush()
        with urllib.request.urlopen(url, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            next_pct = 10
            with dest.open("wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        while pct >= next_pct and next_pct <= 100:
                            sys.stdout.write(f"{next_pct}% ")
                            sys.stdout.flush()
                            next_pct += 10
            sys.stdout.write("OK\n")
            return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        sys.stdout.write(f"FAIL ({e})\n")
        if dest.exists():
            dest.unlink()
        return False


def parse_selection(raw: str, max_n: int) -> list[int]:
    """Parse "1,3,5" / "1-3" / "all" into a list of 1-based indices.

    Empty input returns []; the caller decides whether to abort.
    """
    raw = raw.strip().lower()
    if not raw:
        return []
    if raw in ("all", "alle"):
        return list(range(1, max_n + 1))
    picked: set[int] = set()
    for token in raw.replace(",", " ").split():
        if "-" in token:
            a, b = token.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                continue
            for i in range(min(start, end), max(start, end) + 1):
                if 1 <= i <= max_n:
                    picked.add(i)
        else:
            try:
                i = int(token)
            except ValueError:
                continue
            if 1 <= i <= max_n:
                picked.add(i)
    return sorted(picked)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Download all voices without prompting")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Exit without prompting if stdin is not a TTY (for use in installers)",
    )
    args = parser.parse_args()

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    voices = list(PIPER_VOICES.items())  # [(display_name, (filename, lang)), ...]
    if not voices:
        print("⚠️  No voices configured in aifred/lib/config.py PIPER_VOICES.")
        return 1

    print()
    print("Piper TTS — verfügbare Stimmen aus config.py:")
    print()
    for idx, (display, (filename, _lang)) in enumerate(voices, start=1):
        present = (MODELS_DIR / filename).exists()
        mark = "✓" if present else " "
        print(f"   [{mark}] {idx:>2}. {display:<24} ({filename})")
    print()
    print("   ✓ = bereits in piper_models/ vorhanden")
    print()

    if args.all:
        chosen = list(range(1, len(voices) + 1))
    elif args.non_interactive and not sys.stdin.isatty():
        print("Nicht-interaktiv & keine TTY — übersprungen.")
        return 0
    else:
        prompt = (
            "Welche herunterladen? (z.B. '1 3', '1-3', 'all', leer = abbrechen): "
        )
        try:
            raw = input(prompt)
        except EOFError:
            print()
            return 0
        chosen = parse_selection(raw, len(voices))
        if not chosen:
            print("Nichts ausgewählt — abgebrochen.")
            return 0

    failures = 0
    for idx in chosen:
        display, (filename, _lang) = voices[idx - 1]
        onnx_path = MODELS_DIR / filename
        json_path = MODELS_DIR / f"{filename}.json"

        if onnx_path.exists() and json_path.exists():
            print(f"   ✓ {display} bereits vorhanden — übersprungen")
            continue

        try:
            base_url = hf_url(filename)
        except ValueError as e:
            print(f"   ✗ {display}: {e}")
            failures += 1
            continue

        ok_onnx = onnx_path.exists() or download(base_url, onnx_path)
        ok_json = json_path.exists() or download(f"{base_url}.json", json_path)
        if not (ok_onnx and ok_json):
            failures += 1

    print()
    if failures:
        print(f"⚠️  {failures} Download(s) fehlgeschlagen.")
        return 1
    print(f"✅ Piper-Modelle in {MODELS_DIR} bereit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
