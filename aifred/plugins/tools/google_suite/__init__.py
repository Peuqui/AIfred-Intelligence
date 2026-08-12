"""Google Suite Plugin — Orchestrator für Calendar, Contacts, Tasks und Drive.

Aktiviert Sub-Services via settings.json (GOOGLE_CALENDAR_ENABLED,
GOOGLE_CONTACTS_ENABLED, GOOGLE_TASKS_ENABLED, GOOGLE_DRIVE_ENABLED).
OAuth-Flow über den generischen OAuthBroker.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ....lib.function_calling import Tool
from ....lib.plugin_base import CredentialField, PluginContext

_I18N_PATH = Path(__file__).parent / "i18n.json"

# Scopes pro Sub-Service
_SCOPES: dict[str, str] = {
    "GOOGLE_CALENDAR_ENABLED": "https://www.googleapis.com/auth/calendar",
    "GOOGLE_CONTACTS_ENABLED": "https://www.googleapis.com/auth/contacts",
    "GOOGLE_TASKS_ENABLED":    "https://www.googleapis.com/auth/tasks",
    "GOOGLE_DRIVE_ENABLED":    "https://www.googleapis.com/auth/drive",
}

@functools.lru_cache(maxsize=1)
def _load_i18n() -> dict[str, dict[str, str]]:
    """i18n.json einmal laden (wurde vorher bei JEDEM Status-Aufruf neu von
    Platte gelesen). Änderung der Datei braucht einen Neustart — UI-Texte
    ändern sich nicht zur Laufzeit."""
    if _I18N_PATH.exists():
        with open(_I18N_PATH, encoding="utf-8") as f:
            return dict(json.load(f))
    return {}


# SSOT für den Default eines fehlenden *_ENABLED-Keys in settings.json —
# muss in credential_fields, get_tools und aggregated_scopes identisch sein,
# sonst zeigt die UI "Aktiviert"/fordert OAuth-Scopes an, während get_tools
# die Tools nicht lädt (latenter Bug bei frischer Installation).
_SERVICE_ENABLED_DEFAULT = "true"


@dataclass
class GooglePlugin:
    # MUST equal the folder name — the registry, enable/disable (directory
    # move), toggles and the Plugin-Manager UI all key on the folder name
    # (get_tool_plugin matches plugin.name against it). A mismatch makes the
    # plugin invisible to the UI (no gear/lightbulb, OAuth connect unreachable).
    name: str = "google_suite"
    display_name: str = "Google Suite"
    description: str = "Zugriff auf Google Calendar, Kontakte, Tasks und Drive (Lesen und Schreiben — OAuth-2.0-authentifiziert)."
    oauth_provider: str = "google"

    # ── Settings ────────────────────────────────────────────────

    def _load_settings(self) -> dict[str, str]:
        # lib-SSOT (plugin_base), geteilt mit bible
        from ....lib.plugin_base import load_plugin_settings
        return load_plugin_settings(__file__)

    def _save_settings(self, settings: dict[str, str]) -> None:
        from ....lib.plugin_base import save_plugin_settings
        save_plugin_settings(__file__, settings)

    def _translate(self, key: str, lang: str = "de") -> str:
        entry = _load_i18n().get(key, {})
        return entry.get(lang) or entry.get("de") or key

    # ── ToolPlugin Protocol ──────────────────────────────────────

    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                env_key="GOOGLE_CLIENT_ID",
                label_key="google_client_id",
                placeholder="1234567890-abc.apps.googleusercontent.com",
                is_secret=True,
                group="oauth",
            ),
            CredentialField(
                env_key="GOOGLE_CLIENT_SECRET",
                label_key="google_client_secret",
                is_password=True,
                group="oauth",
            ),
            CredentialField(
                env_key="GOOGLE_CALENDAR_ENABLED",
                label_key="google_calendar_enabled",
                default=_SERVICE_ENABLED_DEFAULT,
                options=[("true", "Aktiviert"), ("false", "Deaktiviert")],
                group="services",
            ),
            CredentialField(
                env_key="GOOGLE_CONTACTS_ENABLED",
                label_key="google_contacts_enabled",
                default=_SERVICE_ENABLED_DEFAULT,
                options=[("true", "Aktiviert"), ("false", "Deaktiviert")],
                group="services",
            ),
            CredentialField(
                env_key="GOOGLE_TASKS_ENABLED",
                label_key="google_tasks_enabled",
                default=_SERVICE_ENABLED_DEFAULT,
                options=[("true", "Aktiviert"), ("false", "Deaktiviert")],
                group="services",
            ),
            CredentialField(
                env_key="GOOGLE_DRIVE_ENABLED",
                label_key="google_drive_enabled",
                default=_SERVICE_ENABLED_DEFAULT,
                options=[("true", "Aktiviert"), ("false", "Deaktiviert")],
                group="services",
            ),
        ]

    def is_available(self) -> bool:
        from ....lib.credential_broker import broker
        if not broker.get("google", "client_id"):
            return False
        from ....lib.oauth.broker import oauth_broker
        return oauth_broker.is_connected("google")

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        settings = self._load_settings()
        tools: list[Tool] = []

        if settings.get("GOOGLE_CALENDAR_ENABLED", _SERVICE_ENABLED_DEFAULT) == "true":
            from .calendar.tools import get_calendar_tools
            tools.extend(get_calendar_tools())

        if settings.get("GOOGLE_CONTACTS_ENABLED", _SERVICE_ENABLED_DEFAULT) == "true":
            from .contacts.tools import get_contacts_tools
            tools.extend(get_contacts_tools())

        if settings.get("GOOGLE_TASKS_ENABLED", _SERVICE_ENABLED_DEFAULT) == "true":
            from .tasks.tools import get_tasks_tools
            tools.extend(get_tasks_tools())

        if settings.get("GOOGLE_DRIVE_ENABLED", _SERVICE_ENABLED_DEFAULT) == "true":
            from .drive.tools import get_drive_tools
            tools.extend(get_drive_tools())

        return tools

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        # Kein Hardcoding — atomare Fragmente in prompts/<de|en>/ beim Plugin.
        from ....lib.plugin_base import load_plugin_instructions
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        # i18n-Key wird aus dem Tool-Namen abgeleitet (tool_<tool_name>) —
        # die i18n.json ist damit die EINZIGE Liste der Status-Texte, keine
        # dritte handgepflegte Tool-Namen-Map mehr. Kein Key → kein Status.
        if not tool_name.startswith("google_"):
            return ""
        key = f"tool_{tool_name}"
        label = self._translate(key, lang)
        return "" if label == key else label

    def aggregated_scopes(self) -> list[str]:
        """Alle Scopes der aktiven Sub-Services — für den OAuth-Flow.

        Plus die userinfo-Scopes (email, profile) — die brauchen wir immer,
        damit der OAuth-Flow den User identifizieren kann.
        """
        settings = self._load_settings()
        scopes = [
            scope
            for key, scope in _SCOPES.items()
            if settings.get(key, _SERVICE_ENABLED_DEFAULT) == "true"
        ]
        # User-Identität immer mitscopen (sonst gibt Google nichts zurück)
        scopes.append("https://www.googleapis.com/auth/userinfo.email")
        scopes.append("https://www.googleapis.com/auth/userinfo.profile")
        return scopes


plugin = GooglePlugin()
