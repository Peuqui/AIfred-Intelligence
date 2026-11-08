"""A session as one standalone HTML file: the chat with its Markdown and the bubbles' own HTML parts (thinking,
sources, mode markers), the images of the messages, the spoken audio, the sandbox pages with their JS libraries and
KaTeX, all embedded. Opened in a tab it serves the browser's "Save as PDF" (a print stylesheet opens every
collapsible); downloaded, it is a file to pass on. The page itself is ``chat_export.html``.

Ported from the Reflex export (state/_export_mixin.py, 7cbf8178). Changed on the way: an agent's bubble is headed with
its own name and emoji (the old export knew Sokrates and Salomo by name); an embedded sandbox page keeps its
``sandbox`` attribute (without it the page's script ran in AIfred's origin, next to the API) and carries the JS
libraries it loads from /vendor/ (a standalone file has no server behind it); the texts come from the translations.
"""

from __future__ import annotations

import base64
import html as html_module
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any

TEMPLATE_FILE = Path(__file__).with_name("chat_export.html")
SANDBOX_OUTPUT_PATH = "/_upload/sandbox_output/"
# Tags whose HTML the Markdown renderer must leave alone: the bubbles carry them ready-made.
_KEPT_TAGS = ("details", "div", "span", "table", "a", "img")
_VOID_TAGS = ("img", "br", "hr", "input")


class _PageTemplate(Template):
    delimiter = "@@"   # the page is CSS and JS: neither uses "@@"


@dataclass(frozen=True)
class ChatExport:
    url: str             # the saved page under /_upload/html_preview/
    download_name: str   # a plain ASCII file name for the download
    messages: int


def export_session(session_id: str) -> ChatExport:
    """Build and save the export of the session's chat.

    Raises:
        Invalid: the session has no chat yet.
    """
    from .formatting import _save_html_to_assets
    from .logging_utils import log_message
    from .prompt_loader import get_language
    from .session_storage import get_session_title, load_session
    from .settings import persisted_settings
    from .user_error import Invalid

    history: list[dict[str, Any]] = (load_session(session_id) or {}).get("data", {}).get("chat_history", [])
    if not history:
        raise Invalid("no_chat_to_export")
    lang = get_language()
    title = get_session_title(session_id) or ""
    user_name = str(persisted_settings().get("user_name") or "")
    markdown = _markdown_renderer()
    messages = [_message(entry, session_id, user_name, lang, markdown) for entry in history]
    page = _page(title, lang, "\n".join(message for message in messages if message))
    url = _save_html_to_assets(page, title)
    log_message(f"📋 Chat exported as HTML ({len(history)} messages): {url}")
    return ChatExport(url=url, download_name=_download_name(title), messages=len(history))


def _download_name(title: str) -> str:
    """``AIfred-Chat-<title>.html`` in plain ASCII: the file name on disk carries an emoji and spaces, which a
    download attribute takes badly."""
    safe = re.sub(r"[^\w\- ]", "", title, flags=re.ASCII).strip().replace(" ", "_")[:50]
    return f"AIfred-Chat-{safe}.html" if safe else "AIfred-Chat.html"


# ── The page ─────────────────────────────────────────────────────────────


def _page(title: str, lang: str, messages: str) -> str:
    from ..theme import css_variables
    from .formatting import get_katex_inline_assets
    from .i18n import t

    katex = get_katex_inline_assets()
    return _PageTemplate(TEMPLATE_FILE.read_text(encoding="utf-8")).substitute(
        lang=lang,
        title=html_module.escape(f"🎩 AIfred - {title}" if title else t("chat_export_title", lang=lang)),
        katex_css=katex.get("css", ""),
        katex_js=katex.get("js", ""),
        mhchem_js=katex.get("mhchem_js", ""),
        autorender_js=katex.get("autorender_js", ""),
        theme_css=css_variables(),
        subtitle=html_module.escape(t("chat_export_subtitle", lang=lang, time=datetime.now().strftime("%Y-%m-%d %H:%M"))),
        expand_label=t("chat_export_expand_all", lang=lang),
        collapse_label=t("chat_export_collapse_all", lang=lang),
        messages=messages,
        exported_from=t("chat_export_exported_from", lang=lang),
        tagline=t("chat_export_tagline", lang=lang),
    )


def _message(entry: dict[str, Any], session_id: str, user_name: str, lang: str, markdown: Callable[[str], str]) -> str:
    content = str(entry.get("content", ""))
    if not content.strip():
        return ""
    if entry.get("role") == "user":
        return _user_message(content, session_id, user_name, lang, markdown)
    return _agent_message(entry, content, session_id, lang, markdown)


def _user_message(content: str, session_id: str, user_name: str, lang: str, markdown: Callable[[str], str]) -> str:
    from .i18n import t
    from .vision_utils import load_image_url_as_base64

    body = _markdown_keeping_html(content, markdown)
    for url in re.findall(r'<img\s+src="([^"]*/_upload/[^"]+)"', body):
        embedded = load_image_url_as_base64(url, session_id)
        if embedded:
            body = body.replace(f'src="{url}"', f'src="{embedded}"')
    name = html_module.escape(user_name or t("chat_export_user", lang=lang))
    return (f'<div class="message user-message"><div class="message-header">{name} 🙋</div>'
            f'<div class="message-content">{body}</div></div>')


def _agent_message(entry: dict[str, Any], content: str, session_id: str, lang: str, markdown: Callable[[str], str]) -> str:
    from .audio_processing import load_audio_url_as_base64
    from .i18n import t

    if entry.get("mode") == "summary":
        css_class, header = "summary-message", t("chat_export_summary", lang=lang)
    else:
        css_class = "agent-message"
        header = html_module.escape(f"{entry.get('agent_emoji', '')} {entry.get('agent_display_name', '')}".strip())
    body = _markdown_keeping_html(content.strip(), markdown)
    body = re.sub(r"(</span>)(?!<br>)", r"\1<br><br>", body)   # a line after each mode marker
    body = _embed_sandbox_images(_embed_sandbox_pages(body, session_id), session_id)
    metadata = entry.get("metadata", {})
    # The speed is in the audio already; the bubble's rate is the replay rate.
    rate = str(metadata.get("playback_rate", "1.0x")).replace("x", "")
    players = [
        f'<audio controls src="{embedded}" preload="metadata" onloadedmetadata="this.playbackRate={rate}"></audio>'
        for embedded in map(load_audio_url_as_base64, metadata.get("audio_urls", [])) if embedded
    ]
    audio = f'<div class="message-audio">{"".join(players)}</div>' if players else ""
    return (f'<div class="message {css_class}"><div class="message-header">{header}</div>'
            f'<div class="message-content">{body}</div>{audio}</div>')


# ── Markdown around the bubbles' own HTML ────────────────────────────────


def _markdown_renderer() -> Callable[[str], str]:
    """Markdown with tables, strikethrough and bare URLs as links, to HTML."""
    import mistune

    markdown = mistune.create_markdown(plugins=["table", "strikethrough", "url"])

    def render(text: str) -> str:
        html = markdown(text)
        assert isinstance(html, str), "the HTML renderer gives text (an AST renderer would give a list)"
        return html

    return render


def _markdown_keeping_html(text: str, markdown: Callable[[str], str]) -> str:
    """Markdown to HTML, the HTML parts of the bubble (thinking, sources, markers, images) left as they are: they
    go out as placeholders and come back after the conversion."""
    kept: dict[str, str] = {}

    def keep(match: re.Match[str]) -> str:
        placeholder = f"HTML_BLOCK_{len(kept)}"
        kept[placeholder] = match.group(0)
        return placeholder

    for tag in _KEPT_TAGS:
        pattern = (rf"<{tag}[^>]*(?:/>|>)" if tag in _VOID_TAGS else rf"<{tag}[^>]*>.*?</{tag}>")
        text = re.sub(pattern, keep, text, flags=re.DOTALL | re.IGNORECASE)
    result = markdown(text)
    for placeholder, original in kept.items():
        result = result.replace(f"<p>{placeholder}</p>", original).replace(f"<p>{placeholder}\n</p>", original)
        result = result.replace(placeholder, original)
    # The metrics line under a reply ("*( TTFT: … )*") in its own style.
    result = re.sub(r"<em>\(\s*((?:TTFT|Inference):.*?)\s*\)</em>", r'<span class="metrics">( \1 )</span>', result)
    return re.sub(r"<a\s[^>]*>", _link_in_new_tab, result)


def _link_in_new_tab(match: re.Match[str]) -> str:
    tag = match.group(0)
    return tag if "target=" in tag else tag[:-1] + ' target="_blank" rel="noopener noreferrer">'


# ── Sandbox output embedded ──────────────────────────────────────────────


def _sandbox_file(url_path: str, session_id: str) -> Path | None:
    """The file behind a sandbox output URL of this session, None when it is gone or the URL leads out of the
    session's folder (sandbox output belongs to its session, as for every other reader)."""
    from .config import SANDBOX_OUTPUT_DIR

    root = (SANDBOX_OUTPUT_DIR / session_id).resolve()
    path = (SANDBOX_OUTPUT_DIR / url_path.removeprefix(SANDBOX_OUTPUT_PATH)).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def _embed_sandbox_pages(body: str, session_id: str) -> str:
    """A sandbox page's iframe gets the page itself (``srcdoc``) instead of its URL and keeps ``sandbox``: its
    script runs, but not in the export's origin."""

    def embed(match: re.Match[str]) -> str:
        path = _sandbox_file(match.group(1), session_id)
        if path is None:
            return match.group(0)
        page = _with_vendor_scripts(path.read_text(encoding="utf-8"))
        return f'<iframe srcdoc="{html_module.escape(page)}"{match.group(2)}>'

    return re.sub(rf'<iframe\s+src="({re.escape(SANDBOX_OUTPUT_PATH)}[^"]+)"([^>]*)>', embed, body)


def _with_vendor_scripts(page: str) -> str:
    """The libraries a sandbox page loads from /vendor/ written into it: the standalone file has no server behind it.
    A ``</script`` inside a library would end its script element early, so it is written as ``<\\/script``."""
    from .config import VENDOR_DIR, VENDOR_URL

    def inline(match: re.Match[str]) -> str:
        library = VENDOR_DIR / Path(match.group(1)).name
        if not library.is_file():
            return match.group(0)
        code = library.read_text(encoding="utf-8").replace("</script", "<\\/script")
        return f"<script>{code}</script>"

    return re.sub(rf'<script\s+src="{re.escape(VENDOR_URL)}/([^"]+)"\s*>\s*</script>', inline, page)


def _embed_sandbox_images(body: str, session_id: str) -> str:
    """A sandbox plot as Base64 in the page."""

    def embed(match: re.Match[str]) -> str:
        path = _sandbox_file(match.group(1), session_id)
        if path is None:
            return match.group(0)
        return f'<img src="data:image/png;base64,{base64.b64encode(path.read_bytes()).decode("ascii")}"'

    return re.sub(rf'<img\s+src="({re.escape(SANDBOX_OUTPUT_PATH)}[^"]+\.png)"', embed, body)
