"""Base classes for the AIfred plugin system.

Two plugin types:
- ToolPlugin: Provides tools the LLM can call (search, EPIM, sandbox, ...)
- BaseChannel: Receives/sends messages via external services (email, Discord, ...)

Both are discovered automatically from aifred/plugins/tools/ and
aifred/plugins/channels/ by the unified registry.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path
    from ..lib.envelope import InboundMessage, OutboundMessage
    from ..lib.function_calling import Tool


# ============================================================
# TOOL PLUGIN
# ============================================================

@dataclass
class PluginContext:
    """Context passed to tool plugins when creating tools."""

    agent_id: str
    lang: str
    session_id: str
    state: Optional[Any] = None
    user_query: str = ""
    max_tier: int = 4        # Max allowed tool tier in this context
    source: str = "browser"  # Origin: browser/email/discord/cron/webhook
    llm_history: list = field(default_factory=list)  # Conversation history for tools


@runtime_checkable
class ToolPlugin(Protocol):
    """Protocol that every tool plugin must satisfy."""

    name: str
    display_name: str

    def is_available(self) -> bool:
        """Check if this plugin can run right now (config flags, services, etc.)."""
        ...

    def get_tools(self, ctx: "PluginContext") -> list["Tool"]:
        """Return Tool instances bound to the given context."""
        ...

    def get_prompt_instructions(self, lang: str) -> str:
        """Return prompt text for the LLM system prompt. Empty string if none."""
        ...

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        """Return UI status string while this tool executes. Empty string if not owned."""
        ...


# ============================================================
# CHANNEL PLUGIN
# ============================================================

@dataclass
class CredentialField:
    """Describes a single credential field for the Settings UI.

    If placeholder is set but default is not, placeholder is used as default.
    This prevents the UI showing a hint that looks like a value but saves empty.

    Storage:
        is_secret=True  → stored in .env (passwords, tokens, API keys)
        is_secret=False → stored in plugin's settings.json (ports, paths, engines)
    """

    env_key: str            # Environment variable name (also used as settings.json key)
    label_key: str          # i18n key for the label
    placeholder: str = ""
    default: str = ""
    is_password: bool = False
    is_secret: bool = False  # True = .env, False = plugin settings.json
    width_ratio: int = 1    # Relative width in a row
    group: str = ""         # Group fields into one hstack
    options: list[tuple[str, str]] | None = None  # If set, render as dropdown: [(value, label), ...]

    def __post_init__(self) -> None:
        if self.placeholder and not self.default:
            self.default = self.placeholder
        # Passwords are always secrets
        if self.is_password:
            self.is_secret = True


class BaseChannel(ABC):
    """Abstract base class for all message channel plugins."""

    # ── Plugin Settings (settings.json in plugin directory) ────

    def _settings_path(self) -> "Path":
        """Path to this plugin's settings.json."""
        from pathlib import Path
        return Path(__file__).parent.parent / "plugins" / "channels" / f"{self.name}_channel" / "settings.json"

    def load_settings(self) -> dict[str, str]:
        """Load plugin settings from settings.json. Returns empty dict if not found."""
        import json
        path = self._settings_path()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data: dict[str, str] = json.load(f)
                return data
        return {}

    def save_settings(self, settings: dict[str, str]) -> None:
        """Save plugin settings to settings.json."""
        import json
        path = self._settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)

    def get_setting(self, key: str) -> str:
        """Get a single setting value. Returns empty string if not set."""
        return self.load_settings().get(key, "")

    def set_setting(self, key: str, value: str) -> None:
        """Set a single setting value (read-modify-write)."""
        settings = self.load_settings()
        settings[key] = value
        self.save_settings(settings)

    # ── Plugin i18n (i18n.json in plugin directory) ────────────

    def _i18n_path(self) -> "Path":
        """Path to this plugin's i18n.json."""
        from pathlib import Path
        return Path(__file__).parent.parent / "plugins" / "channels" / f"{self.name}_channel" / "i18n.json"

    def load_i18n(self) -> dict[str, dict[str, str]]:
        """Load plugin translations from i18n.json.

        Format: {"key": {"de": "Deutsch", "en": "English"}, ...}
        """
        import json
        path = self._i18n_path()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data: dict[str, dict[str, str]] = json.load(f)
                return data
        return {}

    def translate(self, key: str, lang: str = "de") -> str:
        """Translate a key using this plugin's i18n.json. Falls back to key itself."""
        translations = self.load_i18n()
        entry = translations.get(key)
        if entry:
            return entry.get(lang, entry.get("de", key))
        return key

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        ...

    @property
    @abstractmethod
    def icon(self) -> str:
        ...

    @property
    @abstractmethod
    def credential_fields(self) -> list[CredentialField]:
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        ...

    @abstractmethod
    def apply_credentials(self, values: dict[str, str]) -> None:
        ...

    @abstractmethod
    async def listener_loop(self) -> None:
        ...

    @abstractmethod
    async def send_reply(self, outbound: "OutboundMessage", original: "InboundMessage") -> None:
        ...

    @abstractmethod
    def build_context(self, message: "InboundMessage") -> str:
        ...

    @property
    def always_reply(self) -> bool:
        """If True, auto-reply is always on (no toggle shown in UI).

        Override to return True for channels where a reply is always
        expected (e.g. Discord, Telegram). Default: False (toggle shown).
        """
        return False

    @property
    def has_allowlist(self) -> bool:
        """If True, an allowlist row is shown in the UI.

        Override to return False for channels that don't need sender
        filtering (e.g. FreeEcho.2 — local hardware, no external senders).
        """
        return True

    def build_reply_metadata(self, message: "InboundMessage") -> dict:
        return {}

    def channel_log(self, msg: str, level: str = "info") -> None:
        """Log to log file, stderr (→ journalctl), and the live browser
        debug console when a ``session_scope`` is active.

        Use this for all channel lifecycle messages (connect, disconnect,
        errors, received/sent messages) so they survive worker restarts.
        During a hub-driven pipeline (session_scope set) the message is
        also mirrored into the browser debug console so it shows live —
        matching what the terminal log file captures.
        """
        from .logging_utils import log_message
        log_message(msg, level)
        print(f"[{self.name}] {msg}", file=sys.stderr, flush=True)

        # Mirror into the active session's debug console (if any).
        # No-op outside a session_scope — keeps channel startup /
        # shutdown logs out of the browser console.
        from .debug_bus import _current_session, _flush_to_session
        sid = _current_session.get()
        if sid:
            from datetime import datetime
            ts = datetime.now().strftime("%H:%M:%S")
            _flush_to_session(sid, [f"{ts} | {msg}"])

    def load_settings_to_env(self) -> None:
        """Load plugin settings.json into os.environ at boot time.

        settings.json has priority over .env for non-secret values.
        This ensures the latest saved config is used, not stale .env entries.
        Runs at plugin discovery, after migration.
        """
        import os
        settings = self.load_settings()
        for key, value in settings.items():
            if value:
                os.environ[key] = value

    def get_tools(self, ctx: "PluginContext") -> list["Tool"]:
        """Optional: Return tools this channel provides for LLM function calling.

        Override to expose channel-specific tools (e.g. discord_send).
        Default: no tools.
        """
        return []
