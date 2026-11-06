"""The alert rules list every channel plugin under its plugin name.

Since a43e9478 (13.09.2026) channels have no display_name attribute; the list read it, the broad
except caught the AttributeError and the rules showed no channel at all.
"""

from typing import Any

import pytest

import aifred.state._vision_settings_mixin as vision_settings_mixin
from aifred.lib.plugin_base import plugin_display_name
from aifred.lib.plugin_registry import all_channels
from aifred.state import AIState


class _RulesFake:
    _reload_alert_rules = AIState._reload_alert_rules

    def __init__(self, language: str) -> None:
        self.ui_language = language
        self.alert_channels: list[dict[str, str]] = []
        self.alert_rules_ui: list[dict[str, object]] = []


@pytest.mark.parametrize("language", ["de", "en"])
def test_every_channel_with_its_plugin_name(monkeypatch: pytest.MonkeyPatch, language: str) -> None:
    monkeypatch.setattr(vision_settings_mixin, "_load_alert_rules", list)
    channels = all_channels()
    assert channels, "no channel plugin found — the test would check nothing"
    # A stand-in with only the fields the method touches, not a VisionSettingsMixin.
    rules: Any = _RulesFake(language)
    rules._reload_alert_rules()
    assert rules.alert_channels == [
        {"name": name, "display_name": plugin_display_name(channel, language)}
        for name, channel in sorted(channels.items())
    ]
