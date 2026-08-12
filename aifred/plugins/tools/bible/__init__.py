"""Bible plugin — exact scripture lookup + thematic search over the Bible.

Bundles two access paths into one self-contained tool, ``search_bible``:

- a named reference ("Psalm 5", "Joh 3,16", "1. Mose 1,1-5") -> the exact
  verse text from the structured Bible JSON (see reference.py);
- any other query -> a thematic vector search restricted to the bible
  folder, reusing the shared document_store search via file_manager — no
  duplicated vector logic, no plugin-to-plugin call.

Which translation the reference lookup uses is a plugin setting
(``BIBLE_TRANSLATION`` — a sub-folder of data/documents/bibel/), shown
as a dropdown in the plugin's settings. All Bible specifics (the
reference parser, the translation, the book-alias tables) stay
encapsulated here; the generic search_documents tool stays free of any
Bible knowledge.
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ....lib.function_calling import Tool
from ....lib.logging_utils import log_message
from ....lib.plugin_base import CredentialField, PluginContext, load_tool_description
from ....lib.security import TIER_READONLY
from .reference import (
    BIBLE_FOLDER,
    BIBLE_ROOT,
    TRANSLATION_ENV_KEY,
    available_translations,
    reload,
    resolve,
)


@dataclass
class BiblePlugin:
    name: str = "bible"
    display_name: str = "Bibel"
    description: str = (
        "Bibel-Zugriff: exakter Stellen-Lookup (z. B. Psalm 5) und "
        "thematische Suche in der Bibel."
    )

    # ── Plugin settings (settings.json next to this module) ──────────
    @property
    def credential_fields(self) -> list[CredentialField]:
        """One dropdown: which translation the reference lookup uses.
        Options are the translation sub-folders of data/documents/bibel/."""
        translations = available_translations()
        return [
            CredentialField(
                env_key=TRANSLATION_ENV_KEY,
                label_key="bible_cred_translation",
                options=[(t, t) for t in translations],
                default=translations[0] if translations else "",
            ),
        ]

    def _settings_path(self) -> Path:
        return Path(__file__).parent / "settings.json"

    def _load_settings(self) -> dict:
        """Load the plugin's settings.json (empty dict if none)."""
        path = self._settings_path()
        if not path.is_file():
            return {}
        with open(path, encoding="utf-8") as f:
            return dict(json.load(f))

    def _save_settings(self, settings: dict) -> None:
        """Persist the plugin's settings.json. The active translation
        may have changed, so the reference lookup's cache is dropped."""
        with open(self._settings_path(), "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        reload()

    def is_available(self) -> bool:
        # Ordner-Check wie judaica: die thematische Vektor-Suche funktioniert
        # auch ohne Übersetzungs-JSON; die Referenz-Auflösung degradiert
        # dann sichtbar (Log in parse_reference).
        return BIBLE_ROOT.is_dir()

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
            result = await fm.search_index(query, n_results=8, folder=BIBLE_FOLDER)
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
                    load_tool_description(__file__, "search_bible")
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

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        # Kein Hardcoding — atomare Fragmente in prompts/<de|en>/ beim Plugin.
        from ....lib.plugin_base import load_plugin_instructions
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name == "search_bible":
            return f"📖 {tool_args.get('query', '')}"
        return ""


plugin = BiblePlugin()

# Load the saved translation choice into os.environ at import time, so
# the reference lookup and the settings modal both see it after a
# restart (tool plugins have no automatic settings-to-env step).
for _key, _val in plugin._load_settings().items():
    if _val:
        os.environ[_key] = _val
