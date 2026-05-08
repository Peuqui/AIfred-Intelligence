"""Markdown rendering helpers for outbound channel messages.

LLM agents produce Markdown — the browser UI renders it natively, but
external channels (Email, Telegram, EPIM, …) cannot. Each channel plugin
calls one of the helpers below to convert the agent output into a format
its destination understands:

* :func:`md_to_html` — full HTML (for email's ``text/html`` part).
* :func:`md_to_plain` — Markdown markers stripped, structure preserved
  (for email's ``text/plain`` fallback, Telegram, EPIM, …).

Pure functions: input string → output string. No state, no I/O.
"""

from __future__ import annotations

import re

import mistune

# ──────────────────────────────────────────────────────────────────────
# HTML
# ──────────────────────────────────────────────────────────────────────

# Singleton renderer — mistune is thread-safe for read-only conversions.
# Plugins enable GFM-style features the LLM produces routinely.
_HTML_RENDERER = mistune.create_markdown(
    escape=True,                 # XSS protection: escape HTML inside Markdown
    renderer="html",
    plugins=["table", "strikethrough", "task_lists", "url"],
)


def md_to_html(md: str) -> str:
    """Render Markdown to HTML.

    Used by the email channel for the ``text/html`` MIME part. Inline
    HTML inside the Markdown source is escaped so a misbehaving agent
    cannot inject scripts into the recipient's mail client.
    """
    if not md:
        return ""
    return str(_HTML_RENDERER(md))


# ──────────────────────────────────────────────────────────────────────
# Plain text
# ──────────────────────────────────────────────────────────────────────

# Order matters: outermost markers first (code fences, headers) then
# inline (bold, italic). Each rule keeps the content, drops the marker.
_PLAIN_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Fenced code blocks: drop the ``` lines, keep the content.
    (re.compile(r"^```[\w+-]*\n", re.MULTILINE), ""),
    (re.compile(r"^```\s*$", re.MULTILINE), ""),
    # Headers: drop leading #'s plus the space after them.
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),
    # Setext-style headers (=== / --- under a line) — leave content.
    (re.compile(r"^={3,}\s*$", re.MULTILINE), ""),
    # Bold + italic combos. Triple first so doubles don't eat them.
    (re.compile(r"\*\*\*([^*\n]+)\*\*\*"), r"\1"),
    (re.compile(r"___([^_\n]+)___"), r"\1"),
    (re.compile(r"\*\*([^*\n]+)\*\*"), r"\1"),
    (re.compile(r"__([^_\n]+)__"), r"\1"),
    (re.compile(r"\*([^*\n]+)\*"), r"\1"),
    (re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)"), r"\1"),
    # Strikethrough.
    (re.compile(r"~~([^~\n]+)~~"), r"\1"),
    # Inline code: drop the backticks, keep the text.
    (re.compile(r"`([^`\n]+)`"), r"\1"),
    # Images: ![alt](url) → "[image: alt] (url)".
    (re.compile(r"!\[([^\]]*)\]\(([^)]+)\)"), r"[image: \1] (\2)"),
    # Links: [text](url) → "text (url)" so the recipient sees both.
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r"\1 (\2)"),
    # Blockquotes: drop the leading ">".
    (re.compile(r"^>\s?", re.MULTILINE), ""),
    # Bullet list markers: normalise to "• ".
    (re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE), "• "),
    # Numbered lists: keep "1." / "2." prefix, drop extra whitespace.
    (re.compile(r"^[ \t]*(\d+)\.[ \t]+", re.MULTILINE), r"\1. "),
    # Horizontal rules → an Unicode rule that survives in plain text.
    (re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE), "─" * 40),
    # Collapse 3+ blank lines down to 2.
    (re.compile(r"\n{3,}"), "\n\n"),
)


def md_to_plain(md: str) -> str:
    """Strip Markdown markers, keep readable plain text.

    Tables are intentionally left untouched: the pipe-and-dash form is
    legible in monospace clients (most email/IM clients render mail in
    monospace by default for ``text/plain`` parts), and parsing them
    "properly" without losing alignment requires knowing the font.

    Used by:
    * email channel as ``text/plain`` fallback alongside HTML;
    * telegram, EPIM, and any other channel that expects raw text.
    """
    if not md:
        return ""
    out = md
    for pattern, replacement in _PLAIN_RULES:
        out = pattern.sub(replacement, out)
    return out.strip()
