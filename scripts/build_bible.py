#!/usr/bin/env python3
"""Build a Bible's data files from getbible.net.

Fetches all 66 books of a public-domain translation from the
getbible.net v2 API (one request per book) and writes two files into
data/documents/bibel/<output-dir>/:

  <slug>.json -- structured book/chapter/verse; data source for the
                 scripture-reference lookup (aifred/plugins/tools/
                 bible/reference.py). Carries the translation name and
                 the ISO language code — both read straight from the
                 API, nothing hard-coded — and is validated to have
                 every chapter contiguously numbered 1..N.
  <slug>.txt  -- plain text, one verse per line prefixed with its full
                 reference; meant to be indexed for thematic search.

data/ is not version-controlled, so this script is the reproducible way
to (re)create a Bible — on a fresh deployment, or to add another
translation. Find the slugs at getbible.net (English KJV is ``kjv``,
the Schlachter 1951 is ``schlachter``).

Usage:  python scripts/build_bible.py <getbible-slug> [<output-dir>]
        python scripts/build_bible.py schlachter
        python scripts/build_bible.py kjv KingJames
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

_API = "https://api.getbible.net/v2/{translation}/{nr}.json"
_BIBLE_ROOT = (
    Path(__file__).resolve().parent.parent / "data" / "documents" / "bibel"
)


def _fetch_book(translation: str, nr: int) -> dict:
    """Fetch one book; getbible blocks urllib's default UA, so spoof it."""
    req = urllib.request.Request(
        _API.format(translation=translation, nr=nr),
        headers={"User-Agent": "Mozilla/5.0"},
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
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    translation = sys.argv[1]
    out_dir = _BIBLE_ROOT / (
        sys.argv[2] if len(sys.argv) > 2 else translation.capitalize()
    )
    out_json = out_dir / f"{translation}.json"
    out_txt = out_json.with_suffix(".txt")

    books_out: list[dict] = []
    total_verses = 0
    total_chapters = 0
    issues: list[str] = []
    name = ""   # translation display name, from the API
    lang = ""   # ISO language code, from the API

    for nr in range(1, 67):
        data = _fetch_book(translation, nr)
        name = name or data.get("translation", translation)
        lang = lang or data.get("lang", "")
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
        "translation": name,
        "language": lang,
        "source": "getbible.net v2",
        "books": books_out,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
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
    out_txt.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n{name} ({lang}) — {len(books_out)} Bücher, {total_chapters} "
          f"Kapitel, {total_verses} Verse")
    print(f"  → {out_json}")
    print(f"  → {out_txt}")
    if issues:
        print(f"WARNUNG: {len(issues)} Kapitel mit Nummerierungslücken: "
              f"{issues[:10]}")
    else:
        print("Validierung: alle Kapitel lückenlos nummeriert ✓")


if __name__ == "__main__":
    main()
