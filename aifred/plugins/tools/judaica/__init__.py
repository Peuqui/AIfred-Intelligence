"""Judaica plugin — thematic search over the Jewish source corpus.

Exposes one self-contained tool, ``search_judaica``: a thematic vector
search restricted to the judaica folder — Tanakh, Talmud, Mishnah,
Midrash, Halacha and the classic Torah commentaries (Rashi, Ramban,
Ibn Ezra), everything indexed under documents/judaica/. It reuses the
shared document_store search via file_manager, so there is no
duplicated vector logic and no plugin-to-plugin call — the tool stays
atomic.

Unlike the bible plugin there is no exact reference lookup: the Sefaria
texts use heterogeneous citation systems (Daf/Line for the Talmud,
Chapter/Verse for the Tanakh, ...). A structured-JSON reference lookup
is tracked as a follow-up ("Stufe B") in TODO.md.
"""
import json
from dataclasses import dataclass
from typing import Any

from ....lib.config import DATA_DIR
from ....lib.function_calling import Tool
from ....lib.logging_utils import log_message
from ....lib.plugin_base import PluginContext
from ....lib.security import TIER_READONLY

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
        "Judaica-Zugriff: thematische Suche im jüdischen Quellkorpus — "
        "Tanach, Talmud, Mischna, Midrasch und die klassischen "
        "Tora-Kommentare (Rashi, Ramban, Ibn Esra)."
    )

    def is_available(self) -> bool:
        return _JUDAICA_DIR.is_dir()

    def get_tools(self, ctx: PluginContext) -> list[Tool]:

        async def _execute(query: str) -> str:
            log_message(f"📜 search_judaica: {query}")

            # Thematic vector search restricted to the judaica folder.
            # Reuses the shared document search (the library function,
            # not the search_documents tool).
            from ....lib import file_manager as fm
            result = await fm.search_index(
                query, n_results=8, folder=_JUDAICA_FOLDER
            )
            if not result.success:
                return json.dumps({"error": result.detail}, ensure_ascii=False)
            hits = result.metadata.get("results", [])
            return json.dumps({
                "query": query,
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
                    "Search the Jewish source corpus (Judaica): the "
                    "Tanakh, the Babylonian Talmud, the Mishnah, Midrash, "
                    "Halacha and the classic Torah commentaries (Rashi, "
                    "Ramban, Ibn Ezra). Runs a thematic search and returns "
                    "related passages — the Hebrew original paired with "
                    "its translation. Use this for anything from Jewish "
                    "scripture or rabbinic literature; use search_bible for "
                    "the Christian Bible and search_documents for other "
                    "documents."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "A topic or question (e.g. 'Schabbat-Gebote', "
                                "'Was sagt der Talmud über Umkehr?')"
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
