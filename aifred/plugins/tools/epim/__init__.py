"""EPIM database plugin (epim_search, epim_create, epim_update, epim_delete)."""

from dataclasses import dataclass
from typing import Any

from ....lib.function_calling import Tool
from ....lib.i18n import t
from ....lib.plugin_base import CredentialField, PluginContext


@dataclass
class EpimPlugin:
    name: str = "epim"
    display_name: str = "EPIM"
    description: str = "Lese- und Schreibzugriff auf den persönlichen EPIM-Datenbestand: Termine, Kontakte, Notizen, Aufgaben."

    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                env_key="EPIM_DB_PATH",
                label_key="epim_cred_db_path",
                placeholder="/path/to/database.epim",
            ),
        ]

    def is_available(self) -> bool:
        from ....lib.config import EPIM_ENABLED
        if not EPIM_ENABLED:
            return False
        from .db import get_epim_db
        return get_epim_db() is not None

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        from .tools import get_epim_tools
        return get_epim_tools(lang=ctx.lang)

    def get_prompt_instructions(self, lang: str) -> str:
        from ....lib.prompt_loader import load_prompt
        return load_prompt("shared/epim_instructions", lang=lang) or ""

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name == "epim_search":
            entity = tool_args.get("entity_type", "")
            query = tool_args.get("query", "")
            if query:
                return t("tool_epim_search", lang=lang, entity=entity, query=query[:40])
            return t("tool_epim_search_bare", lang=lang, entity=entity) if entity else "📅 EPIM..."
        elif tool_name == "epim_create":
            return t("tool_epim_create", lang=lang, entity=tool_args.get("entity_type", ""))
        elif tool_name == "epim_update":
            return t("tool_epim_update", lang=lang, entity=tool_args.get("entity_type", ""))
        elif tool_name == "epim_delete":
            return t("tool_epim_delete", lang=lang, entity=tool_args.get("entity_type", ""))
        return ""


plugin = EpimPlugin()
