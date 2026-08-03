"""Paragraph-boundary text chunking — shared by translator and narrator.

Extracted from the translator plugin so every consumer that has to feed
long text piecewise into an external service (DeepL, TTS engines) splits
it the same way: at blank-line paragraph boundaries, never mid-sentence.
"""
from __future__ import annotations


def split_paragraph_chunks(text: str, limit: int) -> list[str]:
    """Text an Absatzgrenzen (Leerzeilen) in Stücke <= ``limit`` teilen.

    NIE mitten im Satz oder pro Zeile schneiden — ein hart umbrochener
    Absatz, der zeilenweise übersetzt wird, verliert den Satzkontext
    (beobachtet 2026-07-18: "from a fresh / clone handles …" wurde zu
    "aus einem neuen / Kümmer sich um …"). Ein einzelner Absatz, der
    allein schon zu groß ist, geht ungeteilt raus.
    """
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        candidate = f"{buf}\n\n{para}" if buf else para
        if buf and len(candidate) > limit:
            chunks.append(buf)
            buf = para
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    return chunks
