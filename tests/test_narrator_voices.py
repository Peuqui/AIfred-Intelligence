"""Tests für die Narrator-Engine/Voice-Auflösung + list_narrator_voices."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from aifred.plugins.tools.narrator import (
    _resolve_engine_and_voice,
    _voice_names,
    plugin,
)
from aifred.lib.plugin_base import PluginContext


class _FakeEngine:
    def __init__(self, voices: dict[str, str]):
        self._voices = voices
        self.voices_fallback = voices

    def get_voices(self) -> dict[str, str]:
        return self._voices


@pytest.fixture
def fake_env(monkeypatch):
    """Settings + Engine-Registry mocken (lazy imports → Modul-Patch greift)."""
    settings: dict = {}
    engines = {
        "piper": _FakeEngine({"Deutsch (Thorsten)": "t", "Deutsch (Ramona)": "r"}),
        "edge": _FakeEngine({"Deutsch (Katja)": "k", "Deutsch (Conrad)": "c"}),
    }
    monkeypatch.setattr("aifred.lib.settings.load_settings", lambda: settings)
    monkeypatch.setattr("aifred.lib.tts_engines.get_engine", engines.get)
    return settings


class TestResolveEngineAndVoice:
    def test_explicit_engine_uses_saved_voice(self, fake_env):
        fake_env["narrator_voices"] = {"edge": "Deutsch (Katja)"}
        assert _resolve_engine_and_voice("edge") == ("edge", "Deutsch (Katja)")

    def test_auto_off_tts_resolves_fallback_engine(self, fake_env):
        fake_env.update({
            "narrator_engine": "auto",
            "enable_tts": False,
            "narrator_fallback_engine": "piper",
        })
        engine, voice = _resolve_engine_and_voice()
        assert engine == "piper"
        # Keine gespeicherte Stimme → erste eigene Stimme der Engine.
        assert voice == "Deutsch (Thorsten)"

    def test_saved_voice_is_engine_bound(self, fake_env):
        # Die für Edge gespeicherte Stimme darf Piper nicht erreichen.
        fake_env["narrator_voices"] = {"edge": "Deutsch (Katja)"}
        engine, voice = _resolve_engine_and_voice("piper")
        assert (engine, voice) == ("piper", "Deutsch (Thorsten)")

    def test_explicit_voice_wins(self, fake_env):
        fake_env["narrator_voices"] = {"edge": "Deutsch (Katja)"}
        assert _resolve_engine_and_voice("edge", "Deutsch (Conrad)") == (
            "edge", "Deutsch (Conrad)",
        )


class TestVoiceNames:
    def test_get_voices_failure_falls_back(self):
        eng = SimpleNamespace(
            get_voices=lambda: (_ for _ in ()).throw(RuntimeError("down")),
            voices_fallback={"A": "a"},
        )
        assert _voice_names(eng) == ["A"]


class TestListNarratorVoicesTool:
    def _tool(self):
        ctx = PluginContext(agent_id="aifred", lang="de", session_id="test")
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        return tools["list_narrator_voices"]

    def test_lists_effective_engine_voices(self, fake_env):
        fake_env.update({
            "narrator_engine": "auto",
            "enable_tts": False,
            "narrator_fallback_engine": "edge",
            "narrator_voices": {"edge": "Deutsch (Katja)"},
        })
        res = json.loads(asyncio.run(self._tool().executor()))
        assert res == {
            "engine": "edge",
            "default_voice": "Deutsch (Katja)",
            "voices": ["Deutsch (Katja)", "Deutsch (Conrad)"],
        }

    def test_explicit_engine_param(self, fake_env):
        res = json.loads(asyncio.run(self._tool().executor(engine="piper")))
        assert res["engine"] == "piper"
        assert res["voices"] == ["Deutsch (Thorsten)", "Deutsch (Ramona)"]

    def test_unknown_engine_is_clear_error(self, fake_env):
        res = json.loads(asyncio.run(self._tool().executor(engine="nope")))
        assert "error" in res and "nope" in res["error"]
