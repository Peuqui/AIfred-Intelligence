"""Judaica plugin — exact reference lookup + thematic search over the
Jewish source corpus.

Bundles two access paths into one self-contained tool, ``search_judaica``:

- a named reference ("Berakhot 3", "Pirkei Avot 1,1", "Rashi zu
  Genesis 1,1") -> the exact source text from the structured Judaica
  JSON (see reference.py / scripts/build_judaica_json.py);
- any other query -> a thematic vector search restricted to the judaica
  folder — Tanakh, Talmud, Mishnah, Midrash, Halacha and the classic
  Torah commentaries (Rashi, Ramban, Ibn Ezra). It reuses the shared
  document_store search via file_manager, so there is no duplicated
  vector logic and no plugin-to-plugin call — the tool stays atomic.

The reference lookup needs the structured index built by
scripts/build_judaica_json.py; without it every query simply falls
through to the thematic search.
"""
import json
from dataclasses import dataclass
from typing import Any

from ....lib.config import DATA_DIR
from ....lib.function_calling import Tool
from ....lib.logging_utils import log_message
from ....lib.plugin_base import PluginContext
from ....lib.security import TIER_READONLY
from .reference import resolve

# All Judaica source texts live under this folder. file_manager's
# folder filter is a recursive prefix-match, so "judaica" covers the
# sub-folders (judaica/talmud, judaica/tanakh/tora, ...) as well.
_JUDAICA_FOLDER = "judaica"
_JUDAICA_DIR = DATA_DIR / "documents" / _JUDAICA_FOLDER


@dataclass
class JudaicaPlugin:
    name: str = "judaica"
    display_name: str = "Judaica"
    description: str = (
        "Judaica-Zugriff: exakter Stellen-Lookup (z. B. Berakhot 3) und "
        "thematische Suche im jüdischen Quellkorpus — Tanach, Talmud, "
        "Mischna, Midrasch und die klassischen Tora-Kommentare."
    )

    def is_available(self) -> bool:
        return _JUDAICA_DIR.is_dir()

    def get_tools(self, ctx: PluginContext) -> list[Tool]:

        async def _execute(query: str) -> str:
            log_message(f"📜 search_judaica: {query}")

            # Path 1 — a named reference: exact source text.
            hit = resolve(query)
            if hit is not None:
                return json.dumps(hit, ensure_ascii=False)

            # Path 2 — a topical query: thematic vector search restricted
            # to the judaica folder. Reuses the shared document search
            # (the library function, not the search_documents tool).
            from ....lib import file_manager as fm
            result = await fm.search_index(
                query, n_results=8, folder=_JUDAICA_FOLDER
            )
            if not result.success:
                return json.dumps({"error": result.detail}, ensure_ascii=False)
            hits = result.metadata.get("results", [])
            return json.dumps({
                "query": query,
                "mode": "thematic",
                "results": [
                    {"text": h.get("content", ""), "filename": h.get("filename", "")}
                    for h in hits
                ],
            }, ensure_ascii=False)

        return [
            Tool(
                name="search_judaica",
                tier=TIER_READONLY,
                description=(
                    "Search the Jewish source corpus (Judaica). Two modes, "
                    "chosen automatically from the query: (1) a named "
                    "passage — 'Berakhot 3', 'Pirkei Avot 1,1', 'Rashi zu "
                    "Genesis 1,1' — returns the exact source text (Hebrew "
                    "original + translation); (2) any other (topical) "
                    "query runs a thematic search over the Tanakh, Talmud, "
                    "Mishnah, Midrash, Halacha and the classic Torah "
                    "commentaries (Rashi, Ramban, Ibn Ezra). Use this for "
                    "anything from Jewish scripture or rabbinic literature; "
                    "use search_bible for the Christian Bible and "
                    "search_documents for other documents."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "A reference (e.g. 'Berakhot 3', 'Pirkei "
                                "Avot 1,1') or a topic (e.g. 'Was sagt der "
                                "Talmud über Umkehr?')"
                            ),
                        },
                    },
                    "required": ["query"],
                },
                executor=_execute,
            ),
        ]

    def get_prompt_instructions(self, lang: str) -> str:
        return ""

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name == "search_judaica":
            return f"📜 {tool_args.get('query', '')}"
        return ""


plugin = JudaicaPlugin()
