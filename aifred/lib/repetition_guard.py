"""Recognize a model that writes the same sentence over and over while its answer streams in.

A small model can fall into a loop and repeat one sentence until its context ends (the 4B describer
2026-08-24 and 2026-09-05, with nearly identical frames). Instead of a fixed length that also cuts
real answers, the stream is read sentence by sentence: once a sentence comes REPETITION_GUARD_REPEATS
times, the answer is a loop; what stays is the text up to the sentence's second appearance.
"""

from __future__ import annotations

import re

from .config import REPETITION_GUARD_MIN_SENTENCE_CHARS, REPETITION_GUARD_REPEATS

# A sentence ends at . ! ? or a line break; what follows the last end is still being written.
_SENTENCE_END = re.compile(r"[.!?\n]+")


def _normalized(sentence: str) -> str:
    return " ".join(sentence.lower().split())


class RepetitionGuard:
    """Feed the streamed text piece by piece; ``looping`` turns true at the first loop."""

    def __init__(self) -> None:
        self.text = ""
        self._scanned = 0                       # where the next complete sentence starts
        self._first_repeat: dict[str, int] = {}  # sentence → where its second appearance starts
        self._seen: dict[str, int] = {}
        self.looping = False
        self.repeated = ""

    def feed(self, piece: str) -> bool:
        """Add ``piece``; True once the text is a loop (then nothing more is taken)."""
        if self.looping:
            return True
        self.text += piece
        for match in _SENTENCE_END.finditer(self.text, self._scanned):
            start, self._scanned = self._scanned, match.end()
            sentence = _normalized(self.text[start:match.start()])
            if len(sentence) < REPETITION_GUARD_MIN_SENTENCE_CHARS:
                continue
            count = self._seen.get(sentence, 0) + 1
            self._seen[sentence] = count
            if count == 2:
                self._first_repeat[sentence] = start
            if count >= REPETITION_GUARD_REPEATS:
                self.looping = True
                self.repeated = sentence
                return True
        return False

    def kept_text(self) -> str:
        """The answer without the loop: up to the repeated sentence's second appearance."""
        if not self.looping:
            return self.text
        return self.text[: self._first_repeat[self.repeated]].rstrip()
