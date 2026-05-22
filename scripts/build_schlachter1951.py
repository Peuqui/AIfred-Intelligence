#!/usr/bin/env python3
"""Build the Schlachter 1951 data files from getbible.

The Schlachter 1951 is public domain. This script fetches all 66 books
from the getbible.net v2 API (one request per book) and writes two files
into data/documents/bibel/Schlachter/:

  schlachter1951.json -- structured book/chapter/verse; data source for
                         the scripture-reference lookup
                         (aifred/lib/bible_reference.py). Validated to
                         have every chapter contiguously numbered 1..N.
  schlachter1951.txt  -- plain text, one verse per line prefixed with its
                         full reference; meant to be indexed for thematic
                         vector search.

Both live under data/, which is not version-controlled — so this script
is the reproducible way to recreate them (e.g. on a fresh deployment).

Usage:  python scripts/build_schlachter1951.py
"""
import json
import time
import urllib.request
from pathlib import Path

_API = "https://api.getbible.net/v2/schlachter/{}.json"
_OUT = (
    Path(__file__).resolve().parent.parent
    / "data" / "documents" / "bibel" / "Schlachter" / "schlachter1951.json"
)
_OUT_TXT = _OUT.with_suffix(".txt")


def _fetch_book(nr: int) -> dict:
    """Fetch one book; getbible blocks urllib's default UA, so spoof it."""
    req = urllib.request.Request(
        _API.format(nr), headers={"User-Agent": "Mozilla/5.0"}
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1.5)
    raise RuntimeError(f"unreachable: book {nr}")  # pragma: no cover


def main() -> None:
    books_out: list[dict] = []
    total_verses = 0
    total_chapters = 0
    issues: list[str] = []

    for nr in range(1, 67):
        data = _fetch_book(nr)
        chapters_out = []
        for ch in data["chapters"]:
            verses = ch["verses"]
            vnums = [v["verse"] for v in verses]
            if vnums != list(range(1, len(vnums) + 1)):
                issues.append(f"{data['name']} {ch['chapter']}")
            chapters_out.append({
                "chapter": ch["chapter"],
                "verses": [
                    {"verse": v["verse"], "text": v["text"].strip()}
                    for v in verses
                ],
            })
            total_verses += len(verses)
            total_chapters += 1
        books_out.append(
            {"nr": data["nr"], "name": data["name"], "chapters": chapters_out}
        )
        print(f"  {nr:2d}. {data['name']}: {len(chapters_out)} Kapitel")
        time.sleep(0.1)  # be polite to the API

    out = {
        "translation": "Schlachter (1951)",
        "source": "getbible.net v2",
        "books": books_out,
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # Plain-text form for the vector index: one verse per line, each
    # prefixed with its full reference so every chunk stays self-locating.
    # A blank line between chapters gives the chunker a natural break.
    lines: list[str] = []
    for book in books_out:
        for ch in book["chapters"]:
            for v in ch["verses"]:
                lines.append(
                    f"{book['name']} {ch['chapter']},{v['verse']}  {v['text']}"
                )
            lines.append("")
    _OUT_TXT.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n{len(books_out)} Bücher, {total_chapters} Kapitel, "
          f"{total_verses} Verse")
    print(f"  → {_OUT}")
    print(f"  → {_OUT_TXT}")
    if issues:
        print(f"WARNUNG: {len(issues)} Kapitel mit Nummerierungslücken: "
              f"{issues[:10]}")
    else:
        print("Validierung: alle Kapitel lückenlos nummeriert ✓")


if __name__ == "__main__":
    main()
