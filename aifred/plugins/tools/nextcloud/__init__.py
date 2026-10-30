"""Nextcloud plugin (CalDAV/CardDAV/WebDAV): appointments, tasks, contacts, notes and files.
Independent of every other plugin; each module is switched off, readable or
writable on its own, and writing also needs Nextcloud's own write right."""

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
    AGENT_FOLDER_DEFAULT,
    AGENT_FOLDER_KEY,
    APP_PASSWORD_KEY,
    MODULE_DEFAULTS,
    NOTES_FOLDER_DEFAULT,
    NOTES_FOLDER_KEY,
    SETTINGS_PREFIX,
    URL_KEY,
    USER_KEY,
    load_raw_settings,
    parse_settings,
    save_raw_settings,
)

_TOOLS = ("nextcloud_search", "nextcloud_get", "nextcloud_create", "nextcloud_update", "nextcloud_delete")
_FILE_TOOLS = ("nextcloud_file_list", "nextcloud_file_read", "nextcloud_file_write", "nextcloud_file_move",
               "nextcloud_file_delete")
_WRITE_TOOLS = frozenset({*_TOOLS[2:], *_FILE_TOOLS[2:]})


@dataclass
class NextcloudPlugin:
    name: str = "nextcloud"

    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(env_key=URL_KEY, label_key="nextcloud_cred_url",
                            placeholder="https://cloud.example.org"),
            CredentialField(env_key=USER_KEY, label_key="nextcloud_cred_user", placeholder="aifred"),
            CredentialField(env_key=APP_PASSWORD_KEY, label_key="nextcloud_cred_app_password", is_password=True),
            *module_credential_fields(SETTINGS_PREFIX, MODULE_DEFAULTS, "nextcloud_cred_module_{module}"),
            CredentialField(env_key=AGENT_FOLDER_KEY, label_key="nextcloud_cred_agent_folder",
                            default=AGENT_FOLDER_DEFAULT),
            CredentialField(env_key=NOTES_FOLDER_KEY, label_key="nextcloud_cred_notes_folder",
                            default=NOTES_FOLDER_DEFAULT),
        ]

    def _load_settings(self) -> dict[str, str]:
        return load_raw_settings()

    def _save_settings(self, settings: dict[str, str]) -> None:
        from .store import reset_store
        parse_settings(settings)  # an invalid access level saves nothing
        save_raw_settings(settings)
        reset_store()

    def is_available(self) -> bool:
        from .store import get_store
        return get_store() is not None

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        from .store import get_store
        from .tools import get_nextcloud_tools
        store = get_store()
        return [] if store is None else get_nextcloud_tools(store)

    @property
    def document_source(self) -> Any:
        """The files as a source of the document manager (lib.document_sources), while the
        module is on; writable only at write level."""
        from .source import NextcloudDocumentSource
        from .store import get_store
        store = get_store()
        if store is None or not store.modules.can_read("files"):
            return None
        return NextcloudDocumentSource(store.client, writable=store.modules.can_write("files"))

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        """Tool fragments for the offered and granted tools, then the
        fragments of the readable (and, with a write tool, writable) modules."""
        from ....lib.plugin_base import load_plugin_instructions
        from ....lib.prompt_loader import render_standard_placeholders
        from .store import get_store
        from .tools import get_nextcloud_tools
        store = get_store()
        if store is None:
            return ""
        offered = {t.name for t in get_nextcloud_tools(store)}
        granted = offered if granted_tools is None else granted_tools & offered
        text = load_plugin_instructions(self, lang, granted)
        if not text:
            return ""
        lang_code = "de" if str(lang).startswith("de") else "en"
        parts = [text, *module_instructions(Path(__file__).parent, lang_code, store.modules,
                                            bool(granted & _WRITE_TOOLS))]
        return render_standard_placeholders("\n\n".join(parts), lang_code)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        lang_code = "de" if str(lang).startswith("de") else "en"
        if tool_name in _FILE_TOOLS:
            path = str(tool_args.get("path") or tool_args.get("query") or tool_args.get("folder") or "")
            return plugin_i18n_text(plugin_dir_of(self), f"tool_{tool_name}", lang_code).format(path=path[:60])
        if tool_name not in _TOOLS:
            return ""
        entity = str(tool_args.get("entity_type", ""))
        query = str(tool_args.get("query") or "") if tool_name == "nextcloud_search" else ""
        key = "tool_nextcloud_search_bare" if tool_name == "nextcloud_search" and not query else f"tool_{tool_name}"
        return plugin_i18n_text(plugin_dir_of(self), key, lang_code).format(entity=entity, query=query[:40])


plugin = NextcloudPlugin()
