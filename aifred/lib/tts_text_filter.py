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


def strip_ui_only_content(text: str) -> str:
    """The text without what AIfred's interface adds and the chat hides; stripped. Everything else
    (Markdown, code, links, emojis, ...) is the service's business."""
    cleaned = HTML_COMMENT.sub("", text)
    cleaned = strip_collapsible_blocks(cleaned)
    cleaned = PERFORMANCE_FOOTER.sub("", cleaned)
    cleaned = ROUND_LABEL.sub("", cleaned)
    cleaned = TIMING_PARENTHESES.sub("", cleaned)
    cleaned = NUMBER_PARENTHESES.sub("", cleaned)
    return cleaned.strip()
