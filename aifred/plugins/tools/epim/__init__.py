"""EPIM database plugin (EssentialPIM). Every module — calendar, contacts,
notes, todos, passwords — is switched off, readable or writable on its own
in the plugin settings; tools and instructions follow those settings."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ....lib.function_calling import Tool
from ....lib.plugin_base import (
    CredentialField,
    PluginContext,
    plugin_dir_of,
    plugin_i18n_text,
)
from ....lib.plugin_modules import module_credential_fields, module_instructions
from .settings import (
    DB_PATH_KEY,
    MODULE_DEFAULTS,
    SETTINGS_PREFIX,
    load_raw_settings,
    parse_settings,
    save_raw_settings,
)

# Placeholders in the module instructions, filled from the database.
_LOOKUP_PLACEHOLDERS = {
    "{epim_categories}": "categories",
    "{epim_calendars}": "calendars",
    "{epim_todolists}": "todolists",
    "{epim_notetrees}": "notetrees",
    "{epim_contact_fields}": "contact_fields",
}
_WRITE_TOOLS = frozenset({"epim_create", "epim_update", "epim_delete"})


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
            *module_credential_fields(SETTINGS_PREFIX, MODULE_DEFAULTS, "epim_cred_module_{module}"),
        ]

    def _load_settings(self) -> dict[str, str]:
        return load_raw_settings()

    def _save_settings(self, settings: dict[str, str]) -> None:
        """Set the database file's access mode from the modules, then store the
        settings: read-only unless a module is writable, so the engine itself
        refuses every write. A failed switch saves nothing."""
        from .db import get_epim_db
        from .maintenance import set_access_mode
        parsed = parse_settings(settings)
        current = get_epim_db()
        if current is not None:
            # The access mode can only change while nobody is attached.
            current.close()
        if parsed.db_path is not None:
            set_access_mode(parsed.db_path, read_only=not parsed.modules.any_write)
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
        """Tool fragments for the offered and granted tools, then one fragment
        per readable module (``prompts/<lang>/modules/<module>.txt``) and its
        write fragment (``<module>.write.txt``) when the module is writable
        and a write tool is granted."""
        from ....lib.plugin_base import load_plugin_instructions
        from ....lib.prompt_loader import render_standard_placeholders
        from .db import get_epim_db
        from .tools import get_epim_tools
        db = get_epim_db()
        if db is None:
            return ""
        offered = {t.name for t in get_epim_tools(db, source="browser")}
        granted = offered if granted_tools is None else granted_tools & offered
        text = load_plugin_instructions(self, lang, granted)
        if not text:
            return ""
        lang_code = "de" if str(lang).startswith("de") else "en"
        parts = [text]
        if db.can_read("calendar") or db.can_read("todos"):
            # Categories serve two modules, so they are no module fragment of their own.
            categories = Path(__file__).parent / "prompts" / lang_code / "modules" / "categories.txt"
            parts.append(categories.read_text(encoding="utf-8").strip())
        parts += module_instructions(Path(__file__).parent, lang_code, db.modules, bool(granted & _WRITE_TOOLS))
        text = "\n\n".join(parts)
        lookups = db.lookups()
        for placeholder, key in _LOOKUP_PLACEHOLDERS.items():
            if key in lookups:
                text = text.replace(placeholder, ", ".join(e.name for e in lookups[key]))
        return render_standard_placeholders(text, lang_code)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name not in ("epim_search", "epim_get", *_WRITE_TOOLS):
            return ""
        lang_code = "de" if str(lang).startswith("de") else "en"
        entity = str(tool_args.get("entity_type", ""))
        query = str(tool_args.get("query") or "") if tool_name == "epim_search" else ""
        key = "tool_epim_search_bare" if tool_name == "epim_search" and not query else f"tool_{tool_name}"
        return plugin_i18n_text(plugin_dir_of(self), key, lang_code).format(entity=entity, query=query[:40])


plugin = EpimPlugin()
