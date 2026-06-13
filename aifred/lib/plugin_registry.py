"""Unified plugin registry — discovers channels and tools from subdirectories.

Scans:
- aifred/plugins/channels/ → BaseChannel instances
- aifred/plugins/tools/    → ToolPlugin instances
- aifred/plugins/disabled/ → stored but not loaded

Plugins can be enabled/disabled by moving files between their
subdirectory and disabled/.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import shutil
import sys
from pathlib import Path
from typing import Optional

from .plugin_base import BaseChannel, ToolPlugin

logger = logging.getLogger(__name__)


def _plugins_root() -> Path:
    return Path(__file__).parent.parent / "plugins"


def _disabled_dir() -> Path:
    d = _plugins_root() / "disabled"
    d.mkdir(exist_ok=True)
    return d


# ============================================================
# CHANNEL REGISTRY
# ============================================================

_channels: dict[str, BaseChannel] = {}
_channels_discovered = False


def _discover_channels() -> None:
    """Import all modules in plugins/channels/ and collect BaseChannel instances."""
    global _channels_discovered
    if _channels_discovered:
        return
    _channels_discovered = True

    from .logging_utils import log_message

    channels_dir = _plugins_root() / "channels"
    if not channels_dir.exists():
        return

    # Ensure channels subpackage is importable
    channels_init = channels_dir / "__init__.py"
    if not channels_init.exists():
        channels_init.touch()

    for module_info in pkgutil.iter_modules([str(channels_dir)]):
        if module_info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f".channels.{module_info.name}", package="aifred.plugins")
            # Look for a module-level instance of BaseChannel
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if isinstance(obj, BaseChannel) and obj.name not in _channels:
                    _channels[obj.name] = obj
                    obj.load_settings_to_env()
                    log_message(f"Plugin Registry: channel '{obj.name}' registered")
        except Exception as exc:
            log_message(f"Plugin Registry: failed to load channel '{module_info.name}': {exc}", "error")


def get_channel(name: str) -> BaseChannel | None:
    _discover_channels()
    return _channels.get(name)


def all_channels() -> dict[str, BaseChannel]:
    _discover_channels()
    return dict(_channels)


# ============================================================
# TOOL PLUGIN REGISTRY
# ============================================================

_tools: Optional[list[ToolPlugin]] = None


def get_tool_plugin(name: str) -> ToolPlugin | None:
    """Get a tool plugin by name."""
    for p in discover_tools():
        if p.name == name:
            return p
    return None


def discover_tools() -> list[ToolPlugin]:
    """Scan plugins/tools/ and return all ToolPlugin instances. Cached."""
    global _tools
    if _tools is not None:
        return _tools

    from .logging_utils import log_message

    tools_dir = _plugins_root() / "tools"
    if not tools_dir.exists():
        _tools = []
        return _tools

    # Ensure tools subpackage is importable
    tools_init = tools_dir / "__init__.py"
    if not tools_init.exists():
        tools_init.touch()

    _tools = []
    for module_info in pkgutil.iter_modules([str(tools_dir)]):
        if module_info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f".tools.{module_info.name}", package="aifred.plugins")
        except Exception as e:
            log_message(f"Plugin Registry: failed to load tool '{module_info.name}': {e}", "error")
            continue

        plugin = getattr(mod, "plugin", None)
        if plugin is not None and isinstance(plugin, ToolPlugin):
            _tools.append(plugin)

    log_message(f"Plugin Registry: {len(_tools)} tool plugins: {[p.name for p in _tools]}")
    return _tools


# ============================================================
# RELOAD (after enable/disable)
# ============================================================

def reload_all() -> None:
    """Clear all caches and re-discover. Called after moving plugin files.

    Stops any currently registered channel listeners BEFORE swapping out
    the module objects from ``sys.modules`` — otherwise the old listener
    task keeps running against stale credentials/state while a fresh one
    gets registered on the next call to register_channel_workers, leading
    to duplicate IMAP polls / Discord reconnects.
    """
    global _tools, _channels, _channels_discovered

    # 1. Stop channel listeners. Iterate over the channels we currently
    #    know about (not every hub worker — the scheduler must keep going).
    from .logging_utils import log_message as _log
    try:
        from .message_hub import message_hub
        for channel_name in list(_channels.keys()):
            if message_hub.is_running(channel_name):
                message_hub.unregister(channel_name)
    except Exception as exc:  # noqa: BLE001
        _log(f"Plugin Registry: channel teardown warning: {exc}", "warning")

    # 2. Clear tool cache + stale modules
    stale = [k for k in sys.modules if k.startswith("aifred.plugins.tools.") or k.startswith("aifred.plugins.channels.")]
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
    """List all plugins (enabled + disabled) with status.

    Returns: [{"name": "epim", "file": "epim.py", "type": "tool", "enabled": "1"}, ...]
    """
    root = _plugins_root()
    disabled = _disabled_dir()
    result: list[dict[str, str]] = []

    def _display(stem: str) -> str:
        """Get display name from the loaded plugin."""
        for p in discover_tools():
            if p.name == stem:
                return p.display_name
        return stem.replace("_", " ").title()

    def _has_credentials(stem: str) -> str:
        """Check if a tool plugin has credential_fields."""
        for p in discover_tools():
            if p.name == stem:
                fields = getattr(p, "credential_fields", None)
                if fields:
                    return "1"
        return ""

    # Enabled channels (single files + packages)
    channels_dir = root / "channels"
    if channels_dir.exists():
        for f in sorted(channels_dir.glob("*.py")):
            if f.name.startswith("_"):
                continue
            result.append({"name": f.stem, "display": _display(f.stem), "file": f.name, "type": "channel", "enabled": "1", "has_credentials": ""})
        for d in sorted(channels_dir.iterdir()):
            if d.is_dir() and (d / "__init__.py").exists() and not d.name.startswith("_"):
                name = d.name
                result.append({"name": name, "display": _display(name), "file": d.name, "type": "channel", "enabled": "1", "has_credentials": ""})

    # Enabled tools (single files + packages)
    tools_dir = root / "tools"
    if tools_dir.exists():
        for f in sorted(tools_dir.glob("*.py")):
            if f.name.startswith("_"):
                continue
            result.append({"name": f.stem, "display": _display(f.stem), "file": f.name, "type": "tool", "enabled": "1", "has_credentials": _has_credentials(f.stem)})
        for d in sorted(tools_dir.iterdir()):
            if d.is_dir() and (d / "__init__.py").exists() and not d.name.startswith("_"):
                name = d.name
                result.append({"name": name, "display": _display(name), "file": d.name, "type": "tool", "enabled": "1", "has_credentials": _has_credentials(name)})

    # Disabled (both types — file/dir name encodes origin via prefix).
    def _split_prefix(stem: str) -> tuple[str, str]:
        if stem.startswith("channel_"):
            return "channel", stem.removeprefix("channel_")
        if stem.startswith("tool_"):
            return "tool", stem.removeprefix("tool_")
        return "tool", stem

    # Single-file disabled plugins
    for f in sorted(disabled.glob("*.py")):
        if f.name.startswith("_"):
            continue
        ptype, pname = _split_prefix(f.stem)
        result.append({"name": pname, "display": _display(pname), "file": f.name, "type": ptype, "enabled": "", "has_credentials": ""})

    # Package disabled plugins (directories) — e.g. the vision package.
    # Without this a disabled package vanishes from the manager and can
    # never be re-enabled via the UI.
    if disabled.exists():
        for d in sorted(disabled.iterdir()):
            if d.is_dir() and (d / "__init__.py").exists() and not d.name.startswith("_"):
                ptype, pname = _split_prefix(d.name)
                result.append({"name": pname, "display": _display(pname), "file": d.name, "type": ptype, "enabled": "", "has_credentials": ""})

    result.sort(key=lambda p: p["name"])
    return result


def _find_plugin_path(name: str, plugin_type: str) -> Path | None:
    """Find a plugin by name — single file or package."""
    subdir = "channels" if plugin_type == "channel" else "tools"
    base = _plugins_root() / subdir
    # Single file
    single = base / f"{name}.py"
    if single.exists():
        return single
    # Package (name_pkg or name)
    for pkg_name in [f"{name}_pkg", name]:
        pkg = base / pkg_name
        if pkg.is_dir() and (pkg / "__init__.py").exists():
            return pkg
    return None


def is_plugin_enabled(name: str, plugin_type: str = "tool") -> bool:
    """True if the plugin lives in its active dir (tools/ or channels/),
    i.e. it is NOT moved to disabled/.

    Pure filesystem check (no plugin import) — safe to call from lib modules
    like vision_prewarm without a circular import. Enable/disable is a
    directory move, so presence in the active dir == enabled.
    """
    return _find_plugin_path(name, plugin_type) is not None


def disable_plugin(name: str, plugin_type: str) -> bool:
    """Move a plugin to disabled/. Prefixes with type."""
    src = _find_plugin_path(name, plugin_type)
    if not src:
        return False

    prefix = "channel" if plugin_type == "channel" else "tool"
    dst = _disabled_dir() / f"{prefix}_{src.name}"
    shutil.move(str(src), str(dst))
    reload_all()
    return True


def enable_plugin(name: str, plugin_type: str) -> bool:
    """Move a plugin back from disabled/ to its subdirectory."""
    subdir = "channels" if plugin_type == "channel" else "tools"
    prefix = "channel" if plugin_type == "channel" else "tool"

    # Find in disabled (could be .py or directory)
    src = None
    for candidate in [f"{prefix}_{name}.py", f"{prefix}_{name}_pkg", f"{prefix}_{name}"]:
        p = _disabled_dir() / candidate
        if p.exists():
            src = p
            break

    if not src:
        return False

    # Restore original name (remove type prefix)
    original_name = src.name.removeprefix(f"{prefix}_")
    dst = _plugins_root() / subdir / original_name

    if not src.exists():
        return False

    shutil.move(str(src), str(dst))
    reload_all()
    return True
