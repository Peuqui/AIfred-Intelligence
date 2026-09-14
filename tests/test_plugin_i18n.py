"""Every plugin package carries its own name and description in DE and EN.

The plugin list, tool pills, credential modal, Message Hub and the sub-agent
schema read them only through lib.plugin_base, which fails loud on a gap —
this test finds the gap before the UI does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aifred.lib.plugin_base import (
    PLUGIN_DESCRIPTION_KEY,
    PLUGIN_DISPLAY_NAME_KEY,
    plugin_description,
    plugin_display_name,
    plugin_i18n_text,
)
from aifred.lib.plugin_registry import all_channels, discover_tools

_PLUGINS_ROOT = Path(__file__).resolve().parent.parent / "aifred" / "plugins"


def _plugin_packages() -> list[Path]:
    return sorted(
        d
        for sub in ("tools", "channels", "disabled")
        if (_PLUGINS_ROOT / sub).is_dir()
        for d in (_PLUGINS_ROOT / sub).iterdir()
        if d.is_dir() and (d / "__init__.py").exists() and not d.name.startswith("_")
    )


@pytest.mark.parametrize("package", _plugin_packages(), ids=lambda p: p.name)
def test_package_has_valid_i18n_with_name_and_description(package: Path) -> None:
    data = json.loads((package / "i18n.json").read_text(encoding="utf-8"))
    for key in (PLUGIN_DISPLAY_NAME_KEY, PLUGIN_DESCRIPTION_KEY):
        for lang in ("de", "en"):
            assert data[key][lang].strip(), f"{package.name}: {key}[{lang}] empty"


def test_loaded_plugins_resolve_through_the_accessors() -> None:
    plugins: list[object] = [*discover_tools(), *all_channels().values()]
    assert plugins
    for plugin in plugins:
        for lang in ("de", "en"):
            assert plugin_display_name(plugin, lang)
            assert plugin_description(plugin, lang)


def test_missing_text_fails_loud(tmp_path: Path) -> None:
    (tmp_path / "i18n.json").write_text(
        json.dumps({PLUGIN_DISPLAY_NAME_KEY: {"de": "Nur Deutsch"}}), encoding="utf-8"
    )
    assert plugin_i18n_text(tmp_path, PLUGIN_DISPLAY_NAME_KEY, "de") == "Nur Deutsch"
    with pytest.raises(RuntimeError, match=r"plugin_display_name\[en\]"):
        plugin_i18n_text(tmp_path, PLUGIN_DISPLAY_NAME_KEY, "en")
    with pytest.raises(RuntimeError, match="plugin_description"):
        plugin_i18n_text(tmp_path, PLUGIN_DESCRIPTION_KEY, "de")


def test_credential_texts_come_from_every_plugins_own_i18n() -> None:
    """Tool plugins too: the dialog read only channel i18n, so tool plugins'
    field labels lived in the central i18n (or showed raw keys)."""
    from aifred.lib.plugin_base import load_plugin_i18n, plugin_dir_of
    from aifred.lib.plugin_registry import all_channels, discover_tools
    from aifred.state._settings_mixin import _translate_cred_key

    for plugin in [*discover_tools(), *all_channels().values()]:
        i18n = load_plugin_i18n(plugin_dir_of(plugin))
        for field in getattr(plugin, "credential_fields", []) or []:
            for lang in ("de", "en"):
                assert _translate_cred_key(field.label_key, plugin, lang), (plugin, field.label_key, lang)
            for _, label in field.options or []:
                if label in i18n or "_opt_" in label:
                    assert _translate_cred_key(label, plugin, "de"), (plugin, label)
