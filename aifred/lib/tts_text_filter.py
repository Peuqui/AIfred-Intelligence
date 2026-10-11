"""What AIfred itself keeps out of the speech (SSOT).

The TTS service cleans the general text (Markdown, code, tables, formulas, links, emojis,
HTML) and knows how things are pronounced (``pronunciations`` of its configuration). What it
cannot know belongs to AIfred's interface and is filtered here before the text is sent:

- collapsible blocks (think, vlm_output, data, ... and the rendered details): their content
  is hidden in the chat, so it must never be spoken,
- HTML comments (source metadata such as ``<!--USED_SOURCES:...-->``),
- the performance footer ``*( TTFT: ... )*``,
- labels of multi-agent rounds (``[Auto-Konsens: Synthese R1]``),
- timing information in parentheses (``(STT: 2.5s)``, ``(2.5s)``).
"""

from __future__ import annotations

import re

HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# The footer (formatting.format_performance_footer) holds parentheses of its own
# ("PP: ... (5.685 tok)"), so it must go as a whole, before the timing filter cuts at its first ")".
PERFORMANCE_FOOTER = re.compile(r"\*\(\s[\s\S]*?\s\)\*")
ROUND_LABEL = re.compile(
    r"\[(Auto-Konsens|Tribunal|Devils? Advocate|Auto-Consensus):[^\]]+R\d+\]", re.IGNORECASE,
)
TIMING_PARENTHESES = re.compile(
    r"\s*\([^)]*\b(STT|TTFT|Inference|Inferenz|Agent|Cache-Hit|Decision|Entscheidung|TTS|tok/s|Source)[^)]*\)",
)
NUMBER_PARENTHESES = re.compile(r"\s*\(\s*[\d,\.]+\s*(s|ms|tok/s)?\s*\)")

_collapsible_tags_cache: tuple[str, ...] | None = None


def collapsible_tags() -> tuple[str, ...]:
    """Tag names that render as collapsibles: derived from ``get_xml_tag_config`` (think, analysis,
    data, python, code, sql, json, vlm_output) plus the rendered ``details``. Read from config once,
    so "what is hidden from speech" lives in one place."""
    global _collapsible_tags_cache
    if _collapsible_tags_cache is None:
        from .config import get_xml_tag_config

        _collapsible_tags_cache = (*get_xml_tag_config().keys(), "details")
    return _collapsible_tags_cache


def strip_collapsible_blocks(text: str) -> str:
    """Remove every COMPLETE collapsible block with its content. An unclosed opening tag stays on
    purpose: the streaming buffer wait (``buffer_has_open_collapsible``) detects a block that is
    still being written."""
    for tag in collapsible_tags():
        text = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text


def _strip_ui_only_markup(text: str) -> str:
    """The text without what AIfred's interface adds and the chat hides; whitespace untouched
    (pieces of a stream must not lose the blank between two words)."""
    cleaned = HTML_COMMENT.sub("", text)
    cleaned = strip_collapsible_blocks(cleaned)
    cleaned = PERFORMANCE_FOOTER.sub("", cleaned)
    cleaned = ROUND_LABEL.sub("", cleaned)
    cleaned = TIMING_PARENTHESES.sub("", cleaned)
    return NUMBER_PARENTHESES.sub("", cleaned)


def strip_ui_only_content(text: str) -> str:
    """The text without what AIfred's interface adds and the chat hides; stripped. Everything else
    (Markdown, code, links, emojis, ...) is the service's business."""
    return _strip_ui_only_markup(text).strip()


# ---- The same filter on a stream ----------------------------------------------


def _collapsible_open_start(text: str) -> int | None:
    """Start of the first collapsible block whose closing tag has not arrived yet."""
    earliest: int | None = None
    for tag in collapsible_tags():
        openers = [match.start() for match in re.finditer(rf"<{tag}[^>]*>", text, re.IGNORECASE)]
        closers = len(re.findall(rf"</{tag}>", text, re.IGNORECASE))
        if len(openers) > closers:
            start = openers[closers]
            earliest = start if earliest is None else min(earliest, start)
    return earliest


def _bracket_open_start(text: str, opening: str, closing: str) -> int | None:
    """Start of the first ``opening`` bracket that has no ``closing`` yet (nesting counted)."""
    stack: list[int] = []
    for position, char in enumerate(text):
        if char == opening:
            stack.append(position)
        elif char == closing and stack:
            stack.pop()
    return stack[0] if stack else None


def _footer_open_start(text: str) -> int | None:
    """Start of a performance footer ``*( ... )*`` that is not complete yet."""
    start = text.rfind("*(")
    return start if start >= 0 and ")*" not in text[start:] else None


def _unclosed_tag_start(text: str) -> int | None:
    """Start of a ``<`` whose ``>`` has not arrived (a tag or a comment that is still being written)."""
    start = text.rfind("<")
    return start if start >= 0 and ">" not in text[start:] else None


def _open_start(text: str) -> int | None:
    """Index from which ``text`` holds a construct that is still open, so the filter cannot yet
    say what it will remove; ``None`` if everything before the end is settled. A collapsible
    block may stay open for any length (a long thought), a bracket or tag only for
    ``TTS_FILTER_MAX_HOLD_CHARS`` characters."""
    from .config import TTS_FILTER_MAX_HOLD_CHARS

    starts: list[int] = []
    comment = text.rfind("<!--")
    if comment >= 0 and "-->" not in text[comment:]:
        starts.append(comment)
    block = _collapsible_open_start(text)
    if block is not None:
        starts.append(block)
    for bracket in (_unclosed_tag_start(text), _footer_open_start(text), _bracket_open_start(text, "(", ")"), _bracket_open_start(text, "[", "]")):
        if bracket is not None and len(text) - bracket <= TTS_FILTER_MAX_HOLD_CHARS:
            # the footer starts with "*(": the star belongs to it
            starts.append(bracket - 1 if bracket > 0 and text[bracket - 1] == "*" else bracket)
    if text.endswith("*") and not text.endswith(")*"):  # may turn out to be the star of the footer's "*("
        starts.append(len(text) - 1)
    return min(starts) if starts else None


class UiOnlyContentStream:
    """``strip_ui_only_content`` for text that arrives in pieces: ``feed`` returns what is settled
    so far (without AIfred's interface content), ``finish`` the rest. Blocks, comments, brackets
    and tags cut by a piece border are held back until they are complete (or, for brackets, long
    enough to be prose). The pieces joined are the text ``strip_ui_only_content`` would return,
    apart from the whitespace around removed parts."""

    def __init__(self) -> None:
        self._unsettled = ""

    def feed(self, piece: str) -> str:
        self._unsettled += piece
        start = _open_start(self._unsettled)
        settled, self._unsettled = (
            (self._unsettled, "") if start is None else (self._unsettled[:start], self._unsettled[start:])
        )
        return _strip_ui_only_markup(settled)

    def finish(self) -> str:
        rest, self._unsettled = self._unsettled, ""
        return _strip_ui_only_markup(rest)
