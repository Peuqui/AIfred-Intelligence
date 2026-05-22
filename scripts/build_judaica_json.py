#!/usr/bin/env python3
"""Build structured JSON from the Judaica Markdown texts.

The Sefaria texts under data/documents/judaica/ are uniform Markdown:

    # <work title>
    _Source: <name>_
    ## <SectionType> <nr>      (Chapter / Daf / Psalm / Paragraph)
    ### <EntryType> <nr>       (Verse / Line / Mishnah / Comment / …)
    <Hebrew original>
    <translation>

This script parses every ``*.txt`` into a structured ``*.json`` next to
it, and writes one ``_index.json`` listing every work with the names
the reference lookup recognises it by. The JSON is the data source for
the exact scripture-style lookup in
aifred/plugins/tools/judaica/reference.py — the counterpart to the
thematic vector search.

data/ is not version-controlled, so this script is the reproducible way
to (re)create the lookup data after the texts are (re)downloaded with
download_judaica.py.

Usage:  python scripts/build_judaica_json.py
"""
import json
import re
from pathlib import Path

_JUDAICA = (
    Path(__file__).resolve().parent.parent / "data" / "documents" / "judaica"
)
_INDEX = _JUDAICA / "_index.json"

_SECTION = re.compile(r"^##\s+(.+?)\s+(\d+)\s*$")
_ENTRY = re.compile(r"^###\s+(.+?)\s+(\d+)\s*$")


def _parse(path: Path) -> dict:
    """Parse one Judaica Markdown file into a structured dict."""
    work = ""
    source = ""
    section_type = ""
    entry_type = ""
    sections: dict[str, dict[str, str]] = {}
    cur_sec: str | None = None
    cur_entry: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if cur_sec is not None and cur_entry is not None:
            sections[cur_sec][cur_entry] = "\n".join(buf).strip()

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            work = line[2:].strip()
            continue
        if line.startswith("_Source:"):
            source = line[len("_Source:"):].strip().strip("_").strip()
            continue
        m_sec = _SECTION.match(line)
        if m_sec:
            flush()
            buf = []
            section_type = section_type or m_sec.group(1)
            cur_sec = m_sec.group(2)
            sections.setdefault(cur_sec, {})
            cur_entry = None
            continue
        m_ent = _ENTRY.match(line)
        if m_ent:
            flush()
            buf = []
            entry_type = entry_type or m_ent.group(1)
            cur_entry = m_ent.group(2)
            continue
        if cur_entry is not None:
            buf.append(line)
    flush()

    return {
        "work": work,
        "source": source,
        "section_type": section_type,
        "entry_type": entry_type,
        "sections": sections,
    }


def _names(source: str, stem: str) -> list[str]:
    """Recognition names for a work: the Sefaria source, the file stem,
    and — for commentaries ("Rashi on Genesis") — German/spaced forms."""
    names = {source}
    if " on " in source:
        names.add(source.replace(" on ", " "))
        names.add(source.replace(" on ", " zu "))
    names.add(re.sub(r"^\d+_", "", stem).replace("_", " "))
    return sorted(n for n in names if n)


def _norm(name: str) -> str:
    """Normalize a recognition name: lowercase, no spaces/dots/commas."""
    return re.sub(r"[.\s,]", "", name).lower()


def _resolve_collisions(index: dict) -> None:
    """Drop ambiguous recognition names in place.

    When a normalized name maps to several works it is kept only for the
    work whose source name *is* that name — so the Talmud tractate wins
    "Sanhedrin" over the Mishnah's "Mishnah Sanhedrin"; the loser keeps
    its other, unambiguous names. A name no work owns as its source is
    dropped from every work.
    """
    norm_to_keys: dict[str, set] = {}
    for key, entry in index.items():
        for name in entry["names"]:
            norm_to_keys.setdefault(_norm(name), set()).add(key)
    for nrm, keys in norm_to_keys.items():
        if len(keys) < 2:
            continue
        owner = next(
            (k for k in keys if _norm(index[k]["source"]) == nrm), None
        )
        for key in keys:
            if key != owner:
                index[key]["names"] = [
                    n for n in index[key]["names"] if _norm(n) != nrm
                ]
        kept = index[owner]["work"] if owner else "— (verworfen)"
        print(f"  ⚠️  mehrdeutig {nrm!r} → {kept}")


def main() -> None:
    if not _JUDAICA.is_dir():
        raise SystemExit(f"Judaica folder not found: {_JUDAICA}")

    index: dict[str, dict] = {}
    total_entries = 0

    for txt in sorted(_JUDAICA.rglob("*.txt")):
        key = txt.relative_to(_JUDAICA).with_suffix("").as_posix()
        data = _parse(txt)
        if not data["sections"]:
            print(f"  ⚠️  {key}: keine Abschnitte erkannt — übersprungen")
            continue
        out_json = txt.with_suffix(".json")
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        n_entries = sum(len(s) for s in data["sections"].values())
        total_entries += n_entries
        index[key] = {
            "json": out_json.relative_to(_JUDAICA).as_posix(),
            "work": data["work"],
            "source": data["source"],
            "names": _names(data["source"], txt.stem),
            "section_type": data["section_type"],
            "entry_type": data["entry_type"],
        }
        print(f"  {key}: {len(data['sections'])} {data['section_type']}, "
              f"{n_entries} {data['entry_type']}")

    _resolve_collisions(index)
    with open(_INDEX, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)

    print(f"\n{len(index)} Werke, {total_entries} Einträge")
    print(f"  → {_INDEX}")


if __name__ == "__main__":
    main()
