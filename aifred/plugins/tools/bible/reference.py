"""Scripture reference lookup against a structured Bible JSON.

Resolves textual references like ``Psalm 5``, ``Joh 3,16`` or
``1. Mose 1,1-5`` to the exact verse text — the precise counterpart to
semantic search when a concrete passage is named instead of a topic.

Bible data lives under data/documents/bibel/, one sub-folder per
translation, each holding a structured book/chapter/verse JSON (see
scripts/build_bible.py). Which translation is active is the
``BIBLE_TRANSLATION`` setting — the sub-folder name — managed via the
plugin settings; when unset, the first available translation is used.
The translation name and the language are read from the JSON's own
``translation`` / ``language`` fields — nothing translation- or
language-specific is hard-coded here.

Book recognition is fully data-driven:

- the canonical book names come from the JSON's per-book ``name`` field
  (so the lookup speaks the language of whichever Bible is loaded);
- the abbreviations come from a language-specific alias table,
  ``book_aliases/<lang>.json``, shipped with the plugin.

Any 66-book Bible in any language works — German Schlachter, English
KJV, … — by dropping its JSON folder in. A new language only needs its
own ``book_aliases/<lang>.json``.
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

# Root of all Bible translations — one sub-folder per translation, each
# holding the structured *.json built by scripts/build_bible.py.
BIBLE_ROOT = DATA_DIR / "documents" / "bibel"

# Language-specific book-alias tables live next to this module.
_ALIAS_DIR = Path(__file__).parent / "book_aliases"

# Settings / os.environ key naming the active translation: the
# BIBLE_ROOT sub-folder to read. Set via the plugin settings.
TRANSLATION_ENV_KEY = "BIBLE_TRANSLATION"


def available_translations() -> list[str]:
    """Sub-folders of BIBLE_ROOT that hold a Bible JSON — the set of
    translations the user can choose between."""
    if not BIBLE_ROOT.is_dir():
        return []
    return sorted(
        d.name for d in BIBLE_ROOT.iterdir()
        if d.is_dir() and any(d.glob("*.json"))
    )


def _active_bible_path() -> Optional[Path]:
    """The JSON file of the currently selected translation.

    The translation is the BIBLE_ROOT sub-folder named by the
    ``BIBLE_TRANSLATION`` setting; its single ``*.json`` file is the
    data source. When the setting is unset or names a folder that is
    gone, the first available translation is used instead. ``None``
    when no translation exists at all.
    """
    wanted = os.environ.get(TRANSLATION_ENV_KEY, "").strip()
    names = available_translations()
    ordered = ([wanted] if wanted in names else []) + names
    for name in ordered:
        jsons = sorted((BIBLE_ROOT / name).glob("*.json"))
        if jsons:
            return jsons[0]
    return None


def _norm(name: str) -> str:
    """Normalize a book name for alias lookup: lowercase, no dots/spaces."""
    return re.sub(r"[.\s]", "", name).lower()


def _flex(alias: str) -> str:
    """Regex fragment matching an alias tolerant of its dots/spaces.

    ``re.escape`` escapes the space to ``\\ ``, so the escaped form is
    what gets replaced — not a bare space. Kompakte Ziffer-Aliasse
    ("2Tim", "1Kor") erlauben nach der führenden Ziffer zusätzlich
    optionalen Punkt/Whitespace — sonst matchen gängige Zitierformen
    wie "2 Tim 1,7" oder "1. Kor 13" nie und fallen still in die
    unscharfe thematische Suche.
    """
    body = re.escape(alias).replace(r"\.", r"\.?").replace(r"\ ", r"\s*")
    if len(alias) > 1 and alias[0].isdigit() and alias[1] not in " .":
        body = body[0] + r"\.?\s*" + body[1:]
    return body


@functools.lru_cache(maxsize=1)
def _bible() -> dict:
    """Load the active translation's Bible JSON.

    Returns ``{"translation": str, "language": str,
    "books": {nr: {"name", "chapters"}}}``; the translation name and
    language come from the JSON itself, not from code.
    """
    path = _active_bible_path()
    if path is None:
        raise FileNotFoundError(f"No Bible translation found under {BIBLE_ROOT}")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    books: dict[int, dict] = {}
    for book in raw["books"]:
        chapters = {
            ch["chapter"]: {v["verse"]: v["text"] for v in ch["verses"]}
            for ch in book["chapters"]
        }
        books[book["nr"]] = {"name": book["name"], "chapters": chapters}
    return {
        "translation": raw.get("translation", "Bible"),
        "language": raw.get("language", "de"),
        "books": books,
    }


@functools.lru_cache(maxsize=8)
def _aliases(lang: str) -> dict[int, list[str]]:
    """Abbreviation table for ``lang`` (``book_aliases/<lang>.json``).

    Returns ``{nr: [abbreviation, …]}``. An empty dict when the language
    ships no alias file — recognition then rests on the canonical book
    names from the JSON alone (full names still work, abbreviations
    don't).
    """
    path = _ALIAS_DIR / f"{lang}.json"
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {int(nr): list(forms) for nr, forms in raw.items()}


def _book_forms() -> dict[int, list[str]]:
    """All recognition forms per book nr: the canonical name from the
    loaded Bible JSON plus the current language's abbreviations."""
    bible = _bible()
    aliases = _aliases(bible["language"])
    return {
        nr: [book["name"], *aliases.get(nr, [])]
        for nr, book in bible["books"].items()
    }


@functools.lru_cache(maxsize=1)
def _alias_to_nr() -> dict[str, int]:
    """Normalized recognition form -> book nr."""
    return {
        _norm(form): nr
        for nr, forms in _book_forms().items()
        for form in forms
    }


@functools.lru_cache(maxsize=1)
def _pattern() -> re.Pattern:
    """Compiled reference regex: <book> <chapter>[,<verse>[-<verse>]]."""
    forms = [f for fs in _book_forms().values() for f in fs]
    forms.sort(key=len, reverse=True)  # "1. Johannes" must beat "Johannes"
    book_alt = "|".join(_flex(f) for f in forms)
    return re.compile(
        rf"\b(?P<book>{book_alt})\.?\s+(?P<ch>\d+)"
        rf"(?:\s*[,:]\s*(?P<v1>\d+)(?:\s*[-–]\s*(?P<v2>\d+))?)?",
        re.IGNORECASE,
    )


def reload() -> None:
    """Drop all cached state so the next lookup re-reads the active
    translation. Called after the translation setting changes."""
    _bible.cache_clear()
    _alias_to_nr.cache_clear()
    _pattern.cache_clear()


def data_available() -> bool:
    """True if at least one Bible translation is present (the lookup's
    data source)."""
    return _active_bible_path() is not None


@dataclass
class BibleReference:
    """A parsed scripture reference."""

    book_nr: int
    book_name: str       # canonical name, as the loaded JSON spells it
    chapter: int
    verse_from: Optional[int]   # None = whole chapter
    verse_to: Optional[int]     # None = single verse (or whole chapter)
    display: str         # human form, e.g. "Psalmen 5" or "Johannes 3,16"


def parse_reference(text: str) -> Optional[BibleReference]:
    """Extract the first scripture reference from ``text``; None if none."""
    m = _pattern().search(text)
    if not m:
        return None
    nr = _alias_to_nr().get(_norm(m.group("book")))
    if nr is None:
        return None
    chapter = int(m.group("ch"))
    v1 = int(m.group("v1")) if m.group("v1") else None
    v2 = int(m.group("v2")) if m.group("v2") else None
    # The canonical display name is whatever the loaded Bible JSON calls
    # the book — no per-book special-casing in code.
    canonical = _bible()["books"][nr]["name"]
    display = f"{canonical} {chapter}"
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
        # Iterate the chapter's ACTUAL verses within the span — never
        # range(from, to+1): an unbounded verse_to from the parser (e.g.
        # "Johannes 3,1-9999999999") would materialize a multi-billion-element
        # list and OOM the worker before the membership filter below runs.
        lo, hi = ref.verse_from, ref.verse_to
        wanted = sorted(v for v in chapter if lo <= v <= hi)

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
