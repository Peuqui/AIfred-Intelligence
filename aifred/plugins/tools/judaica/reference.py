"""Reference lookup against the structured Judaica JSON.

Resolves textual references like ``Berakhot 3``, ``Pirkei Avot 1,1`` or
``Rashi zu Genesis 1,1`` to the exact source text — the precise
counterpart to the thematic vector search when a concrete passage is
named instead of a topic.

The data source is built by scripts/build_judaica_json.py: one JSON per
work plus an ``_index.json`` listing every work with the names the
lookup recognises it by. Works have heterogeneous citation systems
(Daf/Line for the Talmud, Chapter/Verse for the Tanakh, …) — the lookup
treats them uniformly as section + optional entry:

- one number  -> the whole section ("Berakhot 3" -> all of Daf 3);
- two numbers -> section + entry ("Pirkei Avot 1,1" -> Chapter 1,
  Mishnah 1), optionally an entry range ("Pirkei Avot 1,1-3").

Nothing work-specific is hard-coded here — the recognition names and
the section/entry labels all come from ``_index.json``.
"""
from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from typing import Optional

from ....lib.config import DATA_DIR
# Normalisierung + Alias-Flexibilisierung: lib-SSOT, geteilt mit bible
# (Option-2-Entscheidung — nur diese Primitiva, Rest bleibt plugin-lokal).
from ....lib.reference_lookup import flex_alias, normalize_name

# SSOT für den Ordnernamen — __init__.py importiert beide Konstanten
# (reference.py ist das Blatt der Import-Kette, kein Zirkel möglich).
JUDAICA_FOLDER = "judaica"
JUDAICA_DIR = DATA_DIR / "documents" / JUDAICA_FOLDER
_INDEX_PATH = JUDAICA_DIR / "_index.json"


@functools.lru_cache(maxsize=1)
def _index() -> dict:
    """Load _index.json: ``{work_key: {json, work, source, names,
    section_type, entry_type}}``."""
    with open(_INDEX_PATH, encoding="utf-8") as f:
        return dict(json.load(f))


@functools.lru_cache(maxsize=16)
def _work_json(rel_path: str) -> dict:
    """Load one work's JSON (book/section/entry). Cached per work so
    repeated lookups into the same work don't re-read the file."""
    with open(JUDAICA_DIR / rel_path, encoding="utf-8") as f:
        return dict(json.load(f))


@functools.lru_cache(maxsize=1)
def _name_to_key() -> dict[str, str]:
    """Normalized recognition name -> work key."""
    return {
        normalize_name(name): key
        for key, entry in _index().items()
        for name in entry["names"]
    }


@functools.lru_cache(maxsize=1)
def _pattern() -> re.Pattern:
    """Compiled reference regex: <work> <section>[a|b][,<entry>[-<entry>]].

    The optional ``a``/``b`` after the section number is the Vilna-edition
    amud (recto/verso of a folio), used in Bavli citations like
    ``Sanhedrin 97b``. Our Talmud JSONs follow Sefaria, which lists every
    amud as one fortlaufender Daf — see :func:`_vilna_to_sefaria_daf`.
    Non-Talmud works (Mishnah, Tanakh, Midrash, …) keep numeric sections;
    the suffix simply never matches there.
    """
    names = [n for e in _index().values() for n in e["names"]]
    names.sort(key=len, reverse=True)  # "Mishnah Sanhedrin" beats "Sanhedrin"
    work_alt = "|".join(flex_alias(n) for n in names)
    return re.compile(
        rf"\b(?P<work>{work_alt})\.?\s+(?P<sec>\d+)(?P<amud>[ab])?"
        rf"(?:\s*[,:]\s*(?P<e1>\d+)(?:\s*[-–]\s*(?P<e2>\d+))?)?",
        re.IGNORECASE,
    )


def _vilna_to_sefaria_daf(vilna_page: int, amud: str) -> str:
    """Convert a Vilna Bavli reference (folio + amud) to the Sefaria daf.

    Sefaria lists each amud as one fortlaufender Daf-Eintrag, starting
    with the first Mishnah page as Daf 3 (Daf 1+2 are the editorial
    front-matter that Sefaria leaves empty for every Bavli tractate). So
    Sanhedrin 2a is Daf 3, 2b is Daf 4, 3a is Daf 5, …, 97b is Daf 194,
    113b is Daf 226. Formula: ``2 * vilna − (1 if 'a' else 0)``.
    """
    return str(2 * vilna_page - (1 if amud == "a" else 0))


def data_available() -> bool:
    """True if the structured Judaica index is present (the reference
    lookup's data source — built by scripts/build_judaica_json.py)."""
    return _INDEX_PATH.is_file()


@dataclass
class JudaicaReference:
    """A parsed Judaica reference."""

    work_key: str
    work_name: str             # display name
    section: str               # section number (JSON keys are strings)
    amud: Optional[str]        # 'a' / 'b' for Talmud Bavli daf, else None
    entry_from: Optional[str]  # None = whole section
    entry_to: Optional[str]    # None = single entry (or whole section)
    display: str               # human form, e.g. "Berakhot 3" or "Sanhedrin 97b"


def parse_reference(text: str) -> Optional[JudaicaReference]:
    """Extract the first Judaica reference from ``text``; None if none."""
    if not data_available():
        # Sichtbar degradieren (Projekt-Regel: keine stillen Fallbacks):
        # ohne _index.json bleibt nur die thematische Suche.
        from ....lib.logging_utils import log_message
        log_message(
            "judaica: _index.json missing — reference lookup unavailable, "
            "falling back to thematic search", "warning",
        )
        return None
    m = _pattern().search(text)
    if not m:
        return None
    key = _name_to_key().get(normalize_name(m.group("work")))
    if key is None:
        return None
    work_name = _index()[key]["names"][0]
    section = m.group("sec")
    amud = m.group("amud").lower() if m.group("amud") else None
    e1 = m.group("e1")
    e2 = m.group("e2")
    # An a/b suffix only carries meaning for Talmud-Bavli works (Daf
    # sections). On a Chapter-section work like Mishnah or Tanakh the user
    # didn't really mean "amud" — drop it rather than carrying a stale flag.
    if amud and _index()[key]["section_type"] != "Daf":
        amud = None
    display = f"{work_name} {section}{amud or ''}"
    if e1:
        display += f",{e1}" + (f"-{e2}" if e2 else "")
    return JudaicaReference(key, work_name, section, amud, e1, e2, display)


def lookup(ref: JudaicaReference) -> dict:
    """Resolve a reference to source text. Returns a result dict.

    On success: ``{reference, work, section, section_type, entry_type,
    entries:[…]}``. On a missing section/entry: ``{reference, error}``.
    """
    meta = _index()[ref.work_key]
    data = _work_json(meta["json"])
    # section_type kommt aus dem _index.json — dieselbe Quelle, die auch
    # parse_reference nutzt (eine Wahrheit; Index deckt alle Werke ab).
    section_type = meta["section_type"]
    # Talmud Bavli citations come in as "97b" (Vilna folio + amud); the
    # Sefaria-style JSON we ship lists every amud as a fortlaufender daf
    # (97b → 194). Translate before the section lookup so users can cite
    # the way they're used to, while the data layer stays 1:1 with Sefaria.
    section_key = (
        _vilna_to_sefaria_daf(int(ref.section), ref.amud)
        if ref.amud and section_type == "Daf"
        else ref.section
    )
    section = data["sections"].get(section_key)
    if not section:
        return {"reference": ref.display,
                "error": f"{ref.work_name} has no "
                         f"{section_type} {ref.section}{ref.amud or ''}"}

    if ref.entry_from is None:
        wanted = sorted(section, key=int)
    elif ref.entry_to is None:
        wanted = [ref.entry_from]
    else:
        # Iterate the section's ACTUAL entries within the span — never
        # range(from, to+1): an unbounded entry_to from the parser would
        # materialize a giant list and OOM the worker.
        lo, hi = int(ref.entry_from), int(ref.entry_to)
        # int(n) direkt (fail-loud bei korrupten Entry-Keys) — gleiche
        # Strategie wie der Ganz-Sektion-Zweig oben, kein isdigit-Filter.
        wanted = sorted(
            (n for n in section if lo <= int(n) <= hi),
            key=int,
        )

    entries = [{"nr": n, "text": section[n]} for n in wanted if n in section]
    if not entries:
        return {"reference": ref.display,
                "error": f"{ref.work_name} {ref.section} has no such "
                         f"{data['entry_type'].lower()}(s)"}
    return {
        "reference": ref.display,
        "work": data["work"],
        "section": ref.section,
        "section_type": data["section_type"],
        "entry_type": data["entry_type"],
        "entries": entries,
    }


def resolve(text: str) -> Optional[dict]:
    """Parse a reference from ``text`` and look it up in one step.

    Returns the lookup result dict, or None if ``text`` contains no
    recognizable Judaica reference.
    """
    ref = parse_reference(text)
    return lookup(ref) if ref else None
