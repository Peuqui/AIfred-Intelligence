"""Tests for lib/text_chunking — paragraph chunks + speaker segments."""

from aifred.lib.text_chunking import split_paragraph_chunks, split_speaker_segments


class TestSplitParagraphChunks:
    def test_single_small_paragraph(self):
        assert split_paragraph_chunks("Hallo Welt.", 100) == ["Hallo Welt."]

    def test_merges_paragraphs_under_limit(self):
        text = "Absatz eins.\n\nAbsatz zwei."
        assert split_paragraph_chunks(text, 100) == [text]

    def test_splits_at_paragraph_boundary(self):
        text = "A" * 60 + "\n\n" + "B" * 60
        assert split_paragraph_chunks(text, 80) == ["A" * 60, "B" * 60]

    def test_oversized_paragraph_splits_at_sentences(self):
        # Ein zu großer Absatz wird an SATZGRENZEN weitergeteilt — vorher
        # ging er ungeteilt raus und sprengte das DeepL-Request-Limit
        # (413 bei einem Whisper-Transkript ohne Leerzeilen, 2026-08-13).
        text = "Erster Satz ist hier. Zweiter Satz folgt sofort. Dritter Satz kommt zuletzt."
        chunks = split_paragraph_chunks(text, 50)
        assert len(chunks) > 1
        assert all(len(c) <= 50 for c in chunks)
        assert all(c.rstrip().endswith(".") for c in chunks)

    def test_oversized_sentence_hard_split_last_resort(self):
        # Pathologischer Einzelsatz ohne Satzzeichen: harter Schnitt beim
        # Limit statt eines Chunks über dem Request-Limit.
        text = "X" * 200
        chunks = split_paragraph_chunks(text, 80)
        assert all(len(c) <= 80 for c in chunks)
        assert "".join(chunks) == text


class TestSplitSpeakerSegments:
    def test_no_markers_single_default_segment(self):
        assert split_speaker_segments("Nur Fließtext.\n\nZweiter Absatz.") == [
            (None, "Nur Fließtext.\n\nZweiter Absatz."),
        ]

    def test_basic_dialog(self):
        text = "[FRAGE]: Wie geht es dir?\n[ANTWORT]: Gut, danke."
        assert split_speaker_segments(text) == [
            ("FRAGE", "Wie geht es dir?"),
            ("ANTWORT", "Gut, danke."),
        ]

    def test_text_before_first_marker_gets_none_label(self):
        text = "Intro vom Erzähler.\n[S1]: Erster Sprecher."
        assert split_speaker_segments(text) == [
            (None, "Intro vom Erzähler."),
            ("S1", "Erster Sprecher."),
        ]

    def test_segment_runs_until_next_marker(self):
        text = (
            "[FRAGE]: Erste Zeile.\nZweite Zeile gehört noch zur Frage.\n\n"
            "Auch dieser Absatz.\n[ANTWORT]: Antwort."
        )
        assert split_speaker_segments(text) == [
            ("FRAGE", "Erste Zeile.\nZweite Zeile gehört noch zur Frage.\n\nAuch dieser Absatz."),
            ("ANTWORT", "Antwort."),
        ]

    def test_marker_is_stripped(self):
        segments = split_speaker_segments("[FRAGE]: Text.")
        assert segments == [("FRAGE", "Text.")]
        assert "[FRAGE]" not in segments[0][1]

    def test_empty_segments_dropped(self):
        text = "[A]:\n[B]: Nur B spricht."
        assert split_speaker_segments(text) == [("B", "Nur B spricht.")]

    def test_marker_only_at_line_start(self):
        # "[sic]:" mitten in der Zeile ist KEIN Marker.
        text = "[S1]: Er sagte [sic]: alles gut."
        assert split_speaker_segments(text) == [("S1", "Er sagte [sic]: alles gut.")]

    def test_bracket_line_without_colon_is_not_marker(self):
        text = "[Musik spielt]\n[S1]: Hallo."
        assert split_speaker_segments(text) == [
            (None, "[Musik spielt]"),
            ("S1", "Hallo."),
        ]

    def test_repeated_labels(self):
        text = "[A]: Eins.\n[B]: Zwei.\n[A]: Drei."
        assert split_speaker_segments(text) == [
            ("A", "Eins."),
            ("B", "Zwei."),
            ("A", "Drei."),
        ]
