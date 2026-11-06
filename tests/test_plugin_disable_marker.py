"""A plugin is switched off by a marker beside its code, not by moving the code away.

Until 06.11.2026 the Plugin Manager moved a plugin's folder to ``plugins/disabled/``: git then
saw its files as deleted, a ``git add -u`` would have removed the plugin from the repository, and
the plugin's tests failed to import. Now the folder stays and gets ``.disabled`` (a single-file
plugin ``<name>.disabled`` beside it). These tests build a plugin tree in a temporary folder, so
no test ever sets a marker in the real code tree.
"""

import json
import pkgutil
from pathlib import Path

import pytest

from aifred.lib import plugin_registry


def _package(base: Path, name: str) -> Path:
    folder = base / name
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("", encoding="utf-8")
    (folder / "i18n.json").write_text(
        json.dumps({"plugin_display_name": {"de": name, "en": name}}), encoding="utf-8",
    )
    return folder


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """tools/alpha (on), tools/beta (off), tools/gamma.py (off), channels/delta (on)."""
    tools, channels = tmp_path / "tools", tmp_path / "channels"
    _package(tools, "alpha")
    (_package(tools, "beta") / ".disabled").touch()
    (tools / "gamma.py").write_text("", encoding="utf-8")
    (tools / "gamma.disabled").touch()
    (tools / "__init__.py").write_text("", encoding="utf-8")
    _package(channels, "delta")
    (channels / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(plugin_registry, "_plugins_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def reloads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Switching re-discovers; here only counted (the real reload swaps the loaded plugin modules)."""
    calls: list[str] = []
    monkeypatch.setattr(plugin_registry, "reload_all", lambda: calls.append("reload"))
    return calls


def test_the_marker_decides_on_or_off(tree: Path) -> None:
    assert plugin_registry.is_plugin_enabled("alpha")
    assert not plugin_registry.is_plugin_enabled("beta")
    assert not plugin_registry.is_plugin_enabled("gamma")
    assert plugin_registry.is_plugin_enabled("delta", "channel")
    assert not plugin_registry.is_plugin_enabled("missing")


def test_the_manager_lists_plugins_switched_off_too(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tree / "tools" / "gamma.py").unlink()   # a single file has no i18n.json for the list
    (tree / "tools" / "gamma.disabled").unlink()
    monkeypatch.setattr(plugin_registry, "discover_tools", list)
    listed = {(entry["type"], entry["name"]): entry["enabled"] for entry in plugin_registry.list_all_plugins()}
    assert listed == {("tool", "alpha"): "1", ("tool", "beta"): "", ("channel", "delta"): "1"}


def test_switching_off_and_on_sets_and_removes_the_marker_only(tree: Path, reloads: list[str]) -> None:
    alpha = tree / "tools" / "alpha"
    files = sorted(path.name for path in alpha.iterdir())

    assert plugin_registry.disable_plugin("alpha", "tool")
    assert (alpha / ".disabled").exists() and not plugin_registry.is_plugin_enabled("alpha")
    assert plugin_registry.enable_plugin("alpha", "tool")
    assert plugin_registry.is_plugin_enabled("alpha")
    assert sorted(path.name for path in alpha.iterdir()) == files   # the code never moved
    assert reloads == ["reload", "reload"]

    assert plugin_registry.disable_plugin("gamma", "tool")          # single file: marker beside it
    assert (tree / "tools" / "gamma.disabled").exists()
    assert plugin_registry.enable_plugin("gamma", "tool")
    assert not (tree / "tools" / "gamma.disabled").exists()


def test_switching_what_is_not_there_changes_nothing(tree: Path, reloads: list[str]) -> None:
    assert not plugin_registry.disable_plugin("missing", "tool")
    assert not plugin_registry.enable_plugin("alpha", "tool")      # already on
    assert reloads == []


def test_discovery_does_not_import_a_plugin_switched_off(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    assert imported == [".tools.alpha", ".channels.delta"]


def test_the_marker_of_a_module_lies_beside_it(tmp_path: Path) -> None:
    (tmp_path / "solo.py").write_text("", encoding="utf-8")
    (tmp_path / "solo.disabled").touch()
    [info] = list(pkgutil.iter_modules([str(tmp_path)]))
    assert plugin_registry._is_switched_off(tmp_path, info)
