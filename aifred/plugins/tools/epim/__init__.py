"""EPIM database plugin (EssentialPIM): epim_search and epim_get, plus
epim_create, epim_update and epim_delete while the write switch is on."""

from dataclasses import dataclass
from typing import Any

from ....lib.function_calling import Tool
from ....lib.plugin_base import (
    CredentialField,
    PluginContext,
    plugin_dir_of,
    plugin_i18n_text,
)
from .settings import (
    DB_PATH_KEY,
    WRITE_ENABLED_DEFAULT,
    WRITE_ENABLED_KEY,
    load_raw_settings,
    parse_settings,
    save_raw_settings,
)

# Placeholders in the plugin instructions, filled from the database.
_LOOKUP_PLACEHOLDERS = {
    "{epim_categories}": "categories",
    "{epim_calendars}": "calendars",
    "{epim_todolists}": "todolists",
    "{epim_notetrees}": "notetrees",
    "{epim_contact_fields}": "contact_fields",
}


@dataclass
class EpimPlugin:
    name: str = "epim"

    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                env_key=DB_PATH_KEY,
                label_key="epim_cred_db_path",
                placeholder="/path/to/database.epim",
            ),
            CredentialField(
                env_key=WRITE_ENABLED_KEY,
                label_key="epim_cred_write_enabled",
                default=WRITE_ENABLED_DEFAULT,
                options=[("false", "epim_write_off"), ("true", "epim_write_on")],
            ),
        ]

    def _load_settings(self) -> dict[str, str]:
        return load_raw_settings()

    def _save_settings(self, settings: dict[str, str]) -> None:
        """Switch the database file's access mode with the write switch, then
        store the settings. The file is read-only while writing is off, so the
        engine itself refuses every write. A failed switch saves nothing."""
        from .db import get_epim_db
        from .maintenance import set_access_mode
        parsed = parse_settings(settings)
        current = get_epim_db()
        if current is not None:
            # The access mode can only change while nobody is attached.
            current.close()
        if parsed.db_path is not None:
            set_access_mode(parsed.db_path, read_only=not parsed.write_enabled)
        save_raw_settings(settings)

    def is_available(self) -> bool:
        from .db import get_epim_db
        db = get_epim_db()
        return db is not None and db.db_path.exists()

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        from .db import get_epim_db
        from .tools import get_epim_tools
        db = get_epim_db()
        if db is None:
            return []
        return get_epim_tools(db, source=ctx.source)

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        from ....lib.plugin_base import load_plugin_instructions
        from ....lib.prompt_loader import render_standard_placeholders
        from .db import get_epim_db
        from .tools import READ_TOOL_NAMES, WRITE_TOOL_NAMES
        db = get_epim_db()
        if db is None:
            return ""
        # Instructions for write tools only while they are offered.
        offered = READ_TOOL_NAMES | (WRITE_TOOL_NAMES if db.write_enabled else frozenset())
        granted = set(offered) if granted_tools is None else granted_tools & offered
        text = load_plugin_instructions(self, lang, granted)
        if not text:
            return ""
        lookups = db.lookups()
        for placeholder, key in _LOOKUP_PLACEHOLDERS.items():
            text = text.replace(placeholder, ", ".join(e.name for e in lookups[key]))
        return render_standard_placeholders(text, "de" if str(lang).startswith("de") else "en")

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        from .tools import READ_TOOL_NAMES, WRITE_TOOL_NAMES
        if tool_name not in READ_TOOL_NAMES | WRITE_TOOL_NAMES:
            return ""
        lang_code = "de" if str(lang).startswith("de") else "en"
        entity = str(tool_args.get("entity_type", ""))
        query = str(tool_args.get("query") or "") if tool_name == "epim_search" else ""
        key = "tool_epim_search_bare" if tool_name == "epim_search" and not query else f"tool_{tool_name}"
        return plugin_i18n_text(plugin_dir_of(self), key, lang_code).format(entity=entity, query=query[:40])


plugin = EpimPlugin()
