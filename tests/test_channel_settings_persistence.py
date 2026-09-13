"""Channel switches must survive sessions that have not loaded them.

Until 2026-09-13 every settings save wrote the session's copy of the channel
switches. A session that saved before its copy was loaded (new browser
session, startup auto-selects) wrote an empty dict, and all channels were off.
"""

import json
from types import SimpleNamespace

import pytest

import aifred.state._settings_mixin as mixin_module
from aifred.state._settings_mixin import SettingsMixin


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "channel_toggles": {"email": {"monitor": True, "listener": True}, "telegram": {"monitor": True}},
        "channel_security_tiers": {"email": 2},
    }))
    monkeypatch.setattr(mixin_module, "SETTINGS_FILE", path)
    monkeypatch.setattr(mixin_module, "load_settings", lambda: json.loads(path.read_text()))
    monkeypatch.setattr(mixin_module, "save_settings", lambda s: path.write_text(json.dumps(s)))
    return path


def _fresh_session() -> SimpleNamespace:
    """A session whose copy of the channel settings was never loaded."""
    session = SimpleNamespace(channel_toggles={}, channel_security_tiers={}, _last_settings_mtime=0.0)
    for name in ("_write_settings_file", "_load_channel_settings", "_save_channel_setting", "_set_channel_toggle"):
        setattr(session, name, getattr(SettingsMixin, name).__get__(session))
    return session


def test_one_switch_keeps_the_other_channels(settings_file):
    session = _fresh_session()
    session._set_channel_toggle("telegram", "listener", True)
    saved = json.loads(settings_file.read_text())
    assert saved["channel_toggles"] == {
        "email": {"monitor": True, "listener": True},
        "telegram": {"monitor": True, "listener": True},
    }
    assert session.channel_toggles == saved["channel_toggles"]  # session mirrors the file


def test_tier_change_keeps_the_switches(settings_file):
    session = _fresh_session()
    session._save_channel_setting("channel_security_tiers", "telegram", 1)
    saved = json.loads(settings_file.read_text())
    assert saved["channel_security_tiers"] == {"email": 2, "telegram": 1}
    assert saved["channel_toggles"]["email"]["monitor"] is True
