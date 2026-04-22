"""Google Suite Plugin — Orchestrator für Calendar und Contacts.

Aktiviert Sub-Services via settings.json (GOOGLE_CALENDAR_ENABLED,
GOOGLE_CONTACTS_ENABLED). OAuth-Flow über den generischen OAuthBroker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ....lib.function_calling import Tool
from ....lib.plugin_base import CredentialField, PluginContext

_SETTINGS_PATH = Path(__file__).parent / "settings.json"
_I18N_PATH = Path(__file__).parent / "i18n.json"

# Scopes pro Sub-Service
_SCOPES: dict[str, str] = {
    "GOOGLE_CALENDAR_ENABLED": "https://www.googleapis.com/auth/calendar",
    "GOOGLE_CONTACTS_ENABLED": "https://www.googleapis.com/auth/contacts",
    "GOOGLE_TASKS_ENABLED":    "https://www.googleapis.com/auth/tasks",
}


@dataclass
class GooglePlugin:
    name: str = "google"
    display_name: str = "Google Suite"

    # ── Settings ────────────────────────────────────────────────

    def _load_settings(self) -> dict[str, str]:
        if _SETTINGS_PATH.exists():
            with open(_SETTINGS_PATH, encoding="utf-8") as f:
                data: dict[str, str] = json.load(f)
                return data
        return {}

    def _save_settings(self, settings: dict[str, str]) -> None:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)

    def _translate(self, key: str, lang: str = "de") -> str:
        if _I18N_PATH.exists():
            with open(_I18N_PATH, encoding="utf-8") as f:
                i18n: dict[str, dict[str, str]] = json.load(f)
            entry = i18n.get(key, {})
            return entry.get(lang) or entry.get("de") or key
        return key

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
                default="true",
                options=[("true", "Aktiviert"), ("false", "Deaktiviert")],
                group="services",
            ),
            CredentialField(
                env_key="GOOGLE_CONTACTS_ENABLED",
                label_key="google_contacts_enabled",
                default="true",
                options=[("true", "Aktiviert"), ("false", "Deaktiviert")],
                group="services",
            ),
            CredentialField(
                env_key="GOOGLE_TASKS_ENABLED",
                label_key="google_tasks_enabled",
                default="false",
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

        if settings.get("GOOGLE_CALENDAR_ENABLED", "true") == "true":
            from .calendar.tools import get_calendar_tools
            tools.extend(get_calendar_tools(ctx.lang))

        if settings.get("GOOGLE_CONTACTS_ENABLED", "true") == "true":
            from .contacts.tools import get_contacts_tools
            tools.extend(get_contacts_tools(ctx.lang))

        if settings.get("GOOGLE_TASKS_ENABLED", "false") == "true":
            from .tasks.tools import get_tasks_tools
            tools.extend(get_tasks_tools(ctx.lang))

        return tools

    def get_prompt_instructions(self, lang: str) -> str:
        settings = self._load_settings()
        parts: list[str] = []

        if settings.get("GOOGLE_CALENDAR_ENABLED", "true") == "true":
            if lang == "de":
                parts.append(
                    "Du hast Zugriff auf Google Calendar. "
                    "Nutze google_calendar_list_events für Terminabfragen, "
                    "google_calendar_create_event um neue Termine anzulegen, "
                    "google_calendar_update_event zum Ändern und "
                    "google_calendar_delete_event zum Löschen. "
                    "Zeitangaben müssen im RFC 3339 Format sein (z.B. 2026-04-22T10:00:00+02:00)."
                )
            else:
                parts.append(
                    "You have access to Google Calendar. "
                    "Use google_calendar_list_events to query events, "
                    "google_calendar_create_event to create, "
                    "google_calendar_update_event to modify, "
                    "google_calendar_delete_event to delete. "
                    "Timestamps must be RFC 3339 (e.g. 2026-04-22T10:00:00+02:00)."
                )

        if settings.get("GOOGLE_CONTACTS_ENABLED", "true") == "true":
            if lang == "de":
                parts.append(
                    "Du hast Zugriff auf Google Contacts. "
                    "Nutze google_contacts_search um Kontakte nach Name oder E-Mail zu suchen "
                    "(z.B. um Empfänger für E-Mails aufzulösen)."
                )
            else:
                parts.append(
                    "You have access to Google Contacts. "
                    "Use google_contacts_search to find contacts by name or email."
                )

        if settings.get("GOOGLE_TASKS_ENABLED", "false") == "true":
            if lang == "de":
                parts.append(
                    "Du hast Zugriff auf Google Tasks. "
                    "Nutze google_tasks_list um Aufgaben abzurufen, "
                    "google_tasks_create um neue anzulegen, "
                    "google_tasks_complete um sie als erledigt zu markieren und "
                    "google_tasks_delete zum Löschen. "
                    "Mit google_tasks_list_tasklists siehst du alle vorhandenen Listen."
                )
            else:
                parts.append(
                    "You have access to Google Tasks. "
                    "Use google_tasks_list to retrieve tasks, "
                    "google_tasks_create to create new ones, "
                    "google_tasks_complete to mark them done, "
                    "google_tasks_delete to remove them. "
                    "Use google_tasks_list_tasklists to see all task lists."
                )

        return "\n\n".join(parts)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        status_map = {
            "google_calendar_list_events":    "tool_list_events",
            "google_calendar_create_event":   "tool_create_event",
            "google_calendar_update_event":   "tool_update_event",
            "google_calendar_delete_event":   "tool_delete_event",
            "google_calendar_list_calendars": "tool_list_calendars",
            "google_contacts_list_all":        "tool_list_all_contacts",
            "google_contacts_list_groups":     "tool_list_groups",
            "google_contacts_list_by_group":   "tool_list_by_group",
            "google_contacts_search":          "tool_search_contacts",
            "google_contacts_create":          "tool_create_contact",
            "google_contacts_update":          "tool_update_contact",
            "google_contacts_delete":          "tool_delete_contact",
            "google_tasks_list_tasklists":     "tool_list_tasklists",
            "google_tasks_list":               "tool_list_tasks",
            "google_tasks_create":             "tool_create_task",
            "google_tasks_update":             "tool_update_task",
            "google_tasks_complete":           "tool_complete_task",
            "google_tasks_delete":             "tool_delete_task",
        }
        key = status_map.get(tool_name)
        if key:
            return self._translate(key, lang)
        return ""

    def aggregated_scopes(self) -> list[str]:
        """Alle Scopes der aktiven Sub-Services — für den OAuth-Flow."""
        settings = self._load_settings()
        return [
            scope
            for key, scope in _SCOPES.items()
            if settings.get(key, "true") == "true"
        ]


plugin = GooglePlugin()
