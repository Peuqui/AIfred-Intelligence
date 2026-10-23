"""Google Suite Plugin — Orchestrator für Calendar, Contacts, Tasks und Drive.

Jeder Dienst steht in der settings.json auf Aus, Lesen oder Lesen und
Schreiben (lib.plugin_modules). Die Stufe setzt AIfred selbst durch: bei
„Lesen“ bietet er nur die lesenden Werkzeuge an, und die Drive-Quelle nimmt
keine Uploads an. Bei Google fordert er für jeden eingeschalteten Dienst das
volle Recht an — so wechselt die Stufe ohne Neuverbinden; nur ein Dienst, dem
Google noch nicht zugestimmt hat, braucht eine neue Zustimmung (bis dahin ist
das Plugin aus). OAuth-Flow über den generischen OAuthBroker.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ....lib.function_calling import Tool
from ....lib.plugin_base import CredentialField, PluginContext, load_plugin_i18n
from ....lib.plugin_modules import READ, ModuleAccess, module_credential_fields

SETTINGS_PREFIX = "GOOGLE"
MODULE_DEFAULTS: dict[str, str] = {
    "calendar": READ,
    "contacts": READ,
    "tasks": READ,
    "drive": READ,
}
# Berechtigung je Dienst — unabhängig von der Stufe (die setzt AIfred durch).
_SCOPES: dict[str, str] = {
    "calendar": "https://www.googleapis.com/auth/calendar",
    "contacts": "https://www.googleapis.com/auth/contacts",
    "tasks": "https://www.googleapis.com/auth/tasks",
    "drive": "https://www.googleapis.com/auth/drive",
}
# User-Identität immer mitscopen (sonst gibt Google nichts zurück).
_IDENTITY_SCOPES = ("https://www.googleapis.com/auth/userinfo.email",
                    "https://www.googleapis.com/auth/userinfo.profile")

# The one Drive folder the agents may work in (drive/agent_folder.py); the
# document manager is not bound to it.
_DRIVE_AGENT_FOLDER_DEFAULT = "AIfred-Intelligence"


@functools.lru_cache(maxsize=1)
def _load_i18n() -> dict[str, dict[str, str]]:
    """i18n.json einmal laden (wurde vorher bei JEDEM Status-Aufruf neu von
    Platte gelesen). Änderung der Datei braucht einen Neustart — UI-Texte
    ändern sich nicht zur Laufzeit."""
    return load_plugin_i18n(Path(__file__).parent)


def _service_tools(service: str, settings: dict[str, str]) -> list[Tool]:
    if service == "calendar":
        from .calendar import tools as calendar_tools
        return _limit(calendar_tools.get_calendar_tools(), calendar_tools.READ_TOOL_NAMES, settings, service)
    if service == "contacts":
        from .contacts import tools as contacts_tools
        return _limit(contacts_tools.get_contacts_tools(), contacts_tools.READ_TOOL_NAMES, settings, service)
    if service == "tasks":
        from .tasks import tools as tasks_tools
        return _limit(tasks_tools.get_tasks_tools(), tasks_tools.READ_TOOL_NAMES, settings, service)
    from .drive import tools as drive_tools
    folder = settings.get("GOOGLE_DRIVE_AGENT_FOLDER", _DRIVE_AGENT_FOLDER_DEFAULT)
    return _limit(drive_tools.get_drive_tools(folder), drive_tools.READ_TOOL_NAMES, settings, service)


def _limit(tools: list[Tool], read_names: frozenset[str], settings: dict[str, str], service: str) -> list[Tool]:
    if _modules(settings).can_write(service):
        return tools
    return [t for t in tools if t.name in read_names]


def _modules(settings: dict[str, str]) -> ModuleAccess:
    return ModuleAccess.parse(settings, SETTINGS_PREFIX, MODULE_DEFAULTS)


@dataclass
class GooglePlugin:
    # MUST equal the folder name — the registry, enable/disable (directory
    # move), toggles and the Plugin-Manager UI all key on the folder name
    # (get_tool_plugin matches plugin.name against it). A mismatch makes the
    # plugin invisible to the UI (no gear/lightbulb, OAuth connect unreachable).
    name: str = "google_suite"
    oauth_provider: str = "google"

    # ── Settings ────────────────────────────────────────────────

    def _load_settings(self) -> dict[str, str]:
        # lib-SSOT (plugin_base), geteilt mit bible
        from ....lib.plugin_base import load_plugin_settings
        return load_plugin_settings(__file__)

    def _save_settings(self, settings: dict[str, str]) -> None:
        from ....lib.plugin_base import save_plugin_settings
        _modules(settings)  # an invalid level saves nothing
        save_plugin_settings(__file__, settings)

    def _translate(self, key: str, lang: str = "de") -> str:
        entry = _load_i18n().get(key, {})
        return entry.get(lang) or entry.get("de") or key

    def modules(self) -> ModuleAccess:
        return _modules(self._load_settings())

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
            *module_credential_fields(SETTINGS_PREFIX, MODULE_DEFAULTS, "google_cred_module_{module}",
                                      group="services"),
            CredentialField(
                env_key="GOOGLE_DRIVE_AGENT_FOLDER",
                label_key="google_drive_agent_folder",
                default=_DRIVE_AGENT_FOLDER_DEFAULT,
            ),
        ]

    def is_available(self) -> bool:
        """Connected, with a right for every service that is switched on."""
        from ....lib.credential_broker import broker
        if not broker.get("google", "client_id"):
            return False
        from ....lib.oauth.broker import oauth_broker
        if not oauth_broker.is_connected("google"):
            return False
        return self.oauth_grant_current(oauth_broker.granted_scopes("google"))

    def oauth_grant_current(self, granted: list[str]) -> bool:
        """True when Google granted every right the switched-on services
        need. Further rights do no harm: the levels are enforced by AIfred."""
        return set(self._service_scopes()) <= set(granted)

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        settings = self._load_settings()
        modules = _modules(settings)
        tools: list[Tool] = []
        for service in MODULE_DEFAULTS:
            if modules.can_read(service):
                tools.extend(_service_tools(service, settings))
        return tools

    @property
    def document_source(self) -> Any:
        """Drive as a second source of the document manager (lib.document_sources)
        — while the Drive service is on; writable only at write level."""
        modules = self.modules()
        if not modules.can_read("drive"):
            return None
        from .drive.source import DriveDocumentSource
        return DriveDocumentSource(writable=modules.can_write("drive"))

    def get_prompt_instructions(self, lang: str, granted_tools: set[str] | None = None) -> str:
        # Kein Hardcoding — atomare Fragmente in prompts/<de|en>/ beim Plugin;
        # nur für die Werkzeuge, die die Dienst-Stufen anbieten.
        from ....lib.plugin_base import load_plugin_instructions
        offered = {t.name for t in self.get_tools(PluginContext(agent_id="_prompt", lang=lang, session_id=""))}
        granted = offered if granted_tools is None else granted_tools & offered
        return load_plugin_instructions(self, lang, granted)

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
        """Berechtigungen der eingeschalteten Dienste — für den OAuth-Flow."""
        return [*self._service_scopes(), *_IDENTITY_SCOPES]

    def _service_scopes(self) -> list[str]:
        modules = self.modules()
        return [_SCOPES[service] for service in MODULE_DEFAULTS if modules.can_read(service)]


plugin = GooglePlugin()
