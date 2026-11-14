"""Unified plugin registry — discovers channels and tools from subdirectories.

Scans:
- aifred/plugins/channels/ → BaseChannel instances
- aifred/plugins/tools/    → ToolPlugin instances

A plugin is a package folder (``__init__.py``) whose name is the plugin's name.

A tool plugin is switched off by a marker in its folder, ``.disabled``. The marker is machine-local
(gitignored); the code stays where git keeps it, so its tests run and updates reach it while it is
off. Without a marker a plugin is on: a new plugin needs nothing, a deleted one takes its marker along.

A channel has no marker: its main switch is ``monitor`` in the settings (``channel_toggles``, see
``channel_is_on``). Off means off for both: what belongs to the plugin alone stops with it (``plugin_background``)
and nothing reaches it any more (``require_plugin_enabled``, ``require_channel_on``).
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .plugin_base import (
    PLUGIN_DISPLAY_NAME_KEY,
    BaseChannel,
    ToolPlugin,
    plugin_display_name,
    plugin_i18n_text,
)

if TYPE_CHECKING:
    from .function_calling import Tool
    from .plugin_base import PluginContext

logger = logging.getLogger(__name__)


def _plugins_root() -> Path:
    return Path(__file__).parent.parent / "plugins"


DISABLED_MARKER = ".disabled"
_SUBDIRS = {"channel": "channels", "tool": "tools"}
CHANNEL_TOGGLES = "channel_toggles"      # settings.json: per channel its switches (monitor, listener, auto_reply)


def _plugin_dirs(plugin_type: str) -> list[Path]:
    """The plugins of one kind, switched on or off: every package folder in its subdirectory."""
    base = _plugins_root() / _SUBDIRS[plugin_type]
    return [d for d in sorted(base.iterdir()) if (d / "__init__.py").exists() and not d.name.startswith("_")]


def _is_switched_off(plugin_dir: Path) -> bool:
    return (plugin_dir / DISABLED_MARKER).exists()


# ============================================================
# CHANNEL REGISTRY
# ============================================================

_channels: dict[str, BaseChannel] = {}
_channels_discovered = False


def _discover_channels() -> None:
    """Import the channel plugins and collect their BaseChannel instances (on or off: the switch is ``channel_is_on``)."""
    global _channels_discovered
    if _channels_discovered:
        return
    _channels_discovered = True

    from .logging_utils import log_exception, log_message

    for plugin_dir in _plugin_dirs("channel"):
        try:
            mod = importlib.import_module(f".channels.{plugin_dir.name}", package="aifred.plugins")
            # Look for a module-level instance of BaseChannel
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if isinstance(obj, BaseChannel) and obj.name not in _channels:
                    _channels[obj.name] = obj
                    obj.load_settings_to_env()
                    log_message(f"Plugin Registry: channel '{obj.name}' registered")
        except Exception as exc:  # noqa: BLE001 — one broken plugin must not stop the others (plugin atomicity)
            log_exception(f"Plugin Registry: failed to load channel '{plugin_dir.name}'", exc)


def get_channel(name: str) -> BaseChannel | None:
    _discover_channels()
    return _channels.get(name)


def all_channels() -> dict[str, BaseChannel]:
    _discover_channels()
    return dict(_channels)


def channel_switch(name: str, switch: str) -> bool:
    """One of a channel's switches in the settings (``monitor``, ``listener``, ``auto_reply``); off when never set."""
    from .settings import load_settings

    toggles = (load_settings() or {}).get(CHANNEL_TOGGLES) or {}
    return bool((toggles.get(name) or {}).get(switch))


def channel_is_on(name: str) -> bool:
    """The channel exists and its main switch (``monitor``) is on. The one answer for: its listener runs, it offers
    tools, it is offered as a target, something may be sent through it."""
    return get_channel(name) is not None and channel_switch(name, "monitor")


def on_channels() -> dict[str, BaseChannel]:
    """The channels whose main switch is on."""
    return {name: channel for name, channel in all_channels().items() if channel_switch(name, "monitor")}


def plugin_label(plugin: object) -> str:
    """A plugin's name as the user reads it, in the UI language."""
    from .prompt_loader import get_language

    return plugin_display_name(plugin, get_language())


def require_channel_on(name: str) -> None:
    """``Conflict`` (409, in the UI language) for a channel that is switched off; ``Missing`` for none of that name."""
    from .user_error import Conflict, Missing

    channel = get_channel(name)
    if channel is None:
        raise Missing("no_plugin", plugin=name)
    if not channel_switch(name, "monitor"):
        raise Conflict("channel_off", plugin=plugin_label(channel))


# ============================================================
# TOOL PLUGIN REGISTRY
# ============================================================

_tools: list[ToolPlugin] | None = None


def get_tool_plugin(name: str) -> ToolPlugin | None:
    """Get a tool plugin by name."""
    for p in discover_tools():
        if p.name == name:
            return p
    return None


def get_tool_plugin_on_or_off(name: str) -> ToolPlugin | None:
    """A tool plugin by name, switched on or off (its module is imported either way). Only for what has to work
    with the plugin off, its settings form; whatever acts as the plugin asks ``get_tool_plugin``."""
    if _plugin_dir(name) is None:
        return None
    plugin = getattr(importlib.import_module(f".tools.{name}", package="aifred.plugins"), "plugin", None)
    return plugin if isinstance(plugin, ToolPlugin) else None


def discover_tools() -> list[ToolPlugin]:
    """All ToolPlugin instances that are not switched off. Cached."""
    global _tools
    if _tools is not None:
        return _tools

    from .logging_utils import log_exception, log_message

    _tools = []
    for plugin_dir in _plugin_dirs("tool"):
        if _is_switched_off(plugin_dir):
            continue
        try:
            mod = importlib.import_module(f".tools.{plugin_dir.name}", package="aifred.plugins")
        except Exception as exc:  # noqa: BLE001 — one broken plugin must not stop the others (plugin atomicity)
            log_exception(f"Plugin Registry: failed to load tool '{plugin_dir.name}'", exc)
            continue

        plugin = getattr(mod, "plugin", None)
        if plugin is not None and isinstance(plugin, ToolPlugin):
            # Invariant: plugin.name MUST equal the folder name — the UI,
            # toggles and enable/disable all key on the folder, get_tool_plugin
            # matches plugin.name against it. A mismatch silently hides the
            # plugin from the Plugin-Manager (no gear/lightbulb). Warn loudly.
            if plugin.name != plugin_dir.name:
                log_message(
                    f"Plugin Registry: tool '{plugin_dir.name}' declares "
                    f"name='{plugin.name}' — MUST match the folder name, else "
                    f"it is invisible to the Plugin-Manager UI.", "warning"
                )
            _tools.append(plugin)

    log_message(f"Plugin Registry: {len(_tools)} tool plugins: {[p.name for p in _tools]}")
    return _tools


def document_source_owners() -> dict[str, str]:
    """The tool plugin (name) behind each document source key, switched on or off: a plugin names its source in
    ``document_source_key``, so the key is known while the plugin is off (the source itself exists only while on)."""
    owners: dict[str, str] = {}
    for plugin_dir in _plugin_dirs("tool"):
        plugin = get_tool_plugin_on_or_off(plugin_dir.name)
        key = getattr(plugin, "document_source_key", None)
        if key is not None:
            owners[key] = plugin_dir.name
    return owners


def collect_plugin_tools(ctx: PluginContext) -> list[tuple[ToolPlugin | BaseChannel, list[Tool]]]:
    """Every available tool plugin and configured channel with the tools it
    offers in ``ctx`` — the one plugin-selection rule of the toolkit factory.
    Tier and whitelist filters come afterwards, at the caller."""
    collected: list[tuple[ToolPlugin | BaseChannel, list[Tool]]] = []
    for plugin in discover_tools():
        if plugin.is_available():
            collected.append((plugin, plugin.get_tools(ctx)))
    for channel in on_channels().values():
        if channel.is_configured():
            collected.append((channel, channel.get_tools(ctx)))
    return [(owner, tools) for owner, tools in collected if tools]


def tool_groups_on_offer(ctx: PluginContext) -> list[dict[str, Any]]:
    """What an agent can be granted right now, for the agent editor: memory first,
    one group per plugin, the channels together at the end. A plugin switched off
    or not connected offers nothing, so it has no group."""
    from .agent_memory import MEMORY_TOOL_TIERS

    def group(name_de: str, name_en: str, tools: list[tuple[str, int]]) -> dict[str, Any]:
        return {"name_de": name_de, "name_en": name_en, "names": [name for name, _ in tools],
                "tools": [{"name": name, "tier": tier} for name, tier in tools]}

    groups = [group("Memory", "Memory", list(MEMORY_TOOL_TIERS.items()))]
    channel_tools: list[tuple[str, int]] = []
    for owner, tools in collect_plugin_tools(ctx):
        rows = [(tool.name, tool.tier) for tool in tools]
        if isinstance(owner, BaseChannel):
            channel_tools += rows
        else:
            groups.append(group(plugin_display_name(owner, "de"), plugin_display_name(owner, "en"), rows))
    if channel_tools:
        groups.append(group("Channels", "Channels", channel_tools))
    return groups


# ============================================================
# RELOAD (after a tool plugin is switched on or off)
# ============================================================

def _reload_tools() -> None:
    """Forget the loaded tool plugins and discover them again.

    Tool plugins only: the channels stay as they are, their listeners keep running. Until
    06.11.2026 this reloaded the channels too and cancelled their hub workers, which nothing
    started again — every switch in the Plugin Manager took mail, Telegram, Discord, the Echo
    and AI-Connect off the air until the next restart. Channels go on and off through their own
    toggles (``channel_toggles``), which start and stop their workers.
    """
    global _tools
    for name in [k for k in sys.modules if k.startswith("aifred.plugins.tools.")]:
        del sys.modules[name]
    _tools = None
    discover_tools()


# ============================================================
# LIST / ENABLE / DISABLE (for Plugin Manager UI)
# ============================================================

def list_all_plugins() -> list[dict[str, str]]:
    """List all tool plugins, switched on or off, with status (a channel is switched by its settings, not listed here).

    Returns: [{"name": "epim", "file": "epim", "type": "tool", "enabled": "1"}, ...]
    """
    result: list[dict[str, str]] = []

    def _display(path: Path) -> dict[str, str]:
        """Display names in both UI languages, read from the plugin's own
        i18n.json — works for plugins switched off too, which are not loaded."""
        return {
            f"display_{lang}": plugin_i18n_text(path, PLUGIN_DISPLAY_NAME_KEY, lang)
            for lang in ("de", "en")
        }

    def _has_credentials(stem: str) -> str:
        """Whether a tool plugin has a settings form, switched on or off."""
        return "1" if getattr(get_tool_plugin_on_or_off(stem), "credential_fields", None) else ""

    for plugin_dir in _plugin_dirs("tool"):
        name = plugin_dir.name
        result.append({
            "name": name, **_display(plugin_dir), "file": name, "type": "tool",
            "enabled": "" if _is_switched_off(plugin_dir) else "1",
            "has_credentials": _has_credentials(name),
        })

    result.sort(key=lambda p: p["name"])
    return result


def _plugin_dir(name: str) -> Path | None:
    """The folder of the tool plugin ``name``; None if there is no such plugin."""
    path = _plugins_root() / _SUBDIRS["tool"] / name
    return path if path in _plugin_dirs("tool") else None


def is_plugin_enabled(name: str) -> bool:
    """True if the tool plugin exists and is not switched off.

    Pure filesystem check (no plugin import) — safe to call from lib modules
    like vision_prewarm without a circular import.
    """
    path = _plugin_dir(name)
    return path is not None and not _is_switched_off(path)


def require_plugin_enabled(name: str) -> None:
    """``Conflict`` (409, in the UI language) for a tool plugin that is switched off: what belongs to it alone answers
    with this instead of working."""
    from .user_error import Conflict

    if not is_plugin_enabled(name):
        path = _plugins_root() / _SUBDIRS["tool"] / name
        from .prompt_loader import get_language

        raise Conflict("plugin_off", plugin=plugin_i18n_text(path, PLUGIN_DISPLAY_NAME_KEY, get_language()) or name)


def disable_plugin(name: str) -> bool:
    """Switch a tool plugin off (set its marker) and reload the tools. False if there is no such plugin."""
    path = _plugin_dir(name)
    if path is None:
        return False
    (path / DISABLED_MARKER).touch()
    _reload_tools()
    return True


def enable_plugin(name: str) -> bool:
    """Switch a tool plugin on (remove its marker) and reload the tools. False if there is no
    such plugin or it is not switched off."""
    path = _plugin_dir(name)
    if path is None:
        return False
    marker = path / DISABLED_MARKER
    if not marker.exists():
        return False
    marker.unlink()
    _reload_tools()
    return True
