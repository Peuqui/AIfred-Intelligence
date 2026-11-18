"""A plugin is a folder, and it is switched off by a marker in that folder, not by moving it away.

Until 06.11.2026 the Plugin Manager moved a plugin's folder to ``plugins/disabled/``: git then
saw its files as deleted, a ``git add -u`` would have removed the plugin from the repository, and
the plugin's tests failed to import. Now the folder stays and gets ``.disabled``. These tests build
a plugin tree in a temporary folder, so no test ever sets a marker in the real code tree.
"""

import json
from pathlib import Path

import pytest

from aifred.lib import plugin_registry


def _package(base: Path, name: str) -> Path:
    folder = base / name
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("", encoding="utf-8")
    (folder / "i18n.json").write_text(
        json.dumps({"plugin_display_name": {"de": name, "en": name},
                    "plugin_description": {"de": f"{name} beschrieben", "en": f"{name} described"}}), encoding="utf-8",
    )
    return folder


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """tools/alpha (on), tools/beta (off), channels/delta (on); tools/gamma.py is no plugin."""
    tools, channels = tmp_path / "tools", tmp_path / "channels"
    _package(tools, "alpha")
    (_package(tools, "beta") / ".disabled").touch()
    (tools / "gamma.py").write_text("", encoding="utf-8")
    (tools / "__init__.py").write_text("", encoding="utf-8")
    _package(channels, "delta")
    (channels / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(plugin_registry, "_plugins_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def reloads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Switching reloads the tools; here only counted (the real reload swaps the loaded tool modules)."""
    calls: list[str] = []
    monkeypatch.setattr(plugin_registry, "_reload_tools", lambda: calls.append("reload"))
    return calls


def test_the_marker_decides_on_or_off(tree: Path) -> None:
    assert plugin_registry.is_plugin_enabled("alpha")
    assert not plugin_registry.is_plugin_enabled("beta")
    assert not plugin_registry.is_plugin_enabled("missing")
    assert not plugin_registry.is_plugin_enabled("gamma")   # a lone module is no plugin


def test_the_manager_lists_plugins_switched_off_too(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_registry, "get_tool_plugin_on_or_off", lambda name: None)      # the stand-ins have no module
    listed = {(entry["type"], entry["name"]): entry["enabled"] for entry in plugin_registry.list_all_plugins()}
    assert listed == {("tool", "alpha"): "1", ("tool", "beta"): ""}      # a channel is switched in the settings


def test_a_plugin_switched_off_keeps_its_description(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Read from the plugin's folder: a switched-off plugin is not loaded, and its row in the settings had no text.
    monkeypatch.setattr(plugin_registry, "get_tool_plugin_on_or_off", lambda name: None)
    beta = next(entry for entry in plugin_registry.list_all_plugins() if entry["name"] == "beta")
    assert (beta["enabled"], beta["description_de"], beta["description_en"]) == ("", "beta beschrieben", "beta described")


def test_switching_off_and_on_sets_and_removes_the_marker_only(tree: Path, reloads: list[str]) -> None:
    alpha = tree / "tools" / "alpha"
    files = sorted(path.name for path in alpha.iterdir())

    assert plugin_registry.disable_plugin("alpha")
    assert (alpha / ".disabled").exists() and not plugin_registry.is_plugin_enabled("alpha")
    assert plugin_registry.enable_plugin("alpha")
    assert plugin_registry.is_plugin_enabled("alpha")
    assert sorted(path.name for path in alpha.iterdir()) == files   # the code never moved
    assert reloads == ["reload", "reload"]


def test_switching_what_is_not_there_changes_nothing(tree: Path, reloads: list[str]) -> None:
    assert not plugin_registry.disable_plugin("missing")
    assert not plugin_registry.disable_plugin("gamma")
    assert not plugin_registry.enable_plugin("alpha")      # already on
    assert reloads == []
    assert not (tree / "tools" / "gamma.disabled").exists()


def test_discovery_imports_only_plugins_switched_on(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    imported: list[str] = []

    def record(name: str, package: str | None = None) -> object:
        imported.append(name)
        return object()   # no plugin object: nothing gets registered

    monkeypatch.setattr(plugin_registry.importlib, "import_module", record)
    monkeypatch.setattr(plugin_registry, "_tools", None)
    monkeypatch.setattr(plugin_registry, "_channels", {})
    monkeypatch.setattr(plugin_registry, "_channels_discovered", False)

    plugin_registry.discover_tools()
    plugin_registry._discover_channels()

    assert imported == [".tools.alpha", ".channels.delta"]   # beta is off, gamma.py is no plugin


def test_switching_a_tool_leaves_the_channels_running(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Until 06.11.2026 the reload cancelled every channel's hub worker and nothing started them
    again: one switch in the Plugin Manager silenced mail, Telegram, Discord, Echo and AI-Connect."""
    import sys

    from aifred.lib import message_hub

    def refuse(name: str) -> None:
        raise AssertionError(f"channel worker {name!r} stopped by a tool switch")

    monkeypatch.setattr(message_hub.message_hub, "unregister", refuse)
    monkeypatch.setattr(message_hub.message_hub, "is_running", lambda name: True)
    monkeypatch.setattr(plugin_registry, "_tools", None)
    channels = {"probe": object()}
    monkeypatch.setattr(plugin_registry, "_channels", channels)
    monkeypatch.setattr(plugin_registry.importlib, "import_module", lambda name, package=None: object())
    loaded = {k: v for k, v in sys.modules.items() if k.startswith("aifred.plugins.")}
    try:
        assert plugin_registry.disable_plugin("alpha")
        assert plugin_registry.enable_plugin("alpha")
        assert [k for k in loaded if k.startswith("aifred.plugins.channels.") and k not in sys.modules] == []
    finally:
        sys.modules.update(loaded)   # the reload dropped this process's tool modules
    assert plugin_registry._channels is channels and list(channels) == ["probe"]


def test_a_channel_folder_with_the_marker_is_still_a_channel(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing ever wrote ``.disabled`` into a channel's folder: a channel is on or off by ``monitor`` in the
    settings (``channel_is_on``). The marker was a second, dead switch; it no longer decides anything."""
    (tree / "channels" / "delta" / ".disabled").touch()
    imported: list[str] = []

    def record(name: str, package: str | None = None) -> object:
        imported.append(name)
        return object()

    monkeypatch.setattr(plugin_registry.importlib, "import_module", record)
    monkeypatch.setattr(plugin_registry, "_channels", {})
    monkeypatch.setattr(plugin_registry, "_channels_discovered", False)

    plugin_registry._discover_channels()

    assert imported == [".channels.delta"]
