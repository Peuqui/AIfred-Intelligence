"""Bible plugin — exact scripture lookup + thematic search over the Bible.

Bundles two access paths into one self-contained tool, ``search_bible``:

- a named reference ("Psalm 5", "Joh 3,16", "1. Mose 1,1-5") -> the exact
  verse text from the structured Bible JSON (see reference.py);
- any other query -> a thematic vector search restricted to the bible
  folder, reusing the shared document_store search via file_manager — no
  duplicated vector logic, no plugin-to-plugin call.

All Bible specifics (translation, JSON path, book names, the reference
parser) are encapsulated here; the generic search_documents tool stays
free of any Bible knowledge.
"""
import json
from dataclasses import dataclass
from typing import Any

from ....lib.function_calling import Tool
from ....lib.logging_utils import log_message
from ....lib.plugin_base import PluginContext
from ....lib.security import TIER_READONLY
from .reference import data_available, resolve


@dataclass
class BiblePlugin:
    name: str = "bible"
    display_name: str = "Bibel"
    description: str = (
        "Bibel-Zugriff: exakter Stellen-Lookup (z. B. Psalm 5) und "
        "thematische Suche in der Bibel."
    )

    def is_available(self) -> bool:
        return data_available()

    def get_tools(self, ctx: PluginContext) -> list[Tool]:

        async def _execute(query: str) -> str:
            log_message(f"📖 search_bible: {query}")

            # Path 1 — a named scripture reference: exact verse text.
            hit = resolve(query)
            if hit is not None:
                return json.dumps(hit, ensure_ascii=False)

            # Path 2 — a topical query: thematic vector search restricted
            # to the bible folder. Reuses the shared document search (the
            # library function, not the search_documents tool).
            from ....lib import file_manager as fm
            result = await fm.search_index(query, n_results=8, folder="bibel")
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
                name="search_bible",
                tier=TIER_READONLY,
                description=(
                    "Search the Bible. Two modes, chosen automatically from "
                    "the query: (1) a named passage — 'Psalm 5', 'Joh 3,16', "
                    "'1. Mose 1,1-5' — returns the exact verse text; "
                    "(2) any other (topical) query runs a thematic search "
                    "and returns related verses. Use this for anything "
                    "Bible-related; use search_documents for other documents."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "A scripture reference (e.g. 'Psalm 5', "
                                "'Joh 3,16') or a topic (e.g. 'Verse über Trost')"
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
        if tool_name == "search_bible":
            return f"📖 {tool_args.get('query', '')}"
        return ""


plugin = BiblePlugin()
