"""Tests für die SSOT-Voice-Resolution (_resolve_agent_tts).

Regression für den Bug: Re-Synth einer HAL-Bubble nach Engine-Wechsel
nahm die globale (AIfred-)Stimme, weil HALs gespeicherte xtts-Stimme
leer war. Der Resolver muss stattdessen HALs agents.json-Engine-Default
(★ HAL9000) ziehen — ein benannter Agent darf NIE die Stimme eines
anderen Agenten erben.
"""

from __future__ import annotations

from types import SimpleNamespace

from aifred.state._tts_streaming_mixin import TTSStreamingMixin

_resolve = TTSStreamingMixin._resolve_agent_tts


def _fake(agent_voices: dict, engine: str = "xtts", global_voice: str = "★ AIfred"):
    return SimpleNamespace(
        tts_agent_voices=agent_voices,
        tts_engine=engine,
        tts_voice=global_voice,
        tts_pitch="1.0",
    )


class TestVoiceResolution:
    def test_hal_empty_voice_falls_back_to_engine_default_not_global(self):
        """The reported bug: HAL's saved xtts voice is empty → must
        resolve to ★ HAL9000 (agents.json default), NOT the global
        AIfred voice."""
        self_ = _fake({"hal": {"voice": "", "speed": "1.0x", "pitch": "1.0"}})
        voice, speed, pitch = _resolve(self_, "hal")
        assert voice == "★ HAL9000"
        assert voice != "★ AIfred"
        assert speed == 1.0
        assert pitch == 1.0

    def test_explicit_user_voice_wins(self):
        """A voice the user set for this engine beats the default."""
        self_ = _fake({"hal": {"voice": "Custom Voice", "speed": "1.1x"}})
        voice, speed, _ = _resolve(self_, "hal")
        assert voice == "Custom Voice"
        assert speed == 1.1

    def test_agent_absent_uses_engine_default(self):
        """Agent not in the saved dict at all → still gets its engine
        default."""
        self_ = _fake({})
        voice, _, _ = _resolve(self_, "hal")
        assert voice == "★ HAL9000"

    def test_agent_without_engine_default_falls_back_to_global(self):
        """An agent with no agents.json tts_voices entry for this engine
        (e.g. pater on xtts) and no saved voice → global as last resort.
        Acceptable: it has no own voice to use, and global is the user's
        chosen default — but it must never silently pick a NAMED agent's
        voice."""
        self_ = _fake({"pater": {"voice": ""}}, global_voice="Neutral Default")
        voice, _, _ = _resolve(self_, "pater")
        assert voice == "Neutral Default"

    def test_speed_pitch_from_engine_default_when_unset(self):
        """When the agent has no per-agent speed/pitch, fall back to the
        engine default's values (HAL xtts default speed 1.0x)."""
        self_ = _fake({"hal": {"voice": ""}})  # no speed/pitch keys
        _, speed, pitch = _resolve(self_, "hal")
        assert speed == 1.0
        assert pitch == 1.0

    def test_aifred_resolves_to_own_voice(self):
        self_ = _fake({"aifred": {"voice": "", "speed": "1.25x"}})
        voice, speed, _ = _resolve(self_, "aifred")
        assert voice == "★ AIfred"
        assert speed == 1.25


class TestRestoreMerge:
    """_restore_agent_voices_for_engine must layer agents.json defaults
    under the saved prefs so the dropdown shows the right voice — an
    empty saved voice must not clobber the engine default."""

    def _run_restore(self, monkeypatch, saved_per_engine):
        import aifred.lib.settings as settings_mod
        from aifred.state._tts_config_mixin import TTSConfigMixin

        monkeypatch.setattr(
            settings_mod, "load_settings",
            lambda: {"tts_agent_voices_per_engine": saved_per_engine},
        )
        self_ = SimpleNamespace(
            tts_agent_voices={
                "hal": {"voice": "stale", "speed": "1.0x", "pitch": "1.0"},
                "aifred": {"voice": "stale", "speed": "1.0x", "pitch": "1.0"},
            },
            add_debug=lambda *a, **k: None,
            _strip_stale_voices_for_engine=lambda engine: None,
        )
        TTSConfigMixin._restore_agent_voices_for_engine(self_, "xtts")
        return self_.tts_agent_voices

    def test_empty_saved_voice_keeps_engine_default(self, monkeypatch):
        """The dropdown bug: saved hal.voice == "" → must show the
        agents.json default ★ HAL9000, not empty."""
        voices = self._run_restore(
            monkeypatch,
            {"xtts": {
                "hal": {"voice": "", "speed": "1.0x", "pitch": "1.0"},
                "aifred": {"voice": "★ AIfred", "speed": "1.2x"},
            }},
        )
        assert voices["hal"]["voice"] == "★ HAL9000"
        assert voices["aifred"]["voice"] == "★ AIfred"  # explicit saved wins

    def test_saved_speed_applies_even_when_voice_falls_back(self, monkeypatch):
        """A saved speed/pitch still applies when the voice itself falls
        back to the default."""
        voices = self._run_restore(
            monkeypatch,
            {"xtts": {"hal": {"voice": "", "speed": "1.4x"}}},
        )
        assert voices["hal"]["voice"] == "★ HAL9000"
        assert voices["hal"]["speed"] == "1.4x"

    def test_no_saved_prefs_uses_defaults(self, monkeypatch):
        voices = self._run_restore(monkeypatch, {})
        assert voices["hal"]["voice"] == "★ HAL9000"
        assert voices["aifred"]["voice"] == "★ AIfred"
