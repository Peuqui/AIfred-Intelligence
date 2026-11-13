"""The loop guard on a streamed answer (aifred/lib/repetition_guard.py)."""

from aifred.lib.config import REPETITION_GUARD_REPEATS
from aifred.lib.repetition_guard import RepetitionGuard

SENTENCE = "Eine Person steht vor der Haustür."


def test_a_sentence_coming_as_often_as_the_limit_is_a_loop() -> None:
    guard = RepetitionGuard()
    looped = [guard.feed(f"{SENTENCE} ") for _ in range(REPETITION_GUARD_REPEATS)]
    assert looped == [False] * (REPETITION_GUARD_REPEATS - 1) + [True]
    assert guard.kept_text() == SENTENCE      # up to the second appearance


def test_a_sentence_split_over_pieces_counts_once_it_is_complete() -> None:
    guard = RepetitionGuard()
    for _ in range(REPETITION_GUARD_REPEATS - 1):
        for piece in ("Eine Person ", "steht vor ", "der Haustür", ". "):
            assert not guard.feed(piece)
    assert not guard.feed("Eine Person steht vor der Haus")   # not complete yet
    assert guard.feed("tür.")


def test_short_sentences_and_a_real_description_never_loop() -> None:
    guard = RepetitionGuard()
    text = "Ja. Ja. Ja. Ja. Links ein Fahrrad. Rechts ein Auto. " + SENTENCE + " Danach Stille.\n" * 2
    assert not guard.feed(text)
    assert guard.kept_text() == text


def test_the_case_and_spacing_of_a_repeat_do_not_hide_it() -> None:
    guard = RepetitionGuard()
    guard.feed(f"{SENTENCE}\n")
    guard.feed(f"{SENTENCE.upper()}  ")
    assert guard.feed("eine  person steht vor der haustür.")
