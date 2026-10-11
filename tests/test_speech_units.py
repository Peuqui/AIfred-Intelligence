"""Einheit der Sprachausgabe: die Wahl pro Engine (SSOT) und die Browser-Extraktion."""

import pytest

import aifred.lib.settings as settings_module
from aifred.lib.audio_processing import extract_complete_paragraphs
from aifred.lib.tts_engines import SPEECH_UNITS, TTS_ENGINES, speech_unit_for


@pytest.fixture
def saved(monkeypatch):
    """Die gespeicherten Einstellungen (settings.json) für den Test setzen."""
    store: dict = {}
    monkeypatch.setattr(settings_module, "load_settings", lambda: store)
    return store


class TestSpeechUnitPerEngine:
    def test_every_engine_has_a_valid_default(self):
        assert all(engine.default_speech_unit in SPEECH_UNITS for engine in TTS_ENGINES.values())

    def test_slow_local_engines_default_to_the_whole_text_fast_ones_to_sentences(self, saved):
        assert {key: speech_unit_for(key) for key in ("piper", "moss", "espeak")} == {
            "piper": "whole", "moss": "whole", "espeak": "whole",
        }
        assert speech_unit_for("edge") == "sentence" and speech_unit_for("dashscope_audio3") == "sentence"

    def test_the_users_choice_per_engine_wins(self, saved):
        saved["tts_toggles_per_engine"] = {"piper": {"autoplay": True, "unit": "paragraph"}}
        assert speech_unit_for("piper") == "paragraph"
        assert speech_unit_for("edge") == "sentence"            # other engines are not touched

    def test_the_old_streaming_switch_is_ignored(self, saved):
        # keine Rückwärtskompatibilität: nur "unit" zählt
        saved["tts_toggles_per_engine"] = {"edge": {"autoplay": True, "streaming": False}}
        assert speech_unit_for("edge") == "sentence"

    def test_an_invalid_saved_unit_is_a_configuration_error(self, saved):
        saved["tts_toggles_per_engine"] = {"edge": {"unit": "word"}}
        with pytest.raises(ValueError, match="speech unit"):
            speech_unit_for("edge")

    def test_an_unknown_engine_is_an_error(self, saved):
        with pytest.raises(ValueError, match="unknown TTS engine"):
            speech_unit_for("nope")


class TestExtractCompleteParagraphs:
    def test_complete_paragraphs_come_out_the_rest_stays(self):
        assert extract_complete_paragraphs("Eins hier.\n\nZwei hier\n\nDrei halb") == (
            ["Eins hier.", "Zwei hier"], "Drei halb",
        )

    def test_no_blank_line_yet_means_nothing_is_complete(self):
        assert extract_complete_paragraphs("Noch kein Absatzende hier") == ([], "Noch kein Absatzende hier")

    def test_windows_line_endings_are_handled(self):
        assert extract_complete_paragraphs("Eins\r\n\r\nZwei") == (["Eins"], "Zwei")
