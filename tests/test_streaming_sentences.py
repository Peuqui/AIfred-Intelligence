"""Satzaufteilung für das satzweise TTS-Streaming von Ansagen (SSOT: audio_processing)."""

import pytest

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


@pytest.mark.parametrize("ending", ["Haus", "noch", "auch", "ich", "Glas", "Tisch"])
def test_word_ending_like_an_abbreviation_is_still_a_sentence_end(ending):
    # "Haus." ends in "s.", "noch." in "ch." — only a word that IS the abbreviation counts
    sentences = split_text_for_streaming_tts(
        f"Das ist heute wirklich ein sehr schöner Tag und ich bleibe {ending}. "
        "Morgen wird das Wetter hoffentlich genauso gut bleiben."
    )
    assert len(sentences) == 2
    assert sentences[0].endswith(f"{ending}.")


def test_real_abbreviation_is_not_a_sentence_end():
    sentences = split_text_for_streaming_tts(
        "Wir treffen Dr. Müller morgen um zehn Uhr im Büro. Danach essen wir zusammen zu Mittag."
    )
    assert len(sentences) == 2
    assert sentences[0].startswith("Wir treffen Dr. Müller")
