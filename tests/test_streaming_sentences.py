"""Satzaufteilung für das satzweise TTS-Streaming von Ansagen (SSOT: audio_processing)."""

from aifred.lib.audio_processing import TTS_MIN_SENTENCE_WORDS, split_text_for_streaming_tts


def test_splits_into_sentences():
    text = "Das ist der erste Satz mit Inhalt. Und hier kommt der zweite Satz. Der dritte folgt zuletzt."
    assert split_text_for_streaming_tts(text) == [
        "Das ist der erste Satz mit Inhalt.",
        "Und hier kommt der zweite Satz.",
        "Der dritte folgt zuletzt.",
    ]


def test_last_fragment_without_period_is_spoken():
    sentences = split_text_for_streaming_tts("Das ist ein vollständiger Satz. Und der Rest ohne Punkt")
    assert len(sentences) == 2
    assert sentences[1].startswith("Und der Rest ohne Punkt")


def test_short_sentence_goes_to_the_next_one():
    sentences = split_text_for_streaming_tts("Ja. Das ist jetzt ein deutlich längerer Satz hier.")
    assert len(sentences) == 1
    assert sentences[0].startswith("Ja.") and sentences[0].endswith("hier.")


def test_short_rest_is_attached_to_the_last_sentence():
    sentences = split_text_for_streaming_tts("Das ist ein langer vollständiger Satz hier. Danke.")
    assert len(sentences) == 1
    assert sentences[0].endswith("Danke.")


def test_only_a_short_text_is_still_spoken():
    assert split_text_for_streaming_tts("Hallo.") == ["Hallo."]


def test_empty_text_has_no_sentences():
    assert split_text_for_streaming_tts("") == []
    assert split_text_for_streaming_tts("  \n ") == []


def test_every_returned_sentence_is_long_enough_unless_it_is_the_only_one():
    text = "Eins. Zwei. Drei vier fünf sechs sieben. Acht. Neun zehn elf zwölf."
    sentences = split_text_for_streaming_tts(text)
    assert all(len(s.split()) >= TTS_MIN_SENTENCE_WORDS for s in sentences)
