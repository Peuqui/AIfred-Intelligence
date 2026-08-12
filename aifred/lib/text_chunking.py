"""Text chunking helpers — shared by translator, narrator and the
message channels.

- :func:`split_paragraph_chunks`: at blank-line paragraph boundaries,
  never mid-sentence (DeepL, TTS engines).
- :func:`split_message`: at line boundaries into a hard length cap
  (Telegram 4096, Discord 2000 — channel message limits).
"""
from __future__ import annotations

import re


def split_message(text: str, max_length: int) -> list[str]:
    """Split a message into chunks that fit a channel's length limit.

    Bevorzugt Umbrüche an Zeilengrenzen (zerreißt keine Wörter/Codeblöcke
    mitten in der Zeile, wenn ein Umbruch existiert). Liefert nie leere
    Chunks — Telegram/Discord lehnen leere Nachrichten mit einem
    API-Fehler ab (traf z.B. Caption-Overflow, der mit ``\\n`` begann).
    """
    if not text:
        return []
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        # Split at last newline before limit (<= 0 = kein sinnvoller Umbruch)
        split_at = text.rfind("\n", 0, max_length)
        if split_at <= 0:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


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


# Sprecher-Marker am Zeilenanfang: "[LABEL]:" — Label frei (FRAGE, S1, …).
_SPEAKER_MARKER = re.compile(r"^\[([^\]]+)\]:[ \t]*", re.MULTILINE)


def split_speaker_segments(text: str) -> list[tuple[str | None, str]]:
    """Text an Sprecher-Markern ``[LABEL]:`` in (label, text)-Segmente teilen.

    Ein Segment läuft bis zum nächsten Marker; der Marker selbst wird
    gestrippt (er soll nicht mitvertont werden). Text vor dem ersten
    Marker bekommt das Label ``None`` (→ Default-Stimme beim Aufrufer).
    Leere Segmente (Marker direkt hintereinander) werden verworfen.
    """
    segments: list[tuple[str | None, str]] = []
    pos = 0
    label: str | None = None
    for match in _SPEAKER_MARKER.finditer(text):
        segment_text = text[pos:match.start()].strip()
        if segment_text:
            segments.append((label, segment_text))
        label = match.group(1)
        pos = match.end()
    tail = text[pos:].strip()
    if tail:
        segments.append((label, tail))
    return segments
