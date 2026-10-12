"""Browser-Streaming nach Absätzen: Extraktion vollständiger Absätze (die Wahl der Einheit je Engine steht in test_tts_service)."""

from aifred.lib.audio_processing import extract_complete_paragraphs


class TestExtractCompleteParagraphs:
    def test_complete_paragraphs_come_out_the_rest_stays(self):
        assert extract_complete_paragraphs("Eins hier.\n\nZwei hier\n\nDrei halb") == (
            ["Eins hier.", "Zwei hier"], "Drei halb",
        )

    def test_no_blank_line_yet_means_nothing_is_complete(self):
        assert extract_complete_paragraphs("Noch kein Absatzende hier") == ([], "Noch kein Absatzende hier")

    def test_windows_line_endings_are_handled(self):
        assert extract_complete_paragraphs("Eins\r\n\r\nZwei") == (["Eins"], "Zwei")
