"""Scripture reference lookup against a structured Bible JSON.

Resolves textual references like ``Psalm 5``, ``Joh 3,16`` or
``1. Mose 1,1-5`` to the exact verse text — the precise counterpart to
semantic search when a concrete passage is named instead of a topic.

The Bible JSON (book/chapter/verse; see scripts/build_schlachter1951.py)
defaults to the Schlachter 1951 file under data/. Override the path with
the ``BIBLE_REFERENCE_JSON`` environment variable. The translation name
is read from the JSON's own ``translation`` field — no translation is
hard-coded here.
"""
from __future__ import annotations

import functools
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ....lib.config import DATA_DIR

_DEFAULT_BIBLE_JSON = (
    DATA_DIR / "documents" / "bibel" / "Schlachter" / "schlachter1951.json"
)
BIBLE_PATH = Path(os.environ.get("BIBLE_REFERENCE_JSON", str(_DEFAULT_BIBLE_JSON)))

# Book aliases: nr -> [canonical display name, abbreviations…]. Matching
# is case-insensitive and tolerant of dot/space in numbered books
# ("1. Mose" == "1.Mose" == "1 Mose" == "1Mose").
_BOOKS: dict[int, list[str]] = {
    1: ["1. Mose", "1Mo", "Genesis", "Gen"],
    2: ["2. Mose", "2Mo", "Exodus", "Ex"],
    3: ["3. Mose", "3Mo", "Levitikus", "Lev"],
    4: ["4. Mose", "4Mo", "Numeri", "Num"],
    5: ["5. Mose", "5Mo", "Deuteronomium", "Dtn"],
    6: ["Josua", "Jos"],
    7: ["Richter", "Ri"],
    8: ["Rut", "Ruth"],
    9: ["1. Samuel", "1Sam"],
    10: ["2. Samuel", "2Sam"],
    11: ["1. Könige", "1Kön"],
    12: ["2. Könige", "2Kön"],
    13: ["1. Chronik", "1Chr"],
    14: ["2. Chronik", "2Chr"],
    15: ["Esra", "Esr"],
    16: ["Nehemia", "Neh"],
    17: ["Ester", "Est"],
    18: ["Hiob", "Hi", "Ijob"],
    19: ["Psalmen", "Psalm", "Ps"],
    20: ["Sprüche", "Spr"],
    21: ["Prediger", "Pred", "Kohelet"],
    22: ["Hohes Lied", "Hoheslied", "Hohelied", "Hld"],
    23: ["Jesaja", "Jes"],
    24: ["Jeremia", "Jer"],
    25: ["Klagelieder", "Klgl"],
    26: ["Hesekiel", "Hes", "Ezechiel", "Ez"],
    27: ["Daniel", "Dan"],
    28: ["Hosea", "Hos"],
    29: ["Joel", "Joe"],
    30: ["Amos", "Am"],
    31: ["Obadja", "Obd"],
    32: ["Jona", "Jon"],
    33: ["Micha", "Mi"],
    34: ["Nahum", "Nah"],
    35: ["Habakuk", "Hab"],
    36: ["Zefanja", "Zef"],
    37: ["Haggai", "Hag"],
    38: ["Sacharja", "Sach"],
    39: ["Maleachi", "Mal"],
    40: ["Matthäus", "Mt", "Matth"],
    41: ["Markus", "Mk", "Mark"],
    42: ["Lukas", "Lk", "Luk"],
    43: ["Johannes", "Joh"],
    44: ["Apostelgeschichte", "Apg"],
    45: ["Römer", "Röm"],
    46: ["1. Korinther", "1Kor"],
    47: ["2. Korinther", "2Kor"],
    48: ["Galater", "Gal"],
    49: ["Epheser", "Eph"],
    50: ["Philipper", "Phil"],
    51: ["Kolosser", "Kol"],
    52: ["1. Thessalonicher", "1Thess", "1Thes"],
    53: ["2. Thessalonicher", "2Thess", "2Thes"],
    54: ["1. Timotheus", "1Tim"],
    55: ["2. Timotheus", "2Tim"],
    56: ["Titus", "Tit"],
    57: ["Philemon", "Phlm"],
    58: ["Hebräer", "Hebr", "Heb"],
    59: ["Jakobus", "Jak"],
    60: ["1. Petrus", "1Petr", "1Pt"],
    61: ["2. Petrus", "2Petr", "2Pt"],
    62: ["1. Johannes", "1Joh"],
    63: ["2. Johannes", "2Joh"],
    64: ["3. Johannes", "3Joh"],
    65: ["Judas", "Jud"],
    66: ["Offenbarung", "Offb", "Apk", "Apokalypse"],
}


def _norm(name: str) -> str:
    """Normalize a book name for alias lookup: lowercase, no dots/spaces."""
    return re.sub(r"[.\s]", "", name).lower()


# normalized alias -> book nr
_ALIAS_TO_NR: dict[str, int] = {
    _norm(alias): nr for nr, names in _BOOKS.items() for alias in names
}


def _flex(alias: str) -> str:
    """Regex fragment matching an alias tolerant of its dots/spaces.

    ``re.escape`` escapes the space to ``\\ ``, so the escaped form is
    what gets replaced — not a bare space.
    """
    return re.escape(alias).replace(r"\.", r"\.?").replace(r"\ ", r"\s*")


@functools.lru_cache(maxsize=1)
def _pattern() -> re.Pattern:
    """Compiled reference regex: <book> <chapter>[,<verse>[-<verse>]]."""
    forms = [a for names in _BOOKS.values() for a in names]
    forms.sort(key=len, reverse=True)  # "1. Johannes" must beat "Johannes"
    book_alt = "|".join(_flex(f) for f in forms)
    return re.compile(
        rf"\b(?P<book>{book_alt})\.?\s+(?P<ch>\d+)"
        rf"(?:\s*[,:]\s*(?P<v1>\d+)(?:\s*[-–]\s*(?P<v2>\d+))?)?",
        re.IGNORECASE,
    )


@functools.lru_cache(maxsize=1)
def _bible() -> dict:
    """Load the Bible JSON.

    Returns ``{"translation": str, "books": {nr: {"name", "chapters"}}}``;
    the translation name comes from the JSON itself, not from code.
    """
    with open(BIBLE_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    books: dict[int, dict] = {}
    for book in raw["books"]:
        chapters = {
            ch["chapter"]: {v["verse"]: v["text"] for v in ch["verses"]}
            for ch in book["chapters"]
        }
        books[book["nr"]] = {"name": book["name"], "chapters": chapters}
    return {"translation": raw.get("translation", "Bible"), "books": books}


def data_available() -> bool:
    """True if the Bible JSON is present (the lookup's data source)."""
    return BIBLE_PATH.is_file()


@dataclass
class BibleReference:
    """A parsed scripture reference."""

    book_nr: int
    book_name: str       # canonical name
    chapter: int
    verse_from: Optional[int]   # None = whole chapter
    verse_to: Optional[int]     # None = single verse (or whole chapter)
    display: str         # human form, e.g. "Psalm 5" or "Johannes 3,16"


def parse_reference(text: str) -> Optional[BibleReference]:
    """Extract the first scripture reference from ``text``; None if none."""
    m = _pattern().search(text)
    if not m:
        return None
    nr = _ALIAS_TO_NR.get(_norm(m.group("book")))
    if nr is None:
        return None
    chapter = int(m.group("ch"))
    v1 = int(m.group("v1")) if m.group("v1") else None
    v2 = int(m.group("v2")) if m.group("v2") else None
    canonical = _BOOKS[nr][0]
    # Psalms are cited "Psalm 5", not "Psalmen 5".
    label = "Psalm" if nr == 19 else canonical
    display = f"{label} {chapter}"
    if v1 is not None:
        display += f",{v1}" + (f"-{v2}" if v2 else "")
    return BibleReference(nr, canonical, chapter, v1, v2, display)


def lookup(ref: BibleReference) -> dict:
    """Resolve a reference to verse text. Returns a result dict.

    On success: ``{reference, book, chapter, translation, verses:[…]}``.
    On a missing book/chapter/verse: ``{reference, error}``.
    """
    bible = _bible()
    book = bible["books"].get(ref.book_nr)
    if not book:
        return {"reference": ref.display, "error": "Book not in this translation"}
    chapter = book["chapters"].get(ref.chapter)
    if not chapter:
        return {"reference": ref.display,
                "error": f"{ref.book_name} has no chapter {ref.chapter}"}

    if ref.verse_from is None:
        wanted = sorted(chapter)
    elif ref.verse_to is None:
        wanted = [ref.verse_from]
    else:
        wanted = list(range(ref.verse_from, ref.verse_to + 1))

    verses = [{"verse": v, "text": chapter[v]} for v in wanted if v in chapter]
    if not verses:
        return {"reference": ref.display,
                "error": f"{ref.book_name} {ref.chapter} has no such verse(s)"}
    return {
        "reference": ref.display,
        "book": ref.book_name,
        "chapter": ref.chapter,
        "translation": bible["translation"],
        "verses": verses,
    }


def resolve(text: str) -> Optional[dict]:
    """Parse a reference from ``text`` and look it up in one step.

    Returns the lookup result dict, or None if ``text`` contains no
    recognizable scripture reference.
    """
    ref = parse_reference(text)
    return lookup(ref) if ref else None
