#!/usr/bin/env python3
"""Download priority 1 + 2 Judaica texts from sefaria.org and save as
plain-text files suitable for AIfred's RAG-Index.

Sefaria API returns a JSON tree (chapter -> verse/mishnah) with HTML
markup (``<b>``, ``<i>``) embedded in the English translations. This
script flattens the tree into a single file per text with section
headers (``## Chapter 1``, ``### Mishnah 3``) and strips HTML so the
embedder sees clean prose.

Output: data/documents/judaica/<tradition>/<text>.txt
Tradition layout:
    judaica/
        pirkei_avot.txt
        mishnah/{sanhedrin,avodah_zarah}.txt
        talmud/{chagigah,berakhot,sanhedrin}.txt
        midrash/{bereshit_rabbah,tehillim}.txt

Run:
    venv/bin/python scripts/download_judaica.py
"""

from __future__ import annotations

import re
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = REPO_ROOT / "data" / "documents" / "judaica"
SEFARIA_API_V3 = "https://www.sefaria.org/api/v3/texts/{ref}"
SEFARIA_API_V1 = "https://www.sefaria.org/api/texts/{ref}?lang=en&context=0&pad=0"
USER_AGENT = "AIfred-Intelligence/1.0 (+research)"
REQUEST_DELAY_SEC = 1.5  # be polite to the API


@dataclass
class TextSpec:
    title: str          # Sefaria slug (URL part)
    display_name: str   # human-readable header
    output_path: Path   # relative to OUTPUT_ROOT


# Mainstream-anerkannte Texte ohne Kabbala (Sohar, Sefer Yetzira, Tanya
# bewusst weggelassen — esoterische Lehren, Indexierung problematisch).
# Halacha-Codices (Mishneh Torah, Schulchan Aruch) ebenfalls nicht
# enthalten — riesig, eher als Spezial-Pakete sinnvoll.
TEXTS: list[TextSpec] = [
    # === Mischna ===
    TextSpec("Pirkei_Avot", "Pirkei Avot (Sprueche der Vaeter)",
             OUTPUT_ROOT / "pirkei_avot.txt"),
    TextSpec("Mishnah_Sanhedrin", "Mischna Sanhedrin",
             OUTPUT_ROOT / "mishnah" / "sanhedrin.txt"),
    TextSpec("Mishnah_Avodah_Zarah", "Mischna Avodah Zarah",
             OUTPUT_ROOT / "mishnah" / "avodah_zarah.txt"),

    # === Talmud Bavli ===
    TextSpec("Berakhot", "Talmud Bavli — Berakhot (Segenssprueche)",
             OUTPUT_ROOT / "talmud" / "berakhot.txt"),
    TextSpec("Shabbat", "Talmud Bavli — Shabbat",
             OUTPUT_ROOT / "talmud" / "shabbat.txt"),
    TextSpec("Pesachim", "Talmud Bavli — Pesachim (Pessach)",
             OUTPUT_ROOT / "talmud" / "pesachim.txt"),
    TextSpec("Yoma", "Talmud Bavli — Yoma (Versoehnungstag)",
             OUTPUT_ROOT / "talmud" / "yoma.txt"),
    TextSpec("Sukkah", "Talmud Bavli — Sukkah (Laubhuettenfest)",
             OUTPUT_ROOT / "talmud" / "sukkah.txt"),
    TextSpec("Rosh_Hashanah", "Talmud Bavli — Rosh Hashanah (Neujahr)",
             OUTPUT_ROOT / "talmud" / "rosh_hashanah.txt"),
    TextSpec("Chagigah", "Talmud Bavli — Chagigah (Festopfer/Mystik)",
             OUTPUT_ROOT / "talmud" / "chagigah.txt"),
    TextSpec("Sanhedrin", "Talmud Bavli — Sanhedrin (Gericht/Eschatologie)",
             OUTPUT_ROOT / "talmud" / "sanhedrin.txt"),
    TextSpec("Makkot", "Talmud Bavli — Makkot (Geisselstrafe)",
             OUTPUT_ROOT / "talmud" / "makkot.txt"),
    TextSpec("Avodah_Zarah", "Talmud Bavli — Avodah Zarah (Goetzendienst)",
             OUTPUT_ROOT / "talmud" / "avodah_zarah.txt"),
    TextSpec("Chullin", "Talmud Bavli — Chullin (Speisegesetze)",
             OUTPUT_ROOT / "talmud" / "chullin.txt"),

    # === Midrash Rabba (alle 5 Buecher Mose) + Psalmen ===
    TextSpec("Bereshit_Rabbah", "Midrash Bereshit Rabbah (Genesis)",
             OUTPUT_ROOT / "midrash" / "bereshit_rabbah.txt"),
    TextSpec("Shemot_Rabbah", "Midrash Shemot Rabbah (Exodus)",
             OUTPUT_ROOT / "midrash" / "shemot_rabbah.txt"),
    TextSpec("Vayikra_Rabbah", "Midrash Vayikra Rabbah (Levitikus)",
             OUTPUT_ROOT / "midrash" / "vayikra_rabbah.txt"),
    TextSpec("Bamidbar_Rabbah", "Midrash Bamidbar Rabbah (Numeri)",
             OUTPUT_ROOT / "midrash" / "bamidbar_rabbah.txt"),
    TextSpec("Devarim_Rabbah", "Midrash Devarim Rabbah (Deuteronomium)",
             OUTPUT_ROOT / "midrash" / "devarim_rabbah.txt"),
    TextSpec("Midrash_Tehillim", "Midrash Tehillim (Psalmen)",
             OUTPUT_ROOT / "midrash" / "tehillim.txt"),

    # === Rashi-Kommentar zur Tora ===
    TextSpec("Rashi_on_Genesis", "Rashi zu Genesis",
             OUTPUT_ROOT / "kommentare" / "rashi_genesis.txt"),
    TextSpec("Rashi_on_Exodus", "Rashi zu Exodus",
             OUTPUT_ROOT / "kommentare" / "rashi_exodus.txt"),
    TextSpec("Rashi_on_Leviticus", "Rashi zu Levitikus",
             OUTPUT_ROOT / "kommentare" / "rashi_leviticus.txt"),
    TextSpec("Rashi_on_Numbers", "Rashi zu Numeri",
             OUTPUT_ROOT / "kommentare" / "rashi_numeri.txt"),
    TextSpec("Rashi_on_Deuteronomy", "Rashi zu Deuteronomium",
             OUTPUT_ROOT / "kommentare" / "rashi_deuteronomium.txt"),

    # === Mussar (Ethik) ===
    TextSpec("Mesilat_Yesharim", "Mesilat Yesharim (Pfad der Aufrichtigen)",
             OUTPUT_ROOT / "ethik" / "mesilat_yesharim.txt"),
    # Chovot HaLevavot weggelassen — Sefaria-Schema ist "complex":
    # Buch ist in 10 "Treatises" (Tore) gegliedert, Buch-Level-Ref nicht
    # unterstuetzt. Bei Bedarf einzeln referenzieren wie
    # "Duties_of_the_Heart,_First_Treatise" etc.
]


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t]+")


def strip_html(text: str) -> str:
    """Remove inline HTML tags and collapse runs of whitespace."""
    cleaned = _HTML_TAG_RE.sub("", text)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    return cleaned.strip()


MIN_DE_SECTIONS = 5  # below this Sefaria's German version is treated as
                     # incomplete (e.g. Yoma 2026-05) and we fall back to English


def _count_sections(text_tree: Any) -> int:
    """Rough estimate of how many top-level sections (chapters / daf) a
    text payload contains. Used to detect partial German translations."""
    if isinstance(text_tree, list):
        return sum(1 for x in text_tree if x)  # non-empty entries only
    return 1 if text_tree else 0


def fetch_text(
    slug: str, session: requests.Session,
) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    """Fetch a Sefaria text in translation + Hebrew original.

    Returns ``(translation_payload, hebrew_payload_or_None, lang)`` where
    ``lang`` is ``"de"`` or ``"en"`` for the translation. Hebrew is fetched
    additionally so the rendered file can pair each verse with its original
    — important for Rabbi-style quoting and cross-language search via the
    multilingual bge-m3 embedder.

    German is preferred when sufficiently complete; otherwise the English
    default version. Hebrew failures are non-fatal — a translation-only
    file is still useful.
    """
    quoted = urllib.parse.quote(slug, safe="_")
    translation_payload: dict[str, Any] | None = None
    lang = "en"

    # Try German via v3
    try:
        v3 = session.get(
            SEFARIA_API_V3.format(ref=quoted),
            params={"version": "german"},
            timeout=120,
        )
        if v3.status_code == 200:
            data = v3.json()
            versions = data.get("versions", [])
            if versions and versions[0].get("text"):
                payload = _normalize_v3(data, versions[0])
                if _count_sections(payload.get("text")) >= MIN_DE_SECTIONS:
                    translation_payload = payload
                    lang = "de"
    except (requests.RequestException, ValueError):
        pass

    # English fallback
    if translation_payload is None:
        v1 = session.get(SEFARIA_API_V1.format(ref=quoted), timeout=120)
        v1.raise_for_status()
        translation_payload = v1.json()
        lang = "en"

    # Hebrew original (parallel)
    hebrew_payload: dict[str, Any] | None = None
    try:
        v3h = session.get(
            SEFARIA_API_V3.format(ref=quoted),
            params={"version": "hebrew"},
            timeout=120,
        )
        if v3h.status_code == 200:
            data = v3h.json()
            versions = data.get("versions", [])
            if versions and versions[0].get("text"):
                hebrew_payload = _normalize_v3(data, versions[0])
    except (requests.RequestException, ValueError):
        pass  # Hebrew is optional

    return translation_payload, hebrew_payload, lang


def _normalize_v3(data: dict[str, Any], version: dict[str, Any]) -> dict[str, Any]:
    """Reshape v3 response so render_flat_text can consume it like v1."""
    return {
        "text": version.get("text"),
        "ref": data.get("ref", ""),
        "heRef": data.get("heRef", ""),
        "sectionNames": data.get("sectionNames")
                       or version.get("sectionNames")
                       or ["Section", "Verse"],
    }


def _normalize_2d(tree: Any) -> list:
    """Lift 1D trees to 2D for uniform iteration."""
    if isinstance(tree, list) and tree and not isinstance(tree[0], list):
        return [tree]
    return tree if isinstance(tree, list) else []


def _verse_at(tree: list, chap_idx: int, verse_idx: int) -> str | None:
    """Get verse_idx-th entry of chap_idx-th chapter (1-based), or None."""
    if 0 <= chap_idx - 1 < len(tree):
        chapter = tree[chap_idx - 1]
        if isinstance(chapter, list) and 0 <= verse_idx - 1 < len(chapter):
            entry = chapter[verse_idx - 1]
            if isinstance(entry, list):
                return " ".join(strip_html(v) for v in entry if v).strip() or None
            elif isinstance(entry, str):
                return strip_html(entry) or None
    return None


def render_flat_text(
    payload: dict[str, Any],
    display_name: str,
    lang: str,
    hebrew_payload: dict[str, Any] | None = None,
) -> str:
    """Convert Sefaria's nested 'text' field to a flat readable document.

    Pairs each verse with its Hebrew original when ``hebrew_payload`` is
    provided. The pairing assumes both versions share the same chapter/
    verse layout — Sefaria normalizes this for canonical works.
    """
    text_tree = payload.get("text")
    section_names = payload.get("sectionNames") or ["Section", "Verse"]
    he_ref = payload.get("heRef") or ""
    en_ref = payload.get("ref") or ""
    hebrew_tree: list = []
    if hebrew_payload is not None:
        hebrew_tree = _normalize_2d(hebrew_payload.get("text"))

    lines: list[str] = []
    lines.append(f"# {display_name}")
    lang_label = "Deutsch" if lang == "de" else "Englisch"
    he_note = " + Hebraeisch (Original)" if hebrew_tree else ""
    lines.append(f"_Sprache: {lang_label}{he_note}_  ")
    if en_ref:
        lines.append(f"_Source: {en_ref}_  ")
    if he_ref:
        lines.append(f"_Hebrew ref: {he_ref}_")
    lines.append("")

    if not text_tree:
        lines.append("(empty — text not available via this endpoint)")
        return "\n".join(lines)

    text_tree = _normalize_2d(text_tree)
    chapter_label = section_names[0] if section_names else "Chapter"
    verse_label = section_names[1] if len(section_names) > 1 else "Verse"

    for chap_idx, chapter in enumerate(text_tree, start=1):
        if not chapter:
            continue
        lines.append(f"## {chapter_label} {chap_idx}")
        lines.append("")
        if isinstance(chapter, str):
            cleaned = strip_html(chapter)
            heb = _verse_at(hebrew_tree, chap_idx, 1) if hebrew_tree else None
            if heb:
                lines.append(heb)
            lines.append(cleaned)
            lines.append("")
            continue
        for verse_idx, verse in enumerate(chapter, start=1):
            if isinstance(verse, list):
                joined = " ".join(strip_html(v) for v in verse if v)
                if joined:
                    lines.append(f"### {verse_label} {verse_idx}")
                    heb = _verse_at(hebrew_tree, chap_idx, verse_idx) if hebrew_tree else None
                    if heb:
                        lines.append(heb)
                    lines.append(joined)
                    lines.append("")
            elif isinstance(verse, str):
                cleaned = strip_html(verse)
                if cleaned:
                    lines.append(f"### {verse_label} {verse_idx}")
                    heb = _verse_at(hebrew_tree, chap_idx, verse_idx) if hebrew_tree else None
                    if heb:
                        lines.append(heb)
                    lines.append(cleaned)
                    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_index(results: list[tuple[TextSpec, str, float, bool]]) -> None:
    """Write an INDEX.md inside OUTPUT_ROOT so an agent (or human) can
    see at a glance what is available, in which language, and where.

    Grouped by category derived from the output sub-directory. Each row
    shows whether the verse-by-verse Hebrew original is paired into the
    file — this is what makes Hebrew citations searchable via bge-m3.
    """
    by_dir: dict[str, list[tuple[TextSpec, str, float, bool]]] = {}
    for spec, lang, kb, has_he in results:
        rel = spec.output_path.relative_to(OUTPUT_ROOT)
        category = rel.parent.as_posix() if rel.parent.as_posix() != "." else ""
        by_dir.setdefault(category, []).append((spec, lang, kb, has_he))

    total = len(results)
    de = sum(1 for _, lang, _, _ in results if lang == "de")
    en = total - de
    he = sum(1 for _, _, _, has_he in results if has_he)
    de_he = sum(1 for _, lang, _, has_he in results if lang == "de" and has_he)
    en_he = sum(1 for _, lang, _, has_he in results if lang == "en" and has_he)

    def _pct(n: int) -> str:
        return f"{(100 * n / total):.0f}%" if total else "—"

    lines: list[str] = []
    lines.append("# Judaica — Verfuegbare Quelltexte")
    lines.append("")
    lines.append(
        "Diese Sammlung wurde automatisch von sefaria.org heruntergeladen "
        "(siehe scripts/download_judaica.py). Jeder Vers ist mit dem "
        "hebraeischen Original gepaart, soweit Sefaria es liefert — "
        "die Uebersetzung steht direkt darunter. Deutsche Versionen "
        "(Goldschmidt-Talmud, Berliner Mischnajot, Rashi-DE) wurden "
        "bevorzugt; sonst Englisch."
    )
    lines.append("")
    lines.append("**Sprach-Paarung (pro Vers):**")
    lines.append("")
    lines.append(f"- Hebraeisch + Deutsch: **{de_he}/{total}** ({_pct(de_he)})")
    lines.append(f"- Hebraeisch + Englisch: **{en_he}/{total}** ({_pct(en_he)})")
    lines.append(f"- Hebraeisch insgesamt: **{he}/{total}** ({_pct(he)})")
    lines.append(f"- Deutsche Uebersetzung insgesamt: **{de}/{total}** ({_pct(de)})")
    lines.append(f"- Englische Uebersetzung (Fallback): **{en}/{total}** ({_pct(en)})")
    lines.append("")
    lines.append("Suchstrategie: bge-m3 matcht sprachuebergreifend — "
                 "Transliteration (z.B. `zerizin makdimin`) findet die hebraeische "
                 "Originalstelle samt Uebersetzung im selben Chunk.")
    lines.append("")
    lines.append("Folder-Struktur fuer search_documents-Aufrufe:")
    lines.append("")

    category_titles = {
        "": "Mischna (eigenstaendig)",
        "mishnah": "Mischna-Traktate",
        "talmud": "Talmud Bavli",
        "midrash": "Midrasch",
        "kommentare": "Tora-Kommentare",
        "ethik": "Mussar (Ethik)",
    }

    for category in sorted(by_dir):
        title = category_titles.get(category, category)
        folder_path = f"judaica/{category}" if category else "judaica"
        lines.append(f"## {title}")
        lines.append(f"`folder=\"{folder_path}\"`")
        lines.append("")
        lines.append("| Datei | Werk | Uebersetzung | Hebr. | Groesse |")
        lines.append("|---|---|---|---|---|")
        for spec, lang, kb, has_he in sorted(by_dir[category], key=lambda x: x[0].output_path.name):
            fname = spec.output_path.name
            lang_label = "DE" if lang == "de" else "EN"
            he_label = "ja" if has_he else "—"
            lines.append(f"| `{fname}` | {spec.display_name} | {lang_label} | {he_label} | {kb:.0f} KB |")
        lines.append("")

    index_path = OUTPUT_ROOT / "INDEX.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Index written: {index_path.relative_to(REPO_ROOT)}")


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    failures: list[tuple[str, str]] = []
    by_lang: dict[str, list[str]] = {"de": [], "en": []}
    no_hebrew: list[str] = []
    pairs: dict[str, list[str]] = {"de+he": [], "en+he": [], "de": [], "en": []}
    results: list[tuple[TextSpec, str, float, bool]] = []

    for i, spec in enumerate(TEXTS, start=1):
        print(f"[{i}/{len(TEXTS)}] {spec.title} -> {spec.output_path.relative_to(REPO_ROOT)}")
        try:
            translation, hebrew, lang = fetch_text(spec.title, session)
            text = render_flat_text(translation, spec.display_name, lang, hebrew)
            spec.output_path.parent.mkdir(parents=True, exist_ok=True)
            spec.output_path.write_text(text, encoding="utf-8")
            kb = spec.output_path.stat().st_size / 1024
            tag = "DE" if lang == "de" else "EN-fallback"
            heb_tag = "+HE" if hebrew is not None else "no-HE"
            print(f"    ok ({kb:.1f} KB, {tag}, {heb_tag})")
            by_lang[lang].append(spec.title)
            if hebrew is None:
                no_hebrew.append(spec.title)
            pair_key = f"{lang}+he" if hebrew is not None else lang
            pairs[pair_key].append(spec.title)
            results.append((spec, lang, kb, hebrew is not None))
        except requests.HTTPError as exc:
            msg = f"HTTP {exc.response.status_code if exc.response else '??'}"
            print(f"    fail: {msg}")
            failures.append((spec.title, msg))
        except Exception as exc:
            print(f"    fail: {exc}")
            failures.append((spec.title, str(exc)))
        time.sleep(REQUEST_DELAY_SEC)

    successes = len(by_lang["de"]) + len(by_lang["en"])
    print()
    if results:
        write_index(results)
        print()
    def _pct(n: int, total: int) -> str:
        return f"{(100 * n / total):.1f}%" if total else "—"

    print(f"Done: {successes}/{len(TEXTS)} succeeded")
    print()
    print("Sprach-Paarung (Vers fuer Vers, Embedding-faehig):")
    print(f"  Hebraeisch + Deutsch:    {len(pairs['de+he']):3d} ({_pct(len(pairs['de+he']), successes)})")
    print(f"  Hebraeisch + Englisch:   {len(pairs['en+he']):3d} ({_pct(len(pairs['en+he']), successes)})")
    print(f"  Nur Deutsch (kein HE):   {len(pairs['de']):3d} ({_pct(len(pairs['de']), successes)})")
    print(f"  Nur Englisch (kein HE):  {len(pairs['en']):3d} ({_pct(len(pairs['en']), successes)})")
    print()
    print(f"  → Hebraeisch-Anteil insgesamt: {successes - len(no_hebrew)}/{successes} ({_pct(successes - len(no_hebrew), successes)})")
    print(f"  → Deutsch-Anteil insgesamt:   {len(by_lang['de'])}/{successes} ({_pct(len(by_lang['de']), successes)})")
    if by_lang["en"]:
        print()
        print("  → Auf Sefaria nicht in Deutsch verfuegbar:")
        for t in by_lang["en"]:
            print(f"    - {t}")
    if no_hebrew:
        print()
        print("  → Ohne Hebraeisch-Original (Sefaria-API lieferte keins):")
        for t in no_hebrew:
            print(f"    - {t}")
    if failures:
        print("Failures:")
        for title, msg in failures:
            print(f"  - {title}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
