"""Tests für die Narrator-Engine/Voice-Auswahl + list_narrator_voices."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from aifred.lib.tts_engines import voice_names
from aifred.lib.tts_escalation import NoSpeechAvailable
from aifred.lib.plugin_base import PluginContext
from aifred.plugins.tools.narrator import _choose_engine_and_voice, plugin


class _FakeEngine:
    def __init__(self, key: str, voices: dict[str, str]):
        self.key = key
        self._voices = voices
        self.voices_fallback = voices

    def get_voices(self) -> dict[str, str]:
        return self._voices


ENGINES = {
    "piper": _FakeEngine("piper", {"Deutsch (Thorsten)": "t", "Deutsch (Ramona)": "r"}),
    "edge": _FakeEngine("edge", {"Deutsch (Katja)": "k", "Deutsch (Conrad)": "c"}),
}


@pytest.fixture
def fake_env(monkeypatch):
    """Settings und Eskalationsliste mocken: die Liste liefert ``list_top``,
    ein expliziter Schlüssel genau diese Engine (lazy imports → Modul-Patch greift)."""
    settings: dict = {}
    state = SimpleNamespace(list_top="piper", asked=[])

    async def choose_speaker(engine_key: str, label: str):
        state.asked.append(engine_key)
        key = engine_key or state.list_top
        if key not in ENGINES:
            raise NoSpeechAvailable(f"[{label}] TTS engine {key!r} cannot speak now")
        return SimpleNamespace(engine=ENGINES[key])

    monkeypatch.setattr("aifred.lib.settings.load_settings", lambda: settings)
    monkeypatch.setattr("aifred.lib.tts_escalation.choose_speaker", choose_speaker)
    return SimpleNamespace(settings=settings, state=state)


def _choose(engine: str = "", voice: str = "") -> tuple[str, str]:
    eng, chosen_voice = asyncio.run(_choose_engine_and_voice(engine, voice))
    return eng.key, chosen_voice


class TestChooseEngineAndVoice:
    def test_without_engine_the_list_decides(self, fake_env):
        assert _choose() == ("piper", "Deutsch (Thorsten)")
        assert fake_env.state.asked == [""]

    def test_explicit_engine_uses_saved_voice(self, fake_env):
        fake_env.settings["narrator_voices"] = {"edge": "Deutsch (Katja)"}
        assert _choose("edge") == ("edge", "Deutsch (Katja)")

    def test_saved_voice_is_engine_bound(self, fake_env):
        # Die für Edge gespeicherte Stimme darf Piper nicht erreichen.
        fake_env.settings["narrator_voices"] = {"edge": "Deutsch (Katja)"}
        assert _choose("piper") == ("piper", "Deutsch (Thorsten)")

    def test_explicit_voice_wins(self, fake_env):
        fake_env.settings["narrator_voices"] = {"edge": "Deutsch (Katja)"}
        assert _choose("edge", "Deutsch (Conrad)") == ("edge", "Deutsch (Conrad)")


class TestVoiceNames:
    def test_get_voices_failure_falls_back(self):
        class _DownEngine:
            voices_fallback = {"A": "a"}

            def get_voices(self) -> dict[str, str]:
                raise RuntimeError("down")

        assert voice_names(_DownEngine()) == ["A"]

    def test_empty_get_voices_falls_back(self):
        # Container-Engines liefern {} (keine Exception), solange der
        # Container down ist — z. B. qwen3local nach Idle-Stop.
        class _StoppedContainerEngine:
            voices_fallback = {"AIfred": "AIfred", "Salomo": "Salomo"}

            def get_voices(self) -> dict[str, str]:
                return {}

        assert voice_names(_StoppedContainerEngine()) == ["AIfred", "Salomo"]


class TestListNarratorVoicesTool:
    def _tool(self):
        ctx = PluginContext(agent_id="aifred", lang="de", session_id="test")
        tools = {t.name: t for t in plugin.get_tools(ctx)}
        return tools["list_narrator_voices"]

    def test_lists_voices_of_the_engine_the_list_picks(self, fake_env):
        fake_env.state.list_top = "edge"
        fake_env.settings["narrator_voices"] = {"edge": "Deutsch (Katja)"}
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

    def test_engine_that_cannot_speak_is_clear_error(self, fake_env):
        res = json.loads(asyncio.run(self._tool().executor(engine="nope")))
        assert "error" in res and "nope" in res["error"]


def test_unknown_engine_key_is_clear_error():
    """Ohne Mock: ``choose_speaker`` kennt den Schlüssel nicht → Fehlertext, kein Absturz."""
    ctx = PluginContext(agent_id="aifred", lang="de", session_id="test")
    tool = {t.name: t for t in plugin.get_tools(ctx)}["list_narrator_voices"]
    res = json.loads(asyncio.run(tool.executor(engine="nope")))
    assert "error" in res and "nope" in res["error"]
