"""
Formatting Utilities - UI text formatting functions

This module provides formatting functions for displaying AI responses
and thinking processes in the Reflex UI.
"""

import re
import uuid
import atexit
import threading
from pathlib import Path
from collections import OrderedDict
from .logging_utils import log_message
from .config import get_xml_tag_config, BACKEND_URL, DATA_DIR, PROJECT_ROOT, HTML_PREVIEW_MAX_FILES
from .html_tags import HTML_TAG_BLACKLIST  # HTML tags to exclude from XML processing
from datetime import datetime

# HTML Preview: Path to data/html_preview directory
# Located in data/ which is excluded from hot-reload
# Served via /_upload/ endpoint
_HTML_PREVIEW_DIR = DATA_DIR / "html_preview"

# LRU Cache for HTML preview files
_html_file_cache: OrderedDict[str, Path] = OrderedDict()
_html_cache_lock = threading.Lock()

# Global UI locale for number formatting (set by AIState on language change)
_ui_locale: str = "de"


def set_ui_locale(locale: str):
    """Set the global UI locale for number formatting (called by AIState)"""
    global _ui_locale
    if locale in ["de", "en"]:
        _ui_locale = locale


def get_ui_locale() -> str:
    """Get the current UI locale"""
    return _ui_locale


def format_number(n: int | float, decimals: int = 0, locale: str | None = None) -> str:
    """
    Format number with locale-aware separators.

    Args:
        n: Number to format (int or float)
        decimals: Number of decimal places (default: 0 for integer formatting)
        locale: Locale for formatting - "de" (German) or "en" (English)
                If None, uses the global UI locale set by set_ui_locale()
                German: dot for thousands, comma for decimals (1.234,56)
                English: comma for thousands, dot for decimals (1,234.56)

    Returns:
        Formatted string with locale-aware number formatting

    Examples:
        >>> format_number(40960)
        '40.960'
        >>> format_number(40960, locale="en")
        '40,960'
        >>> format_number(10.84, 2)
        '10,84'
        >>> format_number(10.84, 2, locale="en")
        '10.84'
    """
    # Use global UI locale if not specified
    if locale is None:
        locale = _ui_locale

    if locale == "en":
        # English: comma for thousands, dot for decimals (Python default)
        if decimals == 0:
            return f"{int(n):,}"
        else:
            return f"{n:,.{decimals}f}"
    else:
        # German (default): dot for thousands, comma for decimals
        if decimals == 0:
            return f"{int(n):,}".replace(",", ".")
        else:
            formatted = f"{n:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
            return formatted


def format_age(seconds: float) -> str:
    """
    Format age in seconds to human-readable format.

    Examples:
        >>> format_age(30)
        '30s'
        >>> format_age(90)
        '1min 30s'
        >>> format_age(3600)
        '1h'
        >>> format_age(7200)
        '2h'
        >>> format_age(86400)
        '1d'
        >>> format_age(90061)
        '1d 1h 1min'
    """
    if seconds < 60:
        return f"{seconds:.0f}s"

    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}min")
    if secs > 0 and days == 0:  # Only show seconds if less than a day
        parts.append(f"{secs}s")

    return " ".join(parts)


def convert_latex_delimiters(text: str) -> str:
    """
    Convert various LaTeX delimiter formats to $...$ syntax for rx.markdown.

    Handles common LLM output formats:
    - \\text{...} → plain text (rx.markdown doesn't support \\text well)
    - \\[...\\] → $$...$$ (block math)
    - \\(...\\) → $...$ (inline math)

    Args:
        text: Text with LaTeX formulas in various formats

    Returns:
        Text with LaTeX converted to $...$ syntax

    Example:
        >>> convert_latex_delimiters("\\text{ATP} + \\text{H}_2\\text{O}")
        'ATP + H_2O'
    """
    if not text:
        return text

    original_text = text

    # 1. Add space before \text{} if missing (after non-space, non-backslash char)
    # Fixes LLM output like "wobei\text{F}" → "wobei \text{F}" → "wobei F"
    text = re.sub(r'([^\s\\])\\text\{', r'\1 \\text{', text)

    # 2. Convert \text{...} to plain text (rx.markdown's KaTeX doesn't render it)
    text = re.sub(r'\\text\{([^}]*)\}', r'\1', text)

    # 3. Convert \[...\] to $$...$$ (LaTeX display mode)
    text = re.sub(r'\\\[(.*?)\\\]', r'$$\1$$', text, flags=re.DOTALL)

    # 4. Convert \(...\) to $...$ (LaTeX inline mode)
    text = re.sub(r'\\\((.*?)\\\)', r'$\1$', text, flags=re.DOTALL)

    # Debug: Log if any conversion happened
    if text != original_text:
        log_message(f"📐 LaTeX: Converted delimiters ({len(original_text)} → {len(text)} chars)")

    return text


def format_metadata(metadata_text: str) -> str:
    """
    Format metadata (inference times, sources, etc.) as italic text in parentheses.

    Args:
        metadata_text: Metadata text, e.g., "Inference: 1.3s    Source: Web Research"
                       (4 spaces as separator between values)

    Returns:
        Markdown-formatted text (italic, in parentheses) with non-breaking spaces

    Example:
        >>> format_metadata("Inference: 1.3s    61.1 tok/s    Source: LLM")
        '*( Inference: 1.3s    61.1 tok/s    Source: LLM )*'  # with non-breaking spaces

    Note:
        Uses Markdown instead of HTML since rx.markdown() escapes inline HTML.
        Italic formatting (*...*) signals meta-information.
        4 normal spaces (group-internal separators) are converted to 4 non-breaking spaces.
        Individual value spaces (e.g. "TTFT: 0.25s") should already use nbsp from callers.
    """
    if not metadata_text:
        return metadata_text

    text = metadata_text.strip()
    # Replace 4 normal spaces with 4 non-breaking spaces (won't collapse)
    text = text.replace("    ", "\u00A0\u00A0\u00A0\u00A0")
    return f'*( {text} )*'


def format_performance_footer(metadata: dict) -> str:
    """Build a performance metadata footer from a metrics dict.

    Used by both browser-path (add_agent_panel) and hub-path (_append_response).

    Args:
        metadata: Dict with keys: ttft, prompt_per_sec, tokens_per_sec,
                  inference_time, source, backend_type (all optional)

    Returns:
        Formatted string like *( TTFT: 0,41s    PP: 466,0 tok/s    ... )*
        or empty string if no metrics.
    """
    if not metadata:
        return ""

    perf_parts: list[str] = []
    info_parts: list[str] = []

    if metadata.get("ttft"):
        perf_parts.append(f"TTFT:\u00A0{format_number(metadata['ttft'], 2)}s")

    if metadata.get("prompt_per_sec"):
        perf_parts.append(f"PP:\u00A0{format_number(metadata['prompt_per_sec'], 1)}\u00A0tok/s")

    if metadata.get("tokens_per_sec"):
        perf_parts.append(f"{format_number(metadata['tokens_per_sec'], 1)}\u00A0tok/s")

    if metadata.get("inference_time"):
        perf_parts.append(f"Inference:\u00A0{format_number(metadata['inference_time'], 1)}s")

    if metadata.get("source"):
        source = metadata["source"]
        backend = metadata.get("backend_type", "")
        source_display = f"{source}\u00A0[{backend}]" if backend else source
        info_parts.append(f"Source:\u00A0{source_display.replace(' ', chr(0xA0))}")

    if not perf_parts and not info_parts:
        return ""

    # Timestamp
    from datetime import datetime as _dt
    time_parts = [_dt.now().strftime("%d.%m.\u00A0\u2014\u00A0%H:%M")]

    groups: list[str] = []
    if perf_parts:
        groups.append("    ".join(perf_parts))
    if info_parts:
        groups.append("    ".join(info_parts))
    if time_parts:
        groups.append("    ".join(time_parts))
    metadata_text = "\u00A0\u00A0\u00A0 ".join(groups)
    return format_metadata(metadata_text)


def build_assistant_chat_entry(
    content: str,
    agent: str = "aifred",
    metadata: dict | None = None,
) -> dict:
    """Build a chat_history dict for an assistant message.

    Single source of truth for the dict shape that the UI renderer expects.
    Used by both the browser path (add_agent_panel) and the hub/channel path
    (_append_response in message_processor).

    Args:
        content: Display content (already formatted, with footer if needed).
        agent: Agent identifier ("aifred", "sokrates", "salomo", etc.)
        metadata: Optional raw metadata dict (stored for export/replay).

    Returns:
        Dict ready to append to chat_history.
    """
    from .agent_config import get_agent_config

    agent_cfg = get_agent_config(agent)
    agent_display_name = agent_cfg.display_name if agent_cfg else agent.capitalize()
    agent_emoji = agent_cfg.emoji if agent_cfg else "\U0001f916"

    return {
        "role": "assistant",
        "content": content,
        "agent": agent,
        "agent_display_name": agent_display_name,
        "agent_emoji": agent_emoji,
        "metadata": metadata or {},
        "timestamp": datetime.now().isoformat(),
        "time_display": datetime.now().strftime("%d.%m. \u2014 %H:%M"),
        "has_audio": False,
        "audio_urls_json": "[]",
    }


def build_inference_metadata(
    ttft: float | None,
    inference_time: float,
    tokens_generated: int,
    tokens_per_sec: float,
    source: str,
    *,
    backend_metrics: dict | None = None,
    tokens_prompt: int = 0,
    history_tokens: int = 0,
    backend_type: str = "",
    agent_label: str = "AIfred-LLM",
    response_chars: int = 0,
) -> tuple[dict, str, str]:
    """
    Central function for inference metadata (chat bubble, debug log, console).

    Calculates PP speed, builds metadata dict + display string + debug message.
    Calls log_message() internally for debug.log output.

    Args:
        ttft: Time To First Token (seconds), None if not measured
        inference_time: Total inference time (seconds)
        tokens_generated: Number of generated tokens
        tokens_per_sec: Generation speed (tok/s)
        source: Source label (e.g. "Own Knowledge (qwen3:4b)")
        backend_metrics: Raw metrics from backend done chunk (has prompt_per_second for Ollama)
        tokens_prompt: Number of prompt tokens (for PP fallback via TTFT)
        history_tokens: LLM history token count (for debug output)
        backend_type: Backend type ("ollama", "llamacpp", "cloud_api", etc.)
        agent_label: Agent name for debug line (e.g. "AIfred-LLM", "Sokrates")
        response_chars: Response text length in chars (for debug output)

    Returns:
        (metadata_dict, metadata_display, debug_msg):
        - metadata_dict: For chat_history / add_agent_panel persistence
        - metadata_display: format_metadata() string for chat bubble embedding
        - debug_msg: Debug "done" line (also logged via log_message())
    """
    # --- PP speed: from backend (Ollama) or TTFT fallback ---
    prompt_per_sec = 0.0
    if backend_metrics:
        prompt_per_sec = backend_metrics.get("prompt_per_second", 0) or 0
    if not prompt_per_sec and ttft and ttft > 0 and tokens_prompt > 0:
        prompt_per_sec = tokens_prompt / ttft

    # --- Metadata dict (for persistence) ---
    metadata_dict: dict = {
        "ttft": ttft,
        "prompt_per_sec": prompt_per_sec,
        "inference_time": inference_time,
        "tokens_per_sec": tokens_per_sec,
        "source": source,
        "backend_type": backend_type,
    }

    # --- Metadata display string (for chat bubble) ---
    # Split into speed metrics (no wrap) and info (wrap allowed before)
    perf_parts: list[str] = []
    info_parts: list[str] = []
    if ttft:
        perf_parts.append(f"TTFT:\u00A0{format_number(ttft, 2)}s")
    if prompt_per_sec:
        perf_parts.append(f"PP:\u00A0{format_number(prompt_per_sec, 1)}\u00A0tok/s")
    perf_parts.append(f"{format_number(tokens_per_sec, 1)}\u00A0tok/s")
    perf_parts.append(f"Inference:\u00A0{format_number(inference_time, 1)}s")
    # Source with backend label (e.g. "Own Knowledge (model) [llamacpp]")
    source_display = f"{source}\u00A0[{backend_type}]" if backend_type else source
    # Replace all spaces within source so it stays as one unbreakable unit
    info_parts.append(f"Source:\u00A0{source_display.replace(' ', chr(0xA0))}")
    # Within groups: "    " → non-breaking spaces (no wrap)
    # Between groups: 3 nbsp + regular space → allows line break on mobile
    from datetime import datetime as _dt
    time_parts = [_dt.now().strftime("%d.%m.\u00A0\u2014\u00A0%H:%M")]

    groups = []
    if perf_parts:
        groups.append("    ".join(perf_parts))
    if info_parts:
        groups.append("    ".join(info_parts))
    if time_parts:
        groups.append("    ".join(time_parts))
    metadata_display = format_metadata("\u00A0\u00A0\u00A0 ".join(groups))

    # --- Debug "done" message ---
    if backend_type == "cloud_api" and tokens_prompt > 0:
        total = tokens_prompt + tokens_generated
        debug_base = (
            f"✅ {agent_label} done ({format_number(inference_time, 1)}s, "
            f"{format_number(tokens_generated)} out / {format_number(total)} total, "
            f"{format_number(tokens_per_sec, 1)} tok/s)"
        )
    else:
        debug_base = (
            f"✅ {agent_label} done ({format_number(inference_time, 1)}s, "
            f"{format_number(tokens_generated)} tok, "
            f"{format_number(tokens_per_sec, 1)} tok/s)"
        )

    suffixes: list[str] = []
    if prompt_per_sec:
        suffixes.append(f"PP: {format_number(prompt_per_sec, 1)} tok/s")
    if response_chars > 0:
        suffixes.append(f"{format_number(response_chars)} chars")
    if history_tokens > 0:
        suffixes.append(f"History: {format_number(history_tokens)} tok")

    # Debug message: base line + suffixes on second line (debug console has white-space: pre)
    debug_msg = debug_base
    if suffixes:
        debug_msg += "\n   (" + " | ".join(suffixes) + ")"

    # NOTE: Caller logs via add_debug() which calls log_message() — no double-logging
    return metadata_dict, metadata_display, debug_msg


def get_timestamp() -> str:
    """
    Returns current timestamp in HH:MM:SS format (like legacy version).

    Returns:
        Formatted timestamp string (e.g., "18:32:33")
    """
    return datetime.now().strftime("%H:%M:%S")


def _save_html_to_assets(html_code: str, title: str = "") -> str:
    """
    Save HTML code as file in uploaded_files/html_preview/ and return URL.

    IMPORTANT: Files are saved outside assets/ to avoid Reflex hot-reload!
    Implements LRU Cache: Maximum 50 files are kept, oldest are deleted.

    Args:
        html_code: The HTML code to save
        title: Optional title for filename (sanitized, max 50 chars)

    Returns:
        Full URL to saved file (e.g., "http://host:8002/_upload/html_preview/abc123.html")
    """
    # Ensure directory exists
    _HTML_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    # Generate filename from title or UUID
    if title:
        # Sanitize title for filename: remove/replace problematic characters
        safe_title = re.sub(r'[<>:"/\\|?*]', '', title)  # Remove forbidden chars
        safe_title = re.sub(r'\s+', '_', safe_title.strip())  # Spaces to underscores
        safe_title = safe_title[:50]  # Limit length
        filename = f"🎩 AIfred - {safe_title}.html"
    else:
        filename = f"{uuid.uuid4().hex[:8]}.html"
    filepath = _HTML_PREVIEW_DIR / filename

    # Save HTML code
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html_code)

    # LRU Cache Management (thread-safe)
    with _html_cache_lock:
        _html_file_cache[filename] = filepath

        # If cache is full, delete oldest file
        if len(_html_file_cache) > HTML_PREVIEW_MAX_FILES:
            oldest_filename, oldest_path = _html_file_cache.popitem(last=False)
            try:
                oldest_path.unlink()
                log_message(f"🗑️ HTML Preview: LRU evicted {oldest_filename} (Cache limit: {HTML_PREVIEW_MAX_FILES})")
            except OSError as e:
                log_message(f"⚠️ HTML Preview: Could not delete {oldest_filename}: {e}")

    log_message(f"🌐 HTML Preview: File saved → {filepath} (Cache: {len(_html_file_cache)}/{HTML_PREVIEW_MAX_FILES})")

    # Return URL - absolute with BACKEND_URL if set, otherwise relative
    # With NGINX: BACKEND_URL="" → relative URL works (NGINX routes to backend)
    # Without NGINX (dev): BACKEND_URL="http://host:8002" → absolute URL to backend
    relative_path = f"/_upload/html_preview/{filename}"
    if BACKEND_URL:
        return f"{BACKEND_URL}{relative_path}"
    return relative_path


@atexit.register
def _cleanup_html_cache():
    """Cleanup on program exit: Delete all HTML preview files from cache"""
    with _html_cache_lock:
        for filepath in _html_file_cache.values():
            try:
                filepath.unlink()
            except OSError:
                pass
        _html_file_cache.clear()


def cleanup_session_html(session_data: dict) -> int:
    """Delete HTML preview files referenced by a session's chat history.

    Scans all messages for html_preview/ URLs and deletes the corresponding files.

    Args:
        session_data: Full session dict (with 'data' → 'chat_history')

    Returns:
        Number of files deleted
    """
    chat_history = session_data.get("data", {}).get("chat_history", [])
    if not chat_history:
        return 0

    # Collect all referenced HTML filenames from message content
    html_pattern = re.compile(r'html_preview/([^"\'<>\s]+\.html)')
    filenames: set[str] = set()
    for msg in chat_history:
        content = msg.get("content", "")
        filenames.update(html_pattern.findall(content))

    deleted = 0
    for filename in filenames:
        filepath = _HTML_PREVIEW_DIR / filename
        if filepath.exists():
            try:
                filepath.unlink()
                deleted += 1
            except OSError:
                pass
            # Also remove from LRU cache
            with _html_cache_lock:
                _html_file_cache.pop(filename, None)

    if deleted:
        log_message(f"🗑️ HTML Preview: Deleted {deleted} file(s) for session cleanup")
    return deleted


# KaTeX inline embedding cache (loaded once, reused for all exports)
_katex_inline_cache: dict[str, str] = {}


def get_katex_inline_assets() -> dict[str, str]:
    """
    Load KaTeX assets and convert them to inline format for portable HTML export.

    Returns dict with:
        - 'css': KaTeX CSS with fonts embedded as Base64 data URLs
        - 'js': KaTeX main JS
        - 'mhchem_js': mhchem extension JS
        - 'autorender_js': auto-render extension JS

    Results are cached after first call.
    """
    import base64

    if _katex_inline_cache:
        return _katex_inline_cache

    katex_dir = PROJECT_ROOT / "assets" / "katex"
    fonts_dir = katex_dir / "fonts"

    # Load CSS and embed fonts as Base64
    css_path = katex_dir / "katex.min.css"
    if css_path.exists():
        css_content = css_path.read_text(encoding='utf-8')

        # Replace font URLs with Base64 data URLs (only woff2 for smaller size)
        for font_file in fonts_dir.glob("*.woff2"):
            font_name = font_file.name
            font_data = base64.b64encode(font_file.read_bytes()).decode('ascii')
            data_url = f"data:font/woff2;base64,{font_data}"
            # Replace all URL patterns for this font
            css_content = css_content.replace(f"url(/katex/fonts/{font_name})", f"url({data_url})")

        # Remove woff and ttf references (browser will use woff2)
        css_content = re.sub(r',url\([^)]+\.woff\)[^,}]*', '', css_content)
        css_content = re.sub(r',url\([^)]+\.ttf\)[^,}]*', '', css_content)

        _katex_inline_cache['css'] = css_content
    else:
        _katex_inline_cache['css'] = ""

    # Load JS files
    js_path = katex_dir / "katex.min.js"
    _katex_inline_cache['js'] = js_path.read_text(encoding='utf-8') if js_path.exists() else ""

    mhchem_path = katex_dir / "mhchem.min.js"
    _katex_inline_cache['mhchem_js'] = mhchem_path.read_text(encoding='utf-8') if mhchem_path.exists() else ""

    autorender_path = katex_dir / "auto-render.min.js"
    _katex_inline_cache['autorender_js'] = autorender_path.read_text(encoding='utf-8') if autorender_path.exists() else ""

    log_message(f"📐 KaTeX: Loaded assets for inline embedding (CSS: {format_number(len(_katex_inline_cache['css'])/1024, 1)}KB)")

    return _katex_inline_cache


def extract_html_previews(text: str, lang: str | None = None) -> tuple[list[str], str]:
    """
    Extract ```html code blocks, save as files, return collapsibles + cleaned text.

    HTML files are saved in data/html_preview/ and can be opened
    via link in a new browser tab. The collapsibles are returned separately
    so the caller can place them at the top of the chat bubble.

    Args:
        text: Text with optional ```html code blocks
        lang: Language for collapsible labels (de/en). If None, uses get_ui_locale()

    Returns:
        Tuple of (collapsibles, cleaned_text):
        - collapsibles: List of HTML preview collapsible strings
        - cleaned_text: Text with ```html blocks removed
    """
    if lang is None:
        lang = get_ui_locale()

    html_block_pattern = r'```html\s*([\s\S]*?)```'

    collapsibles: list[str] = []

    for match in re.finditer(html_block_pattern, text):
        html_code = match.group(1).strip()
        preview_url = _save_html_to_assets(html_code)

        collapsible = f"""<details style="font-size: 0.9em; margin-bottom: 1em; margin-top: 0.2em;">
<summary style="cursor: pointer; font-weight: bold; color: #aaa; position: sticky; top: 0; z-index: 1; background: inherit; padding: 2px 0;">🌐 HTML Preview — <a href="{preview_url}" target="_blank" rel="noopener noreferrer" style="color: #58a6ff; text-decoration: none;" onclick="event.stopPropagation()">Open in Browser</a></summary>
<div style="max-height: 60vh; overflow-y: auto; padding: 0.5em;">

```html
{html_code}
```

</div>
</details>"""
        collapsibles.append(collapsible)

    # Remove ```html blocks from text
    cleaned = re.sub(html_block_pattern, '', text).strip()

    if collapsibles:
        log_message(f"🌐 HTML Code: {len(collapsibles)} preview(s) extracted")

    return collapsibles, cleaned


def fix_orphan_closing_think_tag(text: str) -> str:
    """
    Repair orphaned closing </think> tags (without opening tag).

    Some models (e.g., Qwen3 with enable_thinking=false) output the thinking process
    without opening <think> tag, but with closing </think> tag.

    LOGIC:
    - If <think> AND </think> present → Do nothing (correct tags)
    - If ONLY </think> present (without <think>) → Insert <think> at beginning

    Args:
        text: Text with potentially missing opening tag

    Returns:
        Text with repaired tag (if needed)

    Example:
        Input:  "Okay, let me think...\\n</think>\\nAnswer here"
        Output: "<think>Okay, let me think...\\n</think>\\nAnswer here"

        Input:  "<think>normal</think> text"  # Remains unchanged
        Output: "<think>normal</think> text"
    """
    has_opening = '<think>' in text
    has_closing = '</think>' in text

    # Only repair if closing tag exists but no opening tag
    if has_closing and not has_opening:
        text = '<think>' + text
        log_message("🔧 Missing <think> tag repaired (opening tag added)")

    return text


def extract_xml_tags(text: str) -> list[tuple[str, str]]:
    """
    Extract ALL XML tags from text (generic, not hardcoded).

    Supports two formats:
    1. XML-style: <tag>content</tag> (DeepSeek/Qwen3)
    2. Harmony-style: <|channel|>name<|message|>content<|end|> (GPT-OSS)

    IMPORTANT:
    1. HTML tags are IGNORED (blacklist in html_tags.py)
    2. Tags inside Markdown code blocks are IGNORED!
       (``` ... ``` blocks often contain HTML/XML code examples)
    3. Orphaned closing </think> tags are automatically repaired!

    Args:
        text: Text with optional XML tags

    Returns:
        List of (tag_name, content) tuples (without HTML tags, without code-block tags!)

    Example:
        >>> extract_xml_tags("<think>foo</think> bar <python>code</python>")
        [("think", "foo"), ("python", "code")]

        >>> extract_xml_tags("<span style='...'>text</span>")  # HTML ignored!
        []

        >>> extract_xml_tags("```html\\n<head>...</head>\\n```")  # Code block ignored!
        []

        >>> extract_xml_tags("Thinking...\\n</think>\\nAnswer")  # Orphaned tag repaired!
        [("think", "Thinking...")]

        >>> extract_xml_tags("<|channel|>analysis<|message|>think<|end|>Answer")  # Harmony!
        [("analysis", "think")]

    Note:
        Repair of orphaned </think> tags happens in calling functions
        (format_thinking_process, build_debug_accordion), BEFORE extract_xml_tags is called.
        This is necessary for clean_response to work correctly.
    """
    # STEP 1: Remove Markdown code blocks BEFORE searching for XML tags
    # Code blocks can contain HTML/XML code examples that should NOT be processed
    text_without_codeblocks = re.sub(r'```[\s\S]*?```', '', text)

    # STEP 2: Generic XML pattern: <tagname>content</tagname>
    pattern = r'<(\w+)>(.*?)</\1>'
    matches = re.findall(pattern, text_without_codeblocks, re.DOTALL)

    # STEP 3: Filter: Only return non-HTML tags (blacklist from html_tags.py)
    xml_tags = [
        (tag_name, content.strip())
        for tag_name, content in matches
        if tag_name.lower() not in HTML_TAG_BLACKLIST
    ]

    # STEP 4: Also extract GPT-OSS Harmony-style tags: <|channel|>NAME<|message|>CONTENT<|end|>
    # Pattern: <|channel|>(analysis|commentary|final)<|message|>(.*?)<|end|>
    harmony_pattern = r'<\|channel\|>(\w+)<\|message\|>(.*?)<\|end\|>'
    harmony_matches = re.findall(harmony_pattern, text_without_codeblocks, re.DOTALL)
    for channel_name, content in harmony_matches:
        xml_tags.append((channel_name, content.strip()))

    return xml_tags


def format_debug_message(message: str) -> str:
    """
    Formatiert Debug-Message mit Timestamp (wie Legacy-Version).

    Args:
        message: Debug message text

    Returns:
        Formatted message mit Timestamp (z.B. "18:32:33 | 📨 User Request empfangen")
    """
    timestamp = get_timestamp()
    return f"{timestamp} | {message}"


def format_thinking_process(ai_response: str, model_name: str | None = None, inference_time: float | None = None, tokens_per_sec: float | None = None, lang: str | None = None) -> str:
    """
    Format XML tags as collapsible accordions (GENERIC).

    Supports ALL tags defined in get_xml_tag_config() dynamically.
    No more hardcoding - new tags can be added via config!

    This is the CENTRAL function for RAW response logging - all other formatters
    should NOT log RAW response to avoid duplicates.

    Args:
        ai_response: The AI response with optional XML tags
        model_name: Name of the model used (e.g., "qwen3:1.7b")
        inference_time: Inference time in seconds
        tokens_per_sec: Tokens per second (optional)
        lang: Language for collapsible labels (de/en). If None, uses get_ui_locale()

    Returns:
        Formatted string with collapsibles for all detected XML tags

    Supported Tags (via get_xml_tag_config):
        <think>: Thinking process (DeepSeek Reasoning)
        <data>: Structured data (Vision-LLM JSON)
        <python>: Python Code
        <code>: Generic Code
        <sql>: SQL Query
        <json>: JSON Data

    Example:
        Input: "<think>reasoning</think> Answer <python>code</python>"
        Output: 2 collapsibles (Thinking Process + Python Code) + "Answer"
    """
    # Use current UI locale if no lang specified
    if lang is None:
        lang = get_ui_locale()

    # Get XML tag config with i18n labels
    xml_tag_config = get_xml_tag_config(lang)

    # DEBUG: Log COMPLETE RAW Response (central logging point)
    log_message("=" * 80)
    log_message("🔍 RAW AI RESPONSE (COMPLETE):")
    log_message(ai_response)
    log_message("=" * 80)

    # STEP 0: Repair orphaned </think> tags BEFORE extraction
    # (Important: Must be applied to ai_response so clean_response works later)
    ai_response = fix_orphan_closing_think_tag(ai_response)

    # Extract ALL XML tags generically
    xml_tags = extract_xml_tags(ai_response)

    # Build collapsibles for each detected tag (if any)
    collapsibles = []
    for tag_name, content in xml_tags:
        config = xml_tag_config.get(tag_name)

        if config:
            # Known tag → Use config (nice icon + label)
            icon = config['icon']
            label = config['label']
            css_class = config['class']
        else:
            # Unknown tag → SKIP (don't format as collapsible!)
            # Smaller models often output tags like <result>, <function>,
            # which are not intended to be collapsibles
            log_message(f"ℹ️ Skipping unknown XML tag: <{tag_name}> (not in xml_tag_config)")
            continue

        # Build summary with icon + label
        summary_parts = [f"{icon} {label}"]
        if model_name:
            summary_parts.append(f"({model_name})")
        # NOTE: Inference time is already shown in final metrics (in parentheses)
        # so we don't add it to the collapsible header anymore
        summary_text = " ".join(summary_parts)

        # Collapse multiple consecutive blank lines (pre-wrap preserves them literally)
        content = re.sub(r'\n{3,}', '\n\n', content.strip())

        # Build collapsible HTML
        collapsible = f"""<details style="font-size: 0.9em; margin-bottom: 1em; margin-top: 0.2em;">
<summary style="cursor: pointer; font-weight: bold; color: #aaa; position: sticky; top: 0; z-index: 1; background: inherit; padding: 2px 0;">{summary_text}</summary>
<div class="{css_class}" style="max-height: 60vh; overflow-y: auto;">
{content}
</div>
</details>"""
        collapsibles.append(collapsible)

    # Remove only KNOWN tags (that became collapsibles) from response
    # Unknown tags stay in the text!
    clean_response = ai_response
    for tag_name, _ in xml_tags:
        # Only remove if tag is in config (i.e., a collapsible was created)
        if tag_name in xml_tag_config:
            # Harmony tags use <|channel|>NAME<|message|>CONTENT<|end|> format
            if tag_name == "analysis":  # GPT-OSS Harmony
                pattern = r'<\|channel\|>analysis<\|message\|>.*?<\|end\|>'
            else:  # XML-style tags like <think>, <python>, etc.
                pattern = rf'<{tag_name}>.*?</{tag_name}>'
            clean_response = re.sub(pattern, '', clean_response, count=1, flags=re.DOTALL)
    clean_response = clean_response.strip()

    # Escape Markdown reference-link definitions: [LABEL]: text
    # rx.markdown (react-markdown) interprets these as invisible reference
    # definitions, swallowing the content entirely.
    # Pattern: line starts with [word]: followed by non-URL text
    # Real reference links like [1]: https://... are preserved.
    # Real inline links [text](url) are not affected (different syntax).
    # To restore reference-link support, remove the next line:
    clean_response = re.sub(
        r'^\[([^\]]+)\]:\s+(?!https?://)',
        r'\\[\1]: ',
        clean_response,
        flags=re.MULTILINE,
    )

    # Extract HTML previews from clean response → collapsibles at top
    html_previews, clean_response = extract_html_previews(clean_response, lang=lang)
    collapsibles.extend(html_previews)

    # Return: Collapsibles + Clean Response
    if collapsibles:
        result = "\n\n".join(collapsibles) + "\n\n" + clean_response
    else:
        result = clean_response

    # STEP: Convert LaTeX delimiters for rx.markdown compatibility
    result = convert_latex_delimiters(result)

    return result


def build_debug_accordion(query_reasoning, ai_text, automatik_model, main_model, query_time=None, final_time=None, lang=None):
    """
    Build debug accordion for agent research with all AI thinking processes.

    Now uses extract_xml_tags() for generic XML processing!

    Args:
        query_reasoning: <think> Content from Query Optimization
        ai_text: Final AI response with optional XML tags
        automatik_model: Name of Automatik model (for Query-Opt)
        main_model: Name of Main model (for final answer)
        query_time: Inference time for Query Optimization (optional)
        final_time: Inference time for final answer (optional)
        lang: Language for collapsible labels (de/en). If None, uses get_ui_locale()

    Returns:
        Formatted AI response with debug accordion prepended
    """
    # Import i18n for labels
    from .i18n import t

    # Use current UI locale if no lang specified
    if lang is None:
        lang = get_ui_locale()

    # NOTE: RAW logging is done by format_thinking_process() which is called
    # before build_debug_accordion() in context_builder.py - no duplicate here

    # STEP 0: Repair orphaned </think> tags BEFORE extraction
    ai_text = fix_orphan_closing_think_tag(ai_text)

    debug_sections = []

    # 1. Query Optimization Reasoning (if present)
    if query_reasoning:
        time_suffix = f" • {query_time:.1f}s" if query_time else ""
        query_opt_label = t("collapsible_query_optimization", lang=lang)
        clean_reasoning = re.sub(r'\n{3,}', '\n\n', query_reasoning.strip())
        debug_sections.append(f"""<details style="font-size: 0.9em; margin-bottom: 0.5em;">
<summary style="cursor: pointer; font-weight: bold; color: #aaa; position: sticky; top: 0; z-index: 1; background: inherit; padding: 2px 0;">{query_opt_label} ({automatik_model}){time_suffix}</summary>
<div class="thinking-compact" style="max-height: 60vh; overflow-y: auto;">
{clean_reasoning}
</div>
</details>""")

    # 2. Final Answer XML tags (generic extraction)
    xml_tags = extract_xml_tags(ai_text)

    for tag_name, content in xml_tags:
        if tag_name == "think":
            # <think> tag → Add as debug collapsible
            time_suffix = f" • {final_time:.1f}s" if final_time else ""
            thinking_label = t("collapsible_thinking_process", lang=lang)
            clean_content = re.sub(r'\n{3,}', '\n\n', content.strip())
            debug_sections.append(f"""<details style="font-size: 0.9em; margin-bottom: 0.5em;">
<summary style="cursor: pointer; font-weight: bold; color: #aaa; position: sticky; top: 0; z-index: 1; background: inherit; padding: 2px 0;">{thinking_label} ({main_model}){time_suffix}</summary>
<div class="thinking-compact" style="max-height: 60vh; overflow-y: auto;">
{clean_content}
</div>
</details>""")

    # Combine all debug sections
    debug_accordion = "\n".join(debug_sections)

    # Remove only KNOWN tags (think) from response
    # In build_debug_accordion only <think> is processed
    clean_response = ai_text
    for tag_name, _ in xml_tags:
        # Only remove <think> tag (the only one used as debug section here)
        if tag_name == "think":
            pattern = rf'<{tag_name}>.*?</{tag_name}>'
            clean_response = re.sub(pattern, '', clean_response, count=1, flags=re.DOTALL)
    clean_response = clean_response.strip()

    # Extract HTML previews from clean response
    html_previews, clean_response = extract_html_previews(clean_response, lang=lang)

    # Collect all top-level collapsibles
    top_parts: list[str] = []
    if debug_accordion:
        top_parts.append(debug_accordion)
    top_parts.extend(html_previews)

    # Return: Top collapsibles + Clean Response
    if top_parts:
        result = "\n\n".join(top_parts) + "\n\n" + clean_response
    else:
        result = clean_response

    # STEP: Convert LaTeX delimiters for rx.markdown compatibility
    result = convert_latex_delimiters(result)

    return result


def build_sandbox_iframe(url: str) -> str:
    """Build a collapsible with embedded iframe for sandbox HTML output."""
    from .config import SANDBOX_IFRAME_HEIGHT
    return (
        f'<details open data-sandbox style="font-size: 0.9em; margin-bottom: 0.5em;">'
        f'<summary style="cursor: pointer; font-weight: bold; color: #aaa; '
        f'position: sticky; top: 0; z-index: 1; background: inherit; padding: 2px 0;">'
        f'📊 Interaktive Visualisierung — '
        f'<a href="{url}" target="_blank" rel="noopener noreferrer" '
        f'style="color: #58a6ff; text-decoration: none;" '
        f'onclick="event.stopPropagation()">Im Browser öffnen</a></summary>'
        f'<iframe src="{url}" '
        f'style="width: 100%; height: {SANDBOX_IFRAME_HEIGHT}; '
        f'border: 1px solid #444; border-radius: 8px; '
        f'background: #000; margin-top: 0.4em;" '
        f'sandbox="allow-scripts allow-same-origin" '
        f'scrolling="no" '
        f'loading="lazy"></iframe>'
        f'</details>'
    )


def build_sandbox_image(url: str) -> str:
    """Build a collapsible with embedded image for sandbox plot output."""
    return (
        f'<details open data-sandbox style="font-size: 0.9em; margin-bottom: 0.5em;">'
        f'<summary style="cursor: pointer; font-weight: bold; color: #aaa;">'
        f'📊 Plot — '
        f'<a href="{url}" target="_blank" rel="noopener noreferrer" '
        f'style="color: #58a6ff; text-decoration: none;" '
        f'onclick="event.stopPropagation()">Vollbild</a></summary>'
        f'<img src="{url}" style="max-width: 100%; max-height: 480px; '
        f'border-radius: 8px; border: 1px solid #444; margin-top: 0.4em;" '
        f'alt="Plot" />'
        f'</details>'
    )


def build_sources_collapsible(used_sources: list, failed_sources: list, lang: str | None = None) -> str:
    """
    Build HTML <details> collapsible for web sources.

    Creates a compact collapsible matching the Denkprozess styling,
    showing all sources sorted by rank_index (successful + failed mixed).

    Args:
        used_sources: List of dicts with 'url', 'word_count', 'rank_index', 'success'
        failed_sources: List of dicts with 'url', 'error', 'rank_index'
        lang: Language for labels (de/en). If None, uses get_ui_locale()

    Returns:
        HTML string with <details> collapsible, or empty string if no sources
    """
    if lang is None:
        lang = get_ui_locale()

    # Combine all sources and sort by rank_index
    all_sources = []

    for src in used_sources:
        all_sources.append({
            "url": src.get("url", ""),
            "word_count": src.get("word_count", 0),
            "rank_index": src.get("rank_index", 999),
            "success": True,
            "error": None
        })

    for src in failed_sources:
        all_sources.append({
            "url": src.get("url", ""),
            "word_count": 0,
            "rank_index": src.get("rank_index", 999),
            "success": False,
            "error": src.get("error", "Failed")
        })

    if not all_sources:
        return ""

    # Sort by rank_index
    all_sources.sort(key=lambda x: x["rank_index"])

    # Build summary text
    total = len(all_sources)
    failed_count = len(failed_sources)

    if lang == "de":
        if failed_count > 0:
            summary_text = f"🔗 {total} Web-Quellen ({failed_count} fehlgeschlagen)"
        else:
            summary_text = f"🔗 {total} Web-Quellen"
        words_label = "Wörter"
        sorted_label = "Sortiert nach Relevanz"
    else:
        if failed_count > 0:
            summary_text = f"🔗 {total} Web Sources ({failed_count} failed)"
        else:
            summary_text = f"🔗 {total} Web Sources"
        words_label = "words"
        sorted_label = "Sorted by relevance"

    # Build source list HTML with table-like layout
    # Number + status icon + URL (truncated) + metadata (word count or error)
    # Numbering matches LLM's "Quelle 1, 2, 3" references
    source_lines = []
    for i, src in enumerate(all_sources, 1):
        url = src["url"]
        if src["success"]:
            # Number + green checkmark + URL (cyan, styled via CSS) + word count
            word_count = src["word_count"]
            source_lines.append(
                f'<div style="display: flex; align-items: baseline; gap: 0.8em;">'
                f'<span style="color: #7d8590; flex-shrink: 0; min-width: 1.5em;">{i}.</span>'
                f'<span style="color: #4ade80; flex-shrink: 0;">✓</span>'
                f'<a href="{url}" target="_blank" rel="noopener" '
                f'style="flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{url}</a>'
                f'<span style="color: #7d8590; flex-shrink: 0; white-space: nowrap;">({word_count} {words_label})</span>'
                f'</div>'
            )
        else:
            # Number + orange X + URL (cyan, styled via CSS) + error
            error = src["error"]
            source_lines.append(
                f'<div style="display: flex; align-items: baseline; gap: 0.8em;">'
                f'<span style="color: #7d8590; flex-shrink: 0; min-width: 1.5em;">{i}.</span>'
                f'<span style="color: #cc6a00; flex-shrink: 0;">✗</span>'
                f'<a href="{url}" target="_blank" rel="noopener" '
                f'style="flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{url}</a>'
                f'<span style="color: #7d8590; font-style: italic; flex-shrink: 0; white-space: nowrap;">({error})</span>'
                f'</div>'
            )

    sources_html = "\n".join(source_lines)

    # Build complete collapsible (matching Denkprozess styling exactly)
    collapsible = f"""<details style="font-size: 0.9em; margin-bottom: 0.5em; margin-top: 0.5em;">
<summary style="cursor: pointer; font-weight: bold; color: #aaa; position: sticky; top: 0; z-index: 1; background: inherit; padding: 2px 0;">{summary_text}</summary>
<div style="max-height: 60vh; overflow-y: auto; padding-left: 1em; padding-top: 0.3em; line-height: 1.6;">

{sources_html}

<div style="font-size: 0.9em; font-style: italic; color: #7d8590; margin-top: 0.3em;">{sorted_label}</div>
</div>
</details>"""

    return collapsible
