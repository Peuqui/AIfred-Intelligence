"""Unified plugin registry — discovers channels and tools from subdirectories.

Scans:
- aifred/plugins/channels/ → BaseChannel instances
- aifred/plugins/tools/    → ToolPlugin instances

A plugin is a package folder (``__init__.py``) whose name is the plugin's name.

A plugin is switched off by a marker in its folder, ``.disabled``. The marker is machine-local
(gitignored); the code stays where git keeps it, so its tests run and updates reach it while it is
off. Without a marker a plugin is on: a new plugin needs nothing, a deleted one takes its marker along.
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
    """Import the channel plugins that are not switched off and collect their BaseChannel instances."""
    global _channels_discovered
    if _channels_discovered:
        return
    _channels_discovered = True

    from .logging_utils import log_exception, log_message

    for plugin_dir in _plugin_dirs("channel"):
        if _is_switched_off(plugin_dir):
            continue
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


def collect_plugin_tools(ctx: PluginContext) -> list[tuple[ToolPlugin | BaseChannel, list[Tool]]]:
    """Every available tool plugin and configured channel with the tools it
    offers in ``ctx`` — the one plugin-selection rule of the toolkit factory.
    Tier and whitelist filters come afterwards, at the caller."""
    collected: list[tuple[ToolPlugin | BaseChannel, list[Tool]]] = []
    for plugin in discover_tools():
        if plugin.is_available():
            collected.append((plugin, plugin.get_tools(ctx)))
    for channel in all_channels().values():
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
# RELOAD (after enable/disable)
# ============================================================

def reload_all() -> None:
    """Clear all caches and re-discover. Called after a plugin is switched on or off.

    Stops any currently registered channel listeners BEFORE swapping out
    the module objects from ``sys.modules`` — otherwise the old listener
    task keeps running against stale credentials/state while a fresh one
    gets registered on the next call to register_channel_workers, leading
    to duplicate IMAP polls / Discord reconnects.
    """
    global _tools, _channels_discovered

    # 1. Stop channel listeners. Iterate over the channels we currently
    #    know about (not every hub worker — the scheduler must keep going).
    from .message_hub import message_hub
    for channel_name in list(_channels.keys()):
        if message_hub.is_running(channel_name):
            message_hub.unregister(channel_name)

    # 2. Clear tool cache + stale modules
    stale = [k for k in sys.modules if k.startswith(("aifred.plugins.tools.", "aifred.plugins.channels."))]
    for k in stale:
        del sys.modules[k]

    _tools = None
    _channels.clear()
    _channels_discovered = False

    # 3. Re-discover
    discover_tools()
    _discover_channels()


# ============================================================
# LIST / ENABLE / DISABLE (for Plugin Manager UI)
# ============================================================

def list_all_plugins() -> list[dict[str, str]]:
    """List all plugins, switched on or off, with status.

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
        """Check if a tool plugin has credential_fields (a plugin switched off is not loaded: "")."""
        for p in discover_tools():
            if p.name == stem:
                fields = getattr(p, "credential_fields", None)
                if fields:
                    return "1"
        return ""

    for plugin_type in _SUBDIRS:
        for plugin_dir in _plugin_dirs(plugin_type):
            name = plugin_dir.name
            result.append({
                "name": name, **_display(plugin_dir), "file": name, "type": plugin_type,
                "enabled": "" if _is_switched_off(plugin_dir) else "1",
                "has_credentials": _has_credentials(name) if plugin_type == "tool" else "",
            })

    result.sort(key=lambda p: p["name"])
    return result


def _plugin_dir(name: str, plugin_type: str) -> Path | None:
    """The folder of the plugin ``name``; None if there is no such plugin."""
    path = _plugins_root() / _SUBDIRS[plugin_type] / name
    return path if path in _plugin_dirs(plugin_type) else None


def is_plugin_enabled(name: str, plugin_type: str = "tool") -> bool:
    """True if the plugin exists and is not switched off.

    Pure filesystem check (no plugin import) — safe to call from lib modules
    like vision_prewarm without a circular import.
    """
    path = _plugin_dir(name, plugin_type)
    return path is not None and not _is_switched_off(path)


def disable_plugin(name: str, plugin_type: str) -> bool:
    """Switch a plugin off (set its marker) and re-discover. False if there is no such plugin."""
    path = _plugin_dir(name, plugin_type)
    if path is None:
        return False
    (path / DISABLED_MARKER).touch()
    reload_all()
    return True


def enable_plugin(name: str, plugin_type: str) -> bool:
    """Switch a plugin on (remove its marker) and re-discover. False if there is no such plugin
    or it is not switched off."""
    path = _plugin_dir(name, plugin_type)
    if path is None:
        return False
    marker = path / DISABLED_MARKER
    if not marker.exists():
        return False
    marker.unlink()
    reload_all()
    return True
