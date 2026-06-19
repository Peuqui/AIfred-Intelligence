"""Export mixin – chat export as standalone HTML."""

from __future__ import annotations

import re

import reflex as rx


class ExportMixin(rx.State, mixin=True):
    """Handles exporting the chat history as a standalone HTML file."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _build_and_save_export(self) -> tuple[str, str] | None:
        """Build the standalone HTML export, save it, and return
        ``(preview_url, download_filename)`` — or ``None`` if there is no chat.

        SSOT for both export paths: :meth:`share_chat` (open in a new tab,
        for viewing / browser-PDF) and :meth:`download_chat` (download the
        self-contained .html file, for sharing as a file).
        """
        from datetime import datetime

        import mistune

        from ..lib.formatting import _save_html_to_assets

        # Create markdown renderer with table support and URL auto-linking
        md = mistune.create_markdown(
            plugins=["table", "strikethrough", "url"],
        )

        _ch = self._chat_sub()
        if not _ch.chat_history:
            self.add_debug("⚠️ No chat to export")  # type: ignore[attr-defined]
            return None

        # Build HTML document
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        chat_title: str = self.current_session_title or ""  # type: ignore[attr-defined]

        # Get username for display
        display_name = self.user_name if self.user_name else "User"  # type: ignore[attr-defined]

        # Import localization for failed sources + export toolbar labels
        from ..lib.prompt_loader import get_language

        current_lang = get_language()
        if current_lang == "auto":
            current_lang = "de"

        html_parts: list[str] = [
            self._get_export_html_header(timestamp, chat_title, current_lang),
        ]

        for msg in _ch.chat_history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            agent = msg.get("agent", "aifred")
            mode = msg.get("mode", "")
            metadata = msg.get("metadata", {})

            if role == "user":
                self._export_user_message(
                    content, display_name, md, html_parts,
                )

            elif role == "assistant":
                self._export_assistant_message(
                    content, agent, mode, metadata,
                    current_lang, md, html_parts,
                )

        html_parts.append(self._get_export_html_footer())
        html_content = "\n".join(html_parts)

        # Save HTML file and get URL (with chat title for filename)
        preview_url = _save_html_to_assets(html_content, chat_title)

        # A clean ASCII download filename (the on-disk name carries an emoji +
        # spaces, which URL-encode badly in a download attribute).
        safe_title = re.sub(r'[^\w\- ]', '', chat_title).strip().replace(" ", "_")[:50]
        download_filename = f"AIfred-Chat-{safe_title}.html" if safe_title else "AIfred-Chat.html"

        self.add_debug(  # type: ignore[attr-defined]
            f"📋 Chat exported as HTML ({len(_ch.chat_history)} messages)",
        )
        return preview_url, download_filename

    def share_chat(self):
        """Share chat history – export as HTML and open in a new browser tab.

        For viewing and the browser's "Save as PDF" path. The exported HTML
        carries a print stylesheet + a beforeprint hook that expand all
        collapsibles, so the generated PDF includes the web-source URLs.
        """
        result = self._build_and_save_export()
        if not result:
            return
        preview_url, _ = result
        return rx.call_script(f'window.open("{preview_url}", "_blank");')

    def download_chat(self):
        """Download the chat as a self-contained .html file.

        Fetches the saved export as a Blob and triggers a real file download
        (instead of opening a tab). The file embeds images/audio as Base64 and
        keeps the collapsibles interactive — ideal for sharing the file itself
        (e.g. on mobile, where Firefox can only share a URL, not a page)."""
        result = self._build_and_save_export()
        if not result:
            return
        preview_url, download_filename = result
        # fetch → Blob → <a download> is the most reliable cross-device way to
        # force a real download (same-origin URL; works on Firefox mobile).
        js = (
            f'fetch("{preview_url}").then(function(r){{return r.blob();}})'
            f'.then(function(b){{'
            f'var a=document.createElement("a");'
            f'a.href=URL.createObjectURL(b);'
            f'a.download="{download_filename}";'
            f'document.body.appendChild(a);a.click();a.remove();'
            f'setTimeout(function(){{URL.revokeObjectURL(a.href);}},1000);'
            f'}});'
        )
        return rx.call_script(js)

    # ------------------------------------------------------------------
    # Private helpers – message rendering
    # ------------------------------------------------------------------

    def _export_user_message(
        self,
        content: str,
        display_name: str,
        md: object,
        html_parts: list[str],
    ) -> None:
        """Render a single user message into *html_parts*."""
        from ..lib.vision_utils import load_image_url_as_base64

        user_msg = content
        if not user_msg or not user_msg.strip():
            return

        # Convert markdown (for italic metadata like "*(Decision: 0,2s)*")
        user_msg_html = self._convert_markdown_preserve_html(user_msg, md)

        # Embed images as Base64 for portable HTML export
        img_src_pattern = r'<img\s+src="([^"]*/_upload/[^"]+)"'
        img_matches = re.findall(img_src_pattern, user_msg_html)
        for img_url in img_matches:
            base64_uri = load_image_url_as_base64(img_url)
            if base64_uri:
                user_msg_html = user_msg_html.replace(
                    f'src="{img_url}"', f'src="{base64_uri}"',
                )

        html_parts.append(f'''
                <div class="message user-message">
                    <div class="message-header">{display_name} 🙋</div>
                    <div class="message-content">{user_msg_html}</div>
                </div>
                ''')

    def _export_assistant_message(  # noqa: PLR0912, PLR0915
        self,
        content: str,
        agent: str,
        mode: str,
        metadata: dict,
        current_lang: str,
        md: object,
        html_parts: list[str],
    ) -> None:
        """Render a single assistant message into *html_parts*."""
        from ..lib.audio_processing import load_audio_url_as_base64

        ai_msg = content
        if not ai_msg or not ai_msg.strip():
            return

        # Extract sources from metadata or embedded comments
        used_sources_data = metadata.get("used_sources", [])
        failed_sources_data = metadata.get("failed_sources", [])

        ai_msg = self._extract_embedded_sources(
            ai_msg, used_sources_data, failed_sources_data,
        )

        # Build sources HTML
        content_has_sources_collapsible = (
            "Web-Quellen" in ai_msg or "Web Sources" in ai_msg
        ) and "<details" in ai_msg

        sources_html = self._build_sources_html(
            used_sources_data, failed_sources_data,
            current_lang, content_has_sources_collapsible,
        )

        ai_msg_stripped = ai_msg.strip()

        # Determine agent styling
        agent_class, header, ai_msg_content = self._determine_agent_styling(
            ai_msg_stripped, agent, mode,
        )

        # Convert markdown to HTML
        ai_msg_html = self._convert_markdown_preserve_html(ai_msg_content, md)

        # Add line break after marker spans
        ai_msg_html = re.sub(r'(</span>)(?!<br>)', r'\1<br><br>', ai_msg_html)

        # Embed sandbox HTML inline for portable export (iframe src → srcdoc)
        ai_msg_html = self._embed_sandbox_html(ai_msg_html)

        # Embed sandbox images as Base64 for portable export
        ai_msg_html = self._embed_sandbox_images(ai_msg_html)

        # Embed audio as Base64 for portable HTML export
        audio_html = ""
        audio_urls = metadata.get("audio_urls", [])
        if audio_urls:
            playback_rate = (
                self.tts_agent_voices[agent]["speed"].replace("x", "")  # type: ignore[attr-defined]
            )
            audio_players = []
            for audio_url in audio_urls:
                base64_uri = load_audio_url_as_base64(audio_url)
                if base64_uri:
                    audio_players.append(
                        f'<audio controls src="{base64_uri}" preload="metadata" '
                        f'onloadedmetadata="this.playbackRate={playback_rate}">'
                        f'</audio>',
                    )
            if audio_players:
                audio_html = (
                    f'<div class="message-audio">{"".join(audio_players)}</div>'
                )

        html_parts.append(f'''
                <div class="message {agent_class}">
                    <div class="message-header">{header}</div>
                    {sources_html}
                    <div class="message-content">{ai_msg_html}</div>
                    {audio_html}
                </div>
                ''')

    # ------------------------------------------------------------------
    # Private helpers – source extraction & rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_embedded_sources(
        ai_msg: str,
        used_sources_data: list,
        failed_sources_data: list,
    ) -> str:
        """Extract USED_SOURCES / FAILED_SOURCES comments from *ai_msg*.

        Mutates *used_sources_data* / *failed_sources_data* in place and
        returns the cleaned message text.
        """
        import json as json_mod

        used_pattern = r'<!--USED_SOURCES:(\[.*?\])-->\n?'
        used_match = re.search(used_pattern, ai_msg, re.DOTALL)
        if used_match:
            try:
                embedded = json_mod.loads(used_match.group(1))
                if embedded:
                    used_sources_data.clear()
                    used_sources_data.extend(embedded)
                ai_msg = re.sub(used_pattern, '', ai_msg, count=1)
            except json_mod.JSONDecodeError:  # noqa: BLE001
                pass

        failed_pattern = r'<!--FAILED_SOURCES:(\[.*?\])-->\n?'
        failed_match = re.search(failed_pattern, ai_msg, re.DOTALL)
        if failed_match:
            try:
                embedded = json_mod.loads(failed_match.group(1))
                if embedded:
                    failed_sources_data.clear()
                    failed_sources_data.extend(embedded)
                ai_msg = re.sub(failed_pattern, '', ai_msg, count=1)
            except json_mod.JSONDecodeError:
                pass

        return ai_msg

    @staticmethod
    def _build_sources_html(
        used_sources_data: list,
        failed_sources_data: list,
        current_lang: str,
        content_has_sources_collapsible: bool,
    ) -> str:
        """Build the collapsible web-sources HTML block."""
        total_sources = len(used_sources_data) + len(failed_sources_data)
        if total_sources == 0 or content_has_sources_collapsible:
            return ""

        # Header text
        if current_lang == "de":
            summary_text = f"{total_sources} Web-Quellen"
            if failed_sources_data:
                summary_text += f" ({len(failed_sources_data)} fehlgeschlagen)"
            words_label = "Wörter"
            sorted_label = "Sortiert nach Relevanz"
        else:
            summary_text = f"{total_sources} Web Sources"
            if failed_sources_data:
                summary_text += f" ({len(failed_sources_data)} failed)"
            words_label = "words"
            sorted_label = "Sorted by relevance"

        # Combine and sort by rank_index
        all_sources: list[dict] = []
        for src in used_sources_data:
            all_sources.append({
                "url": src.get("url", ""),
                "word_count": src.get("word_count", 0),
                "rank_index": src.get("rank_index", 999),
                "success": True,
            })
        for src in failed_sources_data:
            all_sources.append({
                "url": src.get("url", ""),
                "error": src.get("error", "Unknown"),
                "rank_index": src.get("rank_index", 999),
                "success": False,
            })
        all_sources.sort(key=lambda x: x.get("rank_index", 999))

        sources_list: list[str] = []
        for src in all_sources:
            url = src.get("url", "Unknown URL")
            if src.get("success"):
                word_count = src.get("word_count", 0)
                sources_list.append(
                    f'<li class="used-source"><span class="source-icon">✓</span>'
                    f'<a href="{url}" target="_blank">{url}</a> '
                    f'<span class="source-info">({word_count} {words_label})</span></li>',
                )
            else:
                error = src.get("error", "Unknown error")
                sources_list.append(
                    f'<li class="failed-source"><span class="source-icon">✗</span>'
                    f'<a href="{url}" target="_blank">{url}</a> '
                    f'<span class="failed-error">({error})</span></li>',
                )

        return f'''
                    <details class="sources-collapsible" style="font-size: 0.9em; margin-bottom: 1em; margin-top: 0.2em;">
                        <summary style="cursor: pointer; font-weight: bold; color: #aaa;">🔗 {summary_text}</summary>
                        <ul class="sources-list">
                            {"".join(sources_list)}
                        </ul>
                        <p style="font-size: 11px; font-style: italic; color: #7d8590; margin-top: 6px;">{sorted_label}</p>
                    </details>
                    '''

    @staticmethod
    def _determine_agent_styling(
        ai_msg_stripped: str,
        agent: str,
        mode: str,
    ) -> tuple[str, str, str]:
        """Return *(agent_class, header, ai_msg_content)* for the message."""
        if mode == "summary" or ai_msg_stripped.startswith("[📊"):
            return "summary-message", "📊 Summary", ai_msg_stripped

        if agent == "sokrates" or ai_msg_stripped.startswith("🏛️"):
            mode_match = re.match(r'🏛️\s*\[([^\]]+)\]', ai_msg_stripped)
            if mode_match:
                mode_text = f" ({mode_match.group(1)})"
                content = re.sub(
                    r'^🏛️\s*\[[^\]]+\]\s*', '', ai_msg_stripped,
                )
            else:
                mode_text = ""
                content = ai_msg_stripped.lstrip("🏛️").lstrip()
            return "sokrates-message", f"🏛️ Sokrates{mode_text}", content

        if agent == "salomo" or ai_msg_stripped.startswith("👑"):
            mode_match = re.match(r'👑\s*\[([^\]]+)\]', ai_msg_stripped)
            if mode_match:
                mode_text = f" ({mode_match.group(1)})"
                content = re.sub(
                    r'^👑\s*\[[^\]]+\]\s*', '', ai_msg_stripped,
                )
            else:
                mode_text = ""
                content = ai_msg_stripped.lstrip("👑").lstrip()
            return "salomo-message", f"👑 Salomo{mode_text}", content

        # Default: AIfred
        mode_match = re.match(r'🎩\s*\[([^\]]+)\]', ai_msg_stripped)
        if mode_match:
            mode_text = f" ({mode_match.group(1)})"
            content = re.sub(
                r'^🎩\s*\[[^\]]+\]\s*', '', ai_msg_stripped,
            )
        else:
            mode_text = ""
            content = (
                ai_msg_stripped.lstrip("🎩").lstrip()
                if ai_msg_stripped.startswith("🎩")
                else ai_msg_stripped
            )
        return "aifred-message", f"🎩 AIfred{mode_text}", content

    # ------------------------------------------------------------------
    # Private helpers – sandbox embedding
    # ------------------------------------------------------------------

    @staticmethod
    def _embed_sandbox_html(html: str) -> str:
        """Replace sandbox iframe src URLs with inline srcdoc for portable export."""
        import html as html_mod
        from ..lib.config import SANDBOX_OUTPUT_DIR

        iframe_pattern = re.compile(
            r'<iframe\s+src="(/_upload/sandbox_output/[^"]+)"([^>]*)>'
        )

        def replace_iframe(match: re.Match[str]) -> str:
            url_path = match.group(1)
            attrs = match.group(2)
            # URL: /_upload/sandbox_output/{session_id}/{file}.html
            relative = url_path.replace("/_upload/sandbox_output/", "")
            file_path = SANDBOX_OUTPUT_DIR / relative
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                escaped = html_mod.escape(content)
                # Remove sandbox attr (srcdoc doesn't need it) and src
                attrs_clean = re.sub(r'sandbox="[^"]*"', '', attrs)
                return f'<iframe srcdoc="{escaped}"{attrs_clean}>'
            return match.group(0)  # Keep original if file not found

        return iframe_pattern.sub(replace_iframe, html)

    @staticmethod
    def _embed_sandbox_images(html: str) -> str:
        """Replace sandbox image URLs with inline Base64 for portable export."""
        import base64
        from ..lib.config import SANDBOX_OUTPUT_DIR

        img_pattern = re.compile(
            r'<img\s+src="(/_upload/sandbox_output/[^"]+\.png)"'
        )

        def replace_img(match: re.Match[str]) -> str:
            url_path = match.group(1)
            relative = url_path.replace("/_upload/sandbox_output/", "")
            file_path = SANDBOX_OUTPUT_DIR / relative
            if file_path.exists():
                b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
                return f'<img src="data:image/png;base64,{b64}"'
            return match.group(0)

        return img_pattern.sub(replace_img, html)

    # ------------------------------------------------------------------
    # Private helpers – markdown / HTML conversion
    # ------------------------------------------------------------------

    def _convert_markdown_preserve_html(self, text: str, md: object) -> str:
        """Convert markdown to HTML while preserving existing HTML elements.

        The AI response may contain:
        - Existing HTML (<details>, <span>, etc.) – preserve these
        - Markdown syntax (tables, **bold**, etc.) – convert to HTML

        Strategy: Extract HTML blocks, convert remaining markdown, restore
        HTML blocks.
        """
        # Extract and replace HTML blocks with unique placeholders
        placeholders: dict[str, str] = {}
        counter = [0]

        def extract_tag(tag_name: str, txt: str) -> str:
            """Extract all occurrences of a specific HTML tag."""
            if tag_name in ("img", "br", "hr", "input"):
                pattern = re.compile(
                    rf'<{tag_name}[^>]*(?:/>|>)',
                    re.IGNORECASE,
                )
            else:
                pattern = re.compile(
                    rf'<{tag_name}[^>]*>.*?</{tag_name}>',
                    re.DOTALL | re.IGNORECASE,
                )

            def replace_match(match: re.Match) -> str:  # type: ignore[type-arg]
                placeholder = f"HTML_BLOCK_{counter[0]}"
                placeholders[placeholder] = match.group(0)
                counter[0] += 1
                return placeholder

            return pattern.sub(replace_match, txt)

        text_with_placeholders = text
        for tag in ("details", "div", "span", "table", "a", "img"):
            text_with_placeholders = extract_tag(tag, text_with_placeholders)

        # Convert markdown to HTML
        html_output: str = md(text_with_placeholders)  # type: ignore[operator]

        # Restore preserved HTML blocks
        for placeholder, original_html in placeholders.items():
            html_output = html_output.replace(
                f"<p>{placeholder}</p>", original_html,
            )
            html_output = html_output.replace(
                f"<p>{placeholder}\n</p>", original_html,
            )
            html_output = html_output.replace(placeholder, original_html)

        # Convert metrics lines
        metrics_pattern = re.compile(
            r'<em>\(\s*((?:TTFT|Inference):[^)]+)\s*\)</em>',
        )
        html_output = metrics_pattern.sub(
            r'<span class="metrics">( \1 )</span>', html_output,
        )

        # Ensure all links open in new tab
        html_output = self._add_target_blank_to_links(html_output)

        return html_output

    @staticmethod
    def _add_target_blank_to_links(html: str) -> str:
        """Add target="_blank" to all <a> tags that don't have it yet."""

        def add_target(match: re.Match) -> str:  # type: ignore[type-arg]
            tag: str = match.group(0)
            if "target=" in tag:
                return tag
            return tag[:-1] + ' target="_blank" rel="noopener noreferrer">'

        return re.sub(r'<a\s[^>]*>', add_target, html)

    # ------------------------------------------------------------------
    # Private helpers – HTML template
    # ------------------------------------------------------------------

    def _get_export_html_header(self, timestamp: str, title: str = "", lang: str = "de") -> str:
        """Generate HTML header with embedded CSS for chat export."""
        from ..lib.formatting import get_katex_inline_assets

        katex_assets = get_katex_inline_assets()
        katex_css = katex_assets.get("css", "")
        katex_js = katex_assets.get("js", "")
        mhchem_js = katex_assets.get("mhchem_js", "")
        autorender_js = katex_assets.get("autorender_js", "")

        html_title = (
            f"🎩 AIfred - {title}" if title
            else "🎩 AIfred Intelligence - Chat Export"
        )

        # Localized labels for the screen-only expand/collapse toolbar.
        expand_label = "📂 Alles aufklappen" if lang == "de" else "📂 Expand all"
        collapse_label = "📁 Alles zuklappen" if lang == "de" else "📁 Collapse all"

        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html_title}</title>
    <!-- KaTeX CSS mit eingebetteten Fonts -->
    <style>{katex_css}</style>
    <!-- KaTeX JavaScript -->
    <script>{katex_js}</script>
    <script>{mhchem_js}</script>
    <script>{autorender_js}</script>
    <script>
        document.addEventListener("DOMContentLoaded", function() {{
            renderMathInElement(document.body, {{
                delimiters: [
                    {{left: '$$', right: '$$', display: true}},
                    {{left: '$', right: '$', display: false}}
                ],
                throwOnError: false
            }});
        }});
    </script>
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background-color: #0d1117;
            color: #e6edf3;
            line-height: 1.4;
            padding: 20px;
            max-width: 1000px;
            margin: 0 auto;
        }}
        .header {{
            text-align: center;
            padding: 20px;
            border-bottom: 1px solid #30363d;
            margin-bottom: 20px;
        }}
        .header h1 {{
            color: #e67700;
            font-size: 1.8em;
            margin-bottom: 5px;
        }}
        .header .timestamp {{
            color: #7d8590;
            font-size: 0.9em;
        }}
        .message {{
            margin-bottom: 15px;
            padding: 15px;
            border-radius: 8px;
        }}
        .message-header {{
            font-weight: bold;
            margin-bottom: 8px;
            font-size: 0.95em;
        }}
        .message-content {{
            word-wrap: break-word;
        }}
        /* Kompakte Abstände für Content-Elemente */
        .message-content p,
        .message-content ul,
        .message-content ol,
        .message-content table,
        .message-content details {{
            margin: 0 0 1em 0;
        }}
        /* Überschriften: mehr Abstand oben (Trennung), etwas Abstand unten */
        .message-content h1,
        .message-content h2,
        .message-content h3,
        .message-content h4 {{
            margin: 0.9em 0 0.3em 0;
            color: #e6edf3;
        }}
        .message-content h2 {{
            font-size: 1.3em;
            border-bottom: 1px solid #30363d;
            padding-bottom: 0.1em;
        }}
        .message-content h3 {{
            font-size: 1.1em;
        }}
        .message-content ul, .message-content ol {{
            padding-left: 1.5em;
        }}
        .message-content li {{
            margin: 0.1em 0;
        }}
        .message-content table {{
            border-collapse: collapse;
            width: 100%;
            font-size: 0.95em;
            margin: 1em 0;
        }}
        .message-content table th,
        .message-content table td {{
            border: 1px solid #30363d;
            padding: 8px 12px;
            text-align: left;
        }}
        .message-content table th {{
            background-color: #21262d;
            font-weight: bold;
            color: #e6edf3;
        }}
        .message-content table tr:nth-child(even) {{
            background-color: rgba(48, 54, 61, 0.3);
        }}
        /* Embedded chat images */
        .chat-image {{
            max-width: 300px;
            max-height: 300px;
            border-radius: 8px;
            margin: 8px 0;
            cursor: pointer;
            transition: transform 0.2s;
        }}
        .chat-image:hover {{
            transform: scale(1.02);
        }}
        /* User: box with border */
        .user-message {{
            background-color: #21262d;
            border: 1px solid #30363d;
            text-align: right;
            padding-right: 85px;
        }}
        .user-message .chat-image {{
            float: right;
            clear: both;
            margin-left: 10px;
        }}
        .user-message .message-header {{
            color: #c06050;
            text-align: right;
            margin-right: -70px;
        }}
        /* AIfred: box with border + left accent */
        .aifred-message {{
            background-color: #161b22;
            border: 1px solid #30363d;
            border-left: 3px solid #e67700;
        }}
        .aifred-message .message-header {{
            color: #e67700;
        }}
        /* Sokrates: full box with border */
        .sokrates-message {{
            background-color: #161b22;
            border: 1px solid #30363d;
            border-left: 3px solid #a371f7;
        }}
        .sokrates-message .message-header {{
            color: #a371f7;
        }}
        /* Salomo: full box with border */
        .salomo-message {{
            background-color: #161b22;
            border: 1px solid #30363d;
            border-left: 3px solid #d29922;
        }}
        .salomo-message .message-header {{
            color: #d29922;
        }}
        .summary-message {{
            background-color: #1c1c1c;
            border-left: 3px solid #7d8590;
        }}
        .summary-message .message-header {{
            color: #7d8590;
        }}
        /* Audio player styling */
        .message-audio {{
            margin-top: 12px;
            padding-top: 10px;
            border-top: 1px solid #30363d;
        }}
        .message-audio audio {{
            width: 100%;
            max-width: 400px;
            height: 36px;
            border-radius: 8px;
        }}
        /* Collapsible details styling */
        details {{
            border: 1px solid #30363d;
            border-radius: 6px;
            background-color: #0d1117;
        }}
        summary {{
            cursor: pointer;
            padding: 8px;
            font-weight: bold;
            color: #7d8590;
            background-color: #161b22;
            border-radius: 6px 6px 0 0;
        }}
        summary:hover {{
            background-color: #21262d;
        }}
        details[open] summary {{
            border-bottom: 1px solid #30363d;
            border-radius: 6px 6px 0 0;
        }}
        details > div {{
            padding: 8px;
        }}
        .thinking-compact {{
            color: #aaa;
            font-size: 0.9em;
            line-height: 1.3;
        }}
        .thinking-compact p {{
            margin: 0.3em 0;
        }}
        /* Web Sources Collapsible */
        .sources-collapsible {{
            margin-bottom: 12px;
            border: 1px solid #30363d;
            border-radius: 6px;
        }}
        .sources-collapsible summary {{
            color: #8b949e;
            background-color: rgba(139, 148, 158, 0.1);
            padding: 8px 12px;
            cursor: pointer;
            border-radius: 6px;
        }}
        .sources-collapsible summary:hover {{
            background-color: rgba(139, 148, 158, 0.2);
        }}
        .sources-list {{
            list-style: none;
            padding: 8px 12px;
            margin: 0;
        }}
        .sources-list li {{
            padding: 4px 0;
            border-bottom: 1px solid #30363d;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .sources-list li:last-child {{
            border-bottom: none;
        }}
        .sources-list .source-icon {{
            font-size: 12px;
            width: 16px;
            text-align: center;
        }}
        .sources-list .used-source .source-icon {{
            color: #4ade80;
        }}
        .sources-list .failed-source .source-icon {{
            color: #d29922;
        }}
        .sources-list a {{
            color: #56d4dd;
            text-decoration: underline;
            word-break: break-all;
        }}
        .sources-list a:hover {{
            color: #a0f0ff;
        }}
        .source-info {{
            color: #7d8590;
            font-size: 0.85em;
        }}
        .failed-error {{
            color: #7d8590;
            font-size: 0.85em;
            font-style: italic;
        }}
        /* Footer */
        .footer {{
            text-align: center;
            padding: 20px;
            border-top: 1px solid #30363d;
            margin-top: 20px;
            color: #7d8590;
            font-size: 0.85em;
        }}
        .footer a {{
            color: #56d4dd;
            text-decoration: underline;
        }}
        .footer a:hover {{
            color: #a0f0ff;
        }}
        /* Global link styling (for embedded content) */
        a {{
            color: #56d4dd;
            text-decoration: underline;
        }}
        a:hover {{
            color: #a0f0ff;
        }}
        /* Italic text (normal markdown *text*) */
        em {{
            font-style: italic;
            color: inherit;
        }}
        /* Metrics styling (wrapped in .metrics class) */
        .metrics {{
            color: #7d8590;
            font-style: normal;
            font-size: 0.85em;
            display: block;
            margin-top: 8px;
        }}
        /* Code blocks */
        pre, code {{
            background-color: #161b22;
            border-radius: 4px;
            font-family: 'Courier New', Consolas, monospace;
        }}
        pre {{
            padding: 8px;
            overflow-x: auto;
            margin: 0.3em 0;
        }}
        code {{
            padding: 2px 5px;
        }}
        /* Tables (from markdown) */
        th, td {{
            border: 1px solid #30363d;
            padding: 6px 10px;
            text-align: left;
        }}
        th {{
            background-color: #21262d;
            font-weight: bold;
            color: #e6edf3;
        }}
        tr:nth-child(even) {{
            background-color: #161b22;
        }}
        tr:hover {{
            background-color: #21262d;
        }}
        /* Bold and italic */
        strong {{
            color: #f0f6fc;
        }}
        /* KaTeX Block-Formeln zentrieren */
        .katex-display {{
            margin: 0.5em 0;
        }}
        /* Screen-only toolbar: expand/collapse all collapsibles. Handy before
           a manual "Save as PDF" on browsers that don't fire beforeprint
           (e.g. Firefox mobile). Hidden in the printed output. */
        .export-toolbar {{
            text-align: center;
            margin-bottom: 16px;
        }}
        .export-toolbar button {{
            background: #21262d;
            color: #58a6ff;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 8px 14px;
            margin: 0 4px;
            cursor: pointer;
            font-size: 0.9em;
        }}
        .export-toolbar button:hover {{
            background: #30363d;
        }}
        /* Print / PDF: light background (saves ink + readable on paper) and —
           crucially — expand every <details> so the web-source URLs and
           thinking blocks actually land in the generated PDF. A closed
           <details> is otherwise omitted from print. The toolbar is dropped. */
        @media print {{
            body {{
                background: #fff;
                color: #111;
            }}
            .header, .footer {{
                border-color: #ccc;
            }}
            .header h1 {{
                color: #b45309;
            }}
            .message,
            .aifred-message, .sokrates-message, .salomo-message,
            .user-message, .summary-message {{
                background: #fff !important;
                border: 1px solid #ccc !important;
            }}
            details, summary,
            .sources-collapsible, .sources-collapsible summary {{
                background: #fff !important;
                color: #333 !important;
            }}
            details > *:not(summary) {{
                display: block !important;
            }}
            a, .sources-list a {{
                color: #0a58ca !important;
            }}
            .export-toolbar {{
                display: none !important;
            }}
        }}
    </style>
    <script>
        // Expand all collapsibles before printing so the web-source URLs and
        // thinking blocks land in the PDF, then restore the previously-closed
        // ones afterwards (keeps the on-screen view tidy). Browsers that don't
        // fire beforeprint (e.g. Firefox mobile) fall back to the @media print
        // CSS above and the manual toolbar buttons below.
        window.addEventListener('beforeprint', function() {{
            document.querySelectorAll('details').forEach(function(d) {{
                if (!d.open) {{ d.setAttribute('data-was-closed', '1'); d.open = true; }}
            }});
        }});
        window.addEventListener('afterprint', function() {{
            document.querySelectorAll('details[data-was-closed]').forEach(function(d) {{
                d.open = false; d.removeAttribute('data-was-closed');
            }});
        }});
    </script>
</head>
<body>
    <div class="header">
        <h1>🎩 AIfred Intelligence</h1>
        <div class="timestamp">Chat Export • {timestamp}</div>
    </div>
    <div class="export-toolbar">
        <button onclick="document.querySelectorAll('details').forEach(function(d){{d.open=true;}})">{expand_label}</button>
        <button onclick="document.querySelectorAll('details').forEach(function(d){{d.open=false;}})">{collapse_label}</button>
    </div>
'''

    @staticmethod
    def _get_export_html_footer() -> str:
        """Generate HTML footer for chat export."""
        return '''
    <div class="footer">
        <p>Exported from <a href="https://github.com/Peuqui/AIfred-Intelligence" target="_blank">AIfred Intelligence</a></p>
        <p>AI at your service • Multi-Agent Debate System</p>
    </div>
</body>
</html>
'''
