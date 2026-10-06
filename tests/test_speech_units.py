"""Einheit der Sprachausgabe: die Wahl pro Engine (SSOT), die Aufteilung je Einheit, die Browser-Extraktion."""

import pytest

import aifred.lib.settings as settings_module
from aifred.lib.audio_processing import build_speech_segments, extract_complete_paragraphs
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
        assert speech_unit_for("edge") == "sentence" and speech_unit_for("dashscope") == "sentence"

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


class TestBuildSpeechSegments:
    TEXT = "Erster Satz mit genug Wörtern. Zweiter Satz mit genug Wörtern.\n\nNeuer Absatz mit genug Wörtern."

    def test_sentence_by_sentence(self):
        assert build_speech_segments([self.TEXT], 0, "sentence") == [
            "Erster Satz mit genug Wörtern.", "Zweiter Satz mit genug Wörtern.", "Neuer Absatz mit genug Wörtern.",
        ]

    def test_sentence_unit_puts_silence_only_between_the_given_paragraphs(self):
        assert build_speech_segments(
            ["Erster Absatz mit genug Wörtern. Zweiter Satz hier.", "Dritter Absatz mit genug Wörtern."], 800, "sentence",
        ) == [
            "Erster Absatz mit genug Wörtern.", "Zweiter Satz hier.", 800, "Dritter Absatz mit genug Wörtern.",
        ]

    def test_paragraph_by_paragraph_splits_at_blank_lines(self):
        assert build_speech_segments([self.TEXT], 500, "paragraph") == [
            "Erster Satz mit genug Wörtern. Zweiter Satz mit genug Wörtern.", 500, "Neuer Absatz mit genug Wörtern.",
        ]

    def test_whole_text_at_once_is_one_render(self):
        assert build_speech_segments(["Absatz eins hier.", "Absatz zwei dort."], 500, "whole") == [
            "Absatz eins hier.\n\nAbsatz zwei dort.",
        ]

    def test_empty_input_has_no_segments(self):
        for unit in SPEECH_UNITS:
            assert build_speech_segments(["  ", ""], 500, unit) == []

    def test_an_invalid_unit_is_refused(self):
        with pytest.raises(ValueError, match="speech unit"):
            build_speech_segments(["Text hier."], 0, "word")


class TestExtractCompleteParagraphs:
    def test_complete_paragraphs_come_out_the_rest_stays(self):
        assert extract_complete_paragraphs("Eins hier.\n\nZwei hier\n\nDrei halb") == (
            ["Eins hier.", "Zwei hier"], "Drei halb",
        )

    def test_no_blank_line_yet_means_nothing_is_complete(self):
        assert extract_complete_paragraphs("Noch kein Absatzende hier") == ([], "Noch kein Absatzende hier")

    def test_windows_line_endings_are_handled(self):
        assert extract_complete_paragraphs("Eins\r\n\r\nZwei") == (["Eins"], "Zwei")


class _FakeState:
    """Das Nötigste, was die Toggle-Methoden des Zustands anfassen."""

    def __init__(self, autoplay: bool = True, unit: str = "sentence") -> None:
        self.tts_autoplay = autoplay
        self.tts_speech_unit = unit
        self.written: dict = {}

    def add_debug(self, message: str) -> None:
        pass

    def _write_settings_file(self, settings: dict) -> None:
        self.written = settings


class TestBrowserToggles:
    """Die Auswahl im Browser schreibt dieselbe Stelle, aus der speech_unit_for liest."""

    def test_the_selection_is_saved_per_engine_and_read_back_by_the_ssot(self, saved):
        from aifred.state._tts_config_mixin import TTSConfigMixin

        state = _FakeState(autoplay=True, unit="paragraph")
        TTSConfigMixin._save_tts_toggles_for_engine(state, "piper")  # type: ignore[arg-type]
        assert state.written["tts_toggles_per_engine"]["piper"] == {"autoplay": True, "unit": "paragraph"}
        saved.update(state.written)                       # was gespeichert wurde, liest die SSOT
        assert speech_unit_for("piper") == "paragraph"

    def test_restoring_an_engine_without_a_saved_choice_uses_the_engine_default(self, saved):
        from aifred.state._tts_config_mixin import TTSConfigMixin

        state = _FakeState(autoplay=False, unit="sentence")
        TTSConfigMixin._restore_tts_toggles_for_engine(state, "moss")  # type: ignore[arg-type]
        assert (state.tts_autoplay, state.tts_speech_unit) == (True, "whole")

    def test_restoring_uses_the_saved_choice(self, saved):
        from aifred.state._tts_config_mixin import TTSConfigMixin

        saved["tts_toggles_per_engine"] = {"edge": {"autoplay": False, "unit": "whole"}}
        state = _FakeState()
        TTSConfigMixin._restore_tts_toggles_for_engine(state, "edge")  # type: ignore[arg-type]
        assert (state.tts_autoplay, state.tts_speech_unit) == (False, "whole")
