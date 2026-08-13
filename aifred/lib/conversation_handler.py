"""
Conversation Handler - Vision Pipeline and Search Query Generation

This module contains:
- Vision pipeline (OCR, structured data extraction, image Q&A)
- Search query generation via Automatik-LLM
- Helper functions (JSON repair, history selection)

The main chat flow is handled by multi_agent.py (unified agent path).
"""

from typing import Any, Dict, List, Optional, AsyncIterator

from .llm_client import LLMClient
from .timer import Timer
from .logging_utils import log_message, log_raw_messages
from .prompt_loader import (
    get_vision_ocr_prompt,
    get_vision_templateless_ocr_prompt,
    get_vision_templateless_default_prompt
)
from .formatting import format_duration_s, format_number
# Cache system removed - will be replaced with Vector DB
from .context_manager import estimate_tokens, strip_thinking_blocks
from .config import (
    DEFAULT_OLLAMA_URL
)
import json
import re


def _select_history_for_context(
    llm_history: List[Dict[str, str]],
    effective_ctx: int
) -> List[Dict[str, str]]:
    """
    Build a history subset that fits within a token budget (2/3 of effective_ctx).

    Iterates from newest to oldest entries, adding until the token limit is reached.

    Args:
        llm_history: Full LLM history (list of role/content dicts)
        effective_ctx: Effective context window size in tokens

    Returns:
        Selected history entries (chronological order), fitting within budget.
    """
    max_history_tokens = (effective_ctx * 2) // 3
    history_tokens = 0
    selected_history: List[Dict[str, str]] = []

    for entry in reversed(llm_history):
        entry_tokens = estimate_tokens([entry])
        if history_tokens + entry_tokens > max_history_tokens:
            break
        selected_history.insert(0, entry)
        history_tokens += entry_tokens

    return selected_history


def _repair_json(s: str) -> str:
    """Fix common LLM JSON errors like ]] instead of ]}."""
    # Fix double brackets: ]] → ]}
    s = re.sub(r'\]\](?=\s*$)', ']}', s)
    s = re.sub(r'\]\](?=\s*})', ']}', s)
    # Fix missing closing brace
    if s.count('{') > s.count('}'):
        s = s + '}'
    return s


def _parse_json_with_recovery(response: str, context: str) -> Any:
    """
    Parse a JSON string with recovery strategies for common LLM errors.

    Tries: direct parse -> repair -> extract from text -> raise ValueError.

    Args:
        response: Raw response string (potentially containing JSON)
        context: Description for error messages (e.g., "research_decision")

    Returns:
        Parsed JSON as dict.

    Raises:
        ValueError: If all parse attempts fail.
    """
    try:
        return json.loads(response)
    except json.JSONDecodeError as e1:
        log_message(f"⚠️ JSON parse error ({context}): {e1}")
        # Try repair
        repaired = _repair_json(response)
        log_message(f"🔧 Attempting JSON repair: {repaired}")
        try:
            result = json.loads(repaired)
            log_message("✅ JSON repair successful")
            return result
        except json.JSONDecodeError:
            # Try extract JSON from text
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                try:
                    extracted = _repair_json(json_match.group())
                    result = json.loads(extracted)
                    log_message("✅ JSON extraction + repair successful")
                    return result
                except json.JSONDecodeError as e3:
                    error_msg = f"JSON parse failed after all repair attempts ({context}): {e3}\nRaw: {response}"
                    log_message(f"❌ {error_msg}")
                    raise ValueError(error_msg)
            else:
                error_msg = f"No JSON found in response ({context}): {response}"
                log_message(f"❌ {error_msg}")
                raise ValueError(error_msg)


async def _process_single_image_vision(
    image: Dict[str, str],
    image_index: int,
    vision_model: str,
    backend_type: str,
    backend_url: Optional[str],
    num_ctx: int,
    supports_chat_template: bool,
    lang: str,
    llm_options: Optional[Dict] = None,
    provider: Optional[str] = None  # Cloud API provider
) -> Dict:
    """
    Process a single image with Vision-LLM.

    This helper function handles the actual Vision-LLM call for one image.
    Used by extract_structured_data_from_images() for sequential multi-image processing.

    Args:
        image: Dict with "name" and "path" keys (path to JPEG file)
        image_index: 0-based index for logging
        vision_model: Vision-LLM model name
        backend_type: Backend type (ollama, vllm, etc.)
        backend_url: Backend URL
        num_ctx: Context window size
        supports_chat_template: Whether model supports system prompts
        lang: Language code ("de" or "en")
        llm_options: Additional LLM options

    Returns:
        Dict with keys:
        - "success": bool
        - "json": Parsed JSON dict (if successful)
        - "raw": Raw response string
        - "metrics": Backend metrics (tokens/s, etc.)
        - "error": Error message (if failed)
        - "time": Processing time in seconds
    """
    from ..backends.base import LLMMessage
    from .vision_utils import load_image_as_base64
    from pathlib import Path

    img_name = image.get("name", f"image_{image_index + 1}")
    log_message(f"📷 [{image_index + 1}] Processing: {img_name}")

    # Build content parts for single image
    content_parts: list[dict[str, Any]] = []

    # Add default prompt for template-less models
    if not supports_chat_template:
        model_lower = vision_model.lower()
        if "ocr" in model_lower or "deepseek-ocr" in model_lower:
            default_prompt = get_vision_templateless_ocr_prompt(lang=lang)
            log_message(f"📝 Vision Prompt: vision_templateless_ocr.txt ({lang})")
        else:
            default_prompt = get_vision_templateless_default_prompt(lang=lang)
            log_message(f"📝 Vision Prompt: vision_ocr.txt ({lang})")
        content_parts.append({"type": "text", "text": default_prompt})

    # Add single image (load from file on-demand)
    image_path = Path(image['path'])
    log_message(f"📂 Loading image: {image_path}")
    base64_data = load_image_as_base64(image_path)
    log_message(f"📦 Base64 size: {len(base64_data)} chars ({len(base64_data) // 1024} KB)")
    content_parts.append({
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{base64_data}"}
    })

    # Build messages
    if supports_chat_template:
        vision_system_prompt = get_vision_ocr_prompt(lang=lang)
        log_message(f"📝 Vision Prompt: vision_ocr.txt [system] ({lang})")

        # Add /no_think to user content if thinking is disabled
        # This is needed because Qwen3-VL ignores the API "think" parameter
        enable_thinking = (llm_options or {}).get('enable_thinking', True)
        if not enable_thinking:
            # Prepend /no_think as text before the image
            content_parts.insert(0, {"type": "text", "text": "/no_think"})

        messages = [
            LLMMessage(role="system", content=vision_system_prompt),
            LLMMessage(role="user", content=content_parts)
        ]
    else:
        messages = [
            LLMMessage(role="user", content=content_parts)
        ]

    # Call Vision-LLM
    llm_client = LLMClient(backend_type=backend_type, base_url=backend_url, provider=provider)

    from .config import VISION_MODEL_TEMPERATURE
    vision_options = {
        "temperature": VISION_MODEL_TEMPERATURE,
        "num_ctx": num_ctx,
        **(llm_options or {})
    }
    # Note: num_predict intentionally NOT set for Vision
    # - Ollama ignores it for thinking models anyway
    # - For OCR, we want complete output without arbitrary limits

    timer = Timer()
    response_text = ""
    metrics = None
    ttft = None  # Time To First Token

    try:
        async for chunk in llm_client.chat_stream(
            model=vision_model,
            messages=messages,  # type: ignore[arg-type]
            options=vision_options
        ):
            if chunk.get("type") == "content":
                if ttft is None:
                    ttft = timer.elapsed()
                response_text += chunk.get("text", "")
            elif chunk.get("type") == "done":
                metrics = chunk.get("metrics", {})

        elapsed = timer.elapsed()

        # Inject TTFT into backend metrics (backend doesn't provide it)
        if metrics is not None and ttft is not None:
            metrics["ttft"] = ttft

        log_message(f"✅ [{image_index + 1}] {img_name} done ({format_duration_s(elapsed)})")

        # Try to parse JSON
        try:
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response_text.strip()

            json_str = _sanitize_json_string(json_str)
            parsed_json = json.loads(json_str)

            return {
                "success": True,
                "json": parsed_json,
                "raw": response_text,
                "metrics": metrics,
                "time": elapsed,
                "image_name": img_name
            }
        except json.JSONDecodeError:
            # JSON parsing failed, return raw response
            return {
                "success": True,  # API call succeeded, just no JSON
                "json": None,
                "raw": response_text,
                "metrics": metrics,
                "time": elapsed,
                "image_name": img_name
            }

    except Exception as e:
        elapsed = timer.elapsed()
        log_message(f"❌ [{image_index + 1}] {img_name} Error: {e}")
        return {
            "success": False,
            "json": None,
            "raw": "",
            "metrics": None,
            "error": str(e),
            "time": elapsed,
            "image_name": img_name
        }


def _html_table_to_markdown(html_content: str) -> str:
    """
    Convert HTML table to Markdown (fallback for models like DeepSeek-OCR).

    Args:
        html_content: HTML string with <table> tags

    Returns:
        Markdown-formatted table
    """
    from html.parser import HTMLParser

    class TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows = []
            self.current_row = []
            self.in_table = False
            self.in_row = False
            self.in_cell = False
            self.cell_content = []

        def handle_starttag(self, tag, attrs):
            if tag == 'table':
                self.in_table = True
            elif tag == 'tr' and self.in_table:
                self.in_row = True
                self.current_row = []
            elif tag in ['td', 'th'] and self.in_row:
                self.in_cell = True
                self.cell_content = []

        def handle_endtag(self, tag):
            if tag == 'table':
                self.in_table = False
            elif tag == 'tr' and self.in_row:
                self.in_row = False
                if self.current_row:
                    self.rows.append(self.current_row)
            elif tag in ['td', 'th'] and self.in_cell:
                self.in_cell = False
                self.current_row.append(''.join(self.cell_content).strip())

        def handle_data(self, data):
            if self.in_cell:
                self.cell_content.append(data)

    parser = TableParser()
    parser.feed(html_content)

    if not parser.rows or len(parser.rows) < 2:
        raise ValueError("No valid HTML table found")

    # First row as header
    headers = parser.rows[0]
    data_rows = parser.rows[1:]

    # Build Markdown table
    markdown = "| " + " | ".join(headers) + " |\n"
    markdown += "| " + " | ".join(["---"] * len(headers)) + " |\n"

    for row in data_rows:
        # Pad row to match header length
        padded_row = row + [""] * (len(headers) - len(row))
        markdown += "| " + " | ".join(padded_row[:len(headers)]) + " |\n"

    return markdown


def _sanitize_json_string(json_str: str) -> str:
    """
    Remove invalid JSON comments (// ...) that some Vision-LLMs add.

    Args:
        json_str: JSON string possibly containing comments

    Returns:
        Sanitized JSON string without comments
    """
    # Remove single-line comments (// ...) but preserve URLs (http://, https://)
    # Use negative lookbehind to avoid removing // in URLs
    sanitized = re.sub(r'(?<!:)//[^\n]*', '', json_str)
    return sanitized


def _json_to_readable(parsed_json: dict, lang: str = "de") -> tuple[str, dict]:
    """
    Convert parsed JSON to human-readable text and return corrected JSON.

    Args:
        parsed_json: Parsed JSON from Vision-LLM (possibly with errors)
        lang: Language ("de" or "en")

    Returns:
        Tuple of (readable_text, corrected_json)
        - readable_text: Human-readable Markdown-formatted text
        - corrected_json: Corrected JSON (with error corrections)
    """
    # Create a copy to avoid modifying the original
    corrected_json = parsed_json.copy()
    doc_type = corrected_json.get("type", "unknown")

    if doc_type == "table":
        # Table → Markdown Table
        columns = corrected_json.get("columns", [])
        rows = corrected_json.get("rows", [])

        # ERROR CORRECTION: Ministral-3 sometimes packs EVERYTHING in "columns" as nested array
        # Format 1: {"columns": [["H1", "H2"]], "rows": [[...]]}  → len == 1
        # Format 2: {"columns": [["H1", "H2"], ["R1C1", "R1C2"], ...], "rows": [...]} → len > 1
        if columns and isinstance(columns[0], list):
            if len(columns) == 1:
                # Only header in nested array, rows separate
                columns = columns[0]
                log_message("⚠️ Vision-LLM format error: columns is nested array (len=1). Corrected.")
            else:
                # Header + data in columns, ignore rows (probably empty or old)
                rows = columns[1:]  # Rest are the actual rows
                columns = columns[0]  # First element is header
                log_message(f"⚠️ Vision-LLM format error: columns contains {len(columns)-1} rows. Corrected.")

            # Update corrected_json with fixed structure
            corrected_json["columns"] = columns
            corrected_json["rows"] = rows

        if not columns:
            return ("⚠️ Leere Tabelle" if lang == "de" else "⚠️ Empty table", corrected_json)

        # If rows is empty but columns is nested
        if not rows and columns:
            return ("⚠️ Tabelle ohne Daten" if lang == "de" else "⚠️ Table without data", corrected_json)

        # Create Markdown table
        table = "| " + " | ".join(str(col) for col in columns) + " |\n"
        table += "| " + " | ".join(["---"] * len(columns)) + " |\n"

        for row in rows:
            # Ensure row has the same length as columns
            row_padded = row + [""] * (len(columns) - len(row))
            table += "| " + " | ".join(str(cell) for cell in row_padded[:len(columns)]) + " |\n"

        return (table, corrected_json)

    elif doc_type == "list":
        # List → Markdown List
        items = corrected_json.get("items", [])
        if not items:
            return ("⚠️ Leere Liste" if lang == "de" else "⚠️ Empty list", corrected_json)

        return ("\n".join(f"- {item}" for item in items), corrected_json)

    elif doc_type == "form":
        # Form → Key-Value list (with subfield support)
        fields = corrected_json.get("fields", [])
        if not fields:
            return ("⚠️ Leeres Formular" if lang == "de" else "⚠️ Empty form", corrected_json)

        result = []
        for field in fields:
            label = field.get('label', '')
            value = field.get('value', '')
            # Support both "subfields" and "fields" for nested data (Vision-LLM uses "fields")
            subfields = field.get('subfields', []) or field.get('fields', [])

            if subfields:
                # Field has subfields → Process recursively
                result.append(f"**{label}**")
                for subfield in subfields:
                    sub_label = subfield.get('label', '')
                    sub_value = subfield.get('value', '')
                    result.append(f"  - {sub_label} {sub_value}")
            else:
                # Normal Key-Value field
                result.append(f"**{label}:** {value}")

        return ("\n".join(result), corrected_json)

    elif doc_type == "text":
        # Plain text - Newlines to Markdown line breaks (2 spaces + \n)
        content = corrected_json.get("content", "")
        # In Markdown \n is ignored - "  \n" creates hard line break without paragraph spacing
        if content:
            content = re.sub(r'\n{2,}', '\n\n', content)  # Multiple newlines → one paragraph
            content = re.sub(r'(?<!\n)\n(?!\n)', '  \n', content)  # Single \n → Markdown line break
        return (content, corrected_json)

    elif doc_type == "mixed" or doc_type == "document":
        # Mixed/complete document → recursively process all sections
        sections = corrected_json.get("sections", [])
        if not sections:
            return ("⚠️ Leeres Dokument" if lang == "de" else "⚠️ Empty document", corrected_json)

        result = []
        for section in sections:
            heading = section.get("heading", "")
            if heading:
                result.append(f"## {heading}\n")
            readable_text, _ = _json_to_readable(section, lang)  # Recursive call
            result.append(readable_text)
            result.append("")  # Empty line between sections

        return ("\n".join(result), corrected_json)

    elif doc_type == "multi_image":
        # Multi-Image analysis → Format each image individually
        images = corrected_json.get("images", [])
        if not images:
            return ("⚠️ Keine Bilder analysiert" if lang == "de" else "⚠️ No images analyzed", corrected_json)

        result = []
        for img in images:
            img_index = img.get("image_index", "?")
            description = img.get("description", "")
            content = img.get("content", {})

            # Header for each image
            header = f"📷 **Bild {img_index}**" if lang == "de" else f"📷 **Image {img_index}**"
            if description:
                header += f": {description}"
            result.append(header)

            # Process content recursively
            if isinstance(content, dict):
                readable_text, _ = _json_to_readable(content, lang)
                if readable_text and readable_text.strip():
                    result.append(readable_text)
            elif isinstance(content, str) and content.strip():
                result.append(content)

            result.append("")  # Empty line between images

        return ("\n".join(result).strip(), corrected_json)

    else:
        # Universal fallback: Try to extract content from known fields
        # This allows Vision-LLMs to use creative/unknown types
        result_parts = []

        # Helper for newline conversion (Markdown: "  \n" = hard line break)
        def _fix_newlines(text: str) -> str:
            text = re.sub(r'\n{2,}', '\n\n', text)  # Multiple newlines → one paragraph
            text = re.sub(r'(?<!\n)\n(?!\n)', '  \n', text)  # Single \n → Markdown line break
            return text

        # Try "content" (most common field)
        content = corrected_json.get("content")
        if content:
            if isinstance(content, dict):
                # Nested content (e.g., {"description": "..."})
                for key, value in content.items():
                    if isinstance(value, str) and value.strip():
                        result_parts.append(_fix_newlines(value))
            elif isinstance(content, str) and content.strip():
                result_parts.append(_fix_newlines(content))

        # Try "description"
        description = corrected_json.get("description")
        if description and isinstance(description, str) and description.strip():
            result_parts.append(_fix_newlines(description))

        # Try "text"
        text = corrected_json.get("text")
        if text and isinstance(text, str) and text.strip():
            result_parts.append(_fix_newlines(text))

        # Try "items" (list)
        items = corrected_json.get("items")
        if items and isinstance(items, list):
            result_parts.append("\n".join(f"- {item}" for item in items if item))

        # Try "sections" recursively
        sections = corrected_json.get("sections")
        if sections and isinstance(sections, list):
            for section in sections:
                if isinstance(section, dict):
                    readable, _ = _json_to_readable(section, lang)
                    if readable and readable.strip():
                        result_parts.append(readable)

        if result_parts:
            return ("\n\n".join(result_parts), corrected_json)

        # Last fallback: Return JSON as string
        return (json.dumps(corrected_json, indent=2, ensure_ascii=False), corrected_json)


async def generate_web_search_queries(
    user_text: str,
    automatik_llm_client,
    automatik_model: str,
    has_images: bool = False,
    vision_json_context: Optional[Dict] = None,
    detected_language: str = "de",
    llm_history: Optional[List[Dict[str, str]]] = None,
    automatik_num_ctx: Optional[int] = None
) -> Dict:
    """
    Generate search queries WITHOUT deciding if web search is needed.

    Used in explicit web search modes (quick/deep) where the user has
    already decided that web search is needed. This function ONLY generates
    3 optimized search queries.

    Args:
        user_text: User query text
        automatik_llm_client: LLM client for Automatik-Model
        automatik_model: Automatik-LLM model name (e.g., "qwen3:4b")
        has_images: Whether the message includes image(s)
        vision_json_context: Structured data extracted from images
        detected_language: Language from Intent Detection ("de" or "en")
        llm_history: Optional chat history for context

    Returns:
        Dict with keys:
        - "queries": List[str] (3 optimized queries)
        - "generation_time": float (LLM call duration in seconds)
        - "raw_response": str (for debugging)
    """
    from .config import AUTOMATIK_LLM_NUM_CTX
    from ..backends.base import LLMMessage
    from .prompt_loader import get_query_generation_prompt

    # Get the query-only prompt
    prompt = get_query_generation_prompt(
        user_text=user_text,
        has_images=has_images,
        vision_json=vision_json_context,
        lang=detected_language
    )

    # DEBUG: Show complete prompt
    log_message("=" * 60)
    log_message("📋 QUERY GENERATION PROMPT:")
    log_message("-" * 60)
    log_message(prompt)
    log_message("-" * 60)
    log_message(f"Prompt length: {len(prompt)} chars, ~{len(prompt.split())} words")
    log_message("=" * 60)

    # Build messages with optional history context
    messages: List[LLMMessage] = []

    effective_ctx = automatik_num_ctx or AUTOMATIK_LLM_NUM_CTX
    if llm_history and len(llm_history) > 0:
        selected_history = _select_history_for_context(llm_history, effective_ctx)

        for entry in selected_history:
            role = entry.get("role", "user")
            content = entry.get("content", "")
            if content.startswith("[AIFRED]:"):
                content = content[9:].strip()
            messages.append(LLMMessage(role=role, content=content))

        if selected_history:
            history_tokens = estimate_tokens(selected_history)
            log_message(f"📜 History context: {len(selected_history)} entries, ~{int(history_tokens)} tokens")

    messages.append(LLMMessage(role="user", content=prompt))

    options: Dict = {
        "temperature": 0.3,  # Slightly higher for query diversity
        "enable_thinking": False,
        "format": "json"
    }
    if automatik_num_ctx is not None:
        options["num_ctx"] = automatik_num_ctx

    generation_timer = Timer()

    log_raw_messages("AUTOMATIK-LLM (generate_web_search_queries)", messages, estimate_tokens)

    try:
        response = await automatik_llm_client.chat(
            model=automatik_model,
            messages=messages,
            options=options
        )
        raw_response = strip_thinking_blocks(response.text).strip()
        generation_time = generation_timer.elapsed()

        log_message("=" * 60)
        log_message("📝 RAW QUERY GENERATION RESPONSE:")
        log_message("-" * 60)
        log_message(raw_response)
        log_message("-" * 60)
        log_message(f"Response length: {len(raw_response)} chars, time: {generation_time:.2f}s")
        log_message("=" * 60)

        # Parse JSON response
        result = _parse_json_with_recovery(raw_response, "query_generation")

        queries = result.get("queries", [])
        volatility = result.get("volatility", "WEEKLY")

        # Validate: must have at least 1 query - NO FALLBACK, raise error
        if not queries:
            error_msg = "LLM returned no queries (parsing error?)"
            log_message(f"❌ {error_msg}")
            raise ValueError(error_msg)

        log_message(f"✅ Query Generation: {len(queries)} queries, volatility={volatility}")

        return {
            "queries": queries,
            "volatility": volatility,
            "generation_time": generation_time,
            "raw_response": raw_response
        }

    except Exception as e:
        generation_time = generation_timer.elapsed()
        log_message(f"❌ Query generation failed ({generation_time:.2f}s): {e}")
        # NO FALLBACK - re-raise to make error visible
        raise


async def extract_structured_data_from_images(
    user_text: str,
    images: List[Dict[str, str]],
    vision_model: str,
    main_model: str,
    backend_type: str = "ollama",
    backend_url: Optional[str] = None,
    llm_options: Optional[Dict] = None,
    state=None,  # AIState object (REQUIRED for per-agent num_ctx lookup)
    detected_language: str = "de",  # Language from Intent Detection or UI setting
    provider: Optional[str] = None  # Cloud API provider ("claude", "qwen", "kimi")
) -> AsyncIterator[Dict]:
    """
    3-Model Architecture: Vision-LLM extracts structured data, Main-LLM optionally formats it.

    Pipeline:
    1. Vision-LLM analyzes image(s) with OCR system prompt → JSON output
    2. If user query contains formatting keywords ("formatiere", "tabelle", "markdown", etc.):
       → Main-LLM post-processes JSON → formatted output
    3. Otherwise: Return JSON directly

    Args:
        user_text: User query text
        images: List of image dicts with "name" and "base64" keys
        vision_model: Vision-LLM model name (e.g., "qwen3-vl:8b")
        main_model: Main LLM for post-processing (e.g., "qwen3:30b")
        backend_type: "ollama", "llamacpp", "vllm"
        backend_url: Backend URL (optional, uses default if None)
        llm_options: Additional LLM options
        state: AIState object (REQUIRED for per-agent num_ctx lookup via get_agent_num_ctx)
        detected_language: Language from LLM-based Intent Detection ("de" or "en")

    Yields:
        Dict with keys: "type" (status/response/debug/error), "content"
    """
    # === DEBUG: Log entry point with image count ===
    log_message(f"🚀 Vision Pipeline started: {len(images)} image(s)")
    for i, img in enumerate(images):
        img_name = img.get("name", "unknown")
        # Use size_kb from pending_images (set during upload), not base64 length
        img_size = img.get("size_kb", 0)
        log_message(f"   [{i+1}] {img_name} ({img_size} KB)")

    # Use detected_language from Intent Detection (passed from state.py)
    lang = detected_language
    log_message(f"🌐 Vision Pipeline using language: {lang.upper()}")

    # Collect image names for display
    image_names = ", ".join([img.get("name", "unknown") for img in images])
    log_message(f"📷 Analyzing images: {image_names}")

    # === PHASE 1: Vision-LLM OCR extraction ===
    # Note: Status message now shown in state.py for immediate feedback

    # === Get model capabilities (chat template + context window) in single API call ===
    from .vision_utils import get_vision_model_capabilities

    # Ensure backend_url is set (fallback based on backend type)
    if not backend_url:
        if backend_type == "cloud_api":
            # Cloud API URL is determined by provider in BackendFactory - don't override!
            log_message("📋 Cloud API: URL will be set by provider config")
        else:
            backend_url = DEFAULT_OLLAMA_URL
            log_message(f"⚠️ No backend_url provided, using default: {DEFAULT_OLLAMA_URL}")

    # === Side-Channel-Routing ===
    # If the user picked a llama-swap/vllm VL model that also exists
    # in Ollama (e.g. "Qwen3VL-4B-Instruct-Q8_0" ↔ "qwen3-vl:4b-instruct-q8_0"),
    # route to Ollama instead so the chat LLM doesn't get model-swapped out
    # for the duration of the vision call. Cloud-API and Ollama backends pass
    # through unchanged. See aifred/lib/vision_routing.py.
    from .vision_routing import maybe_route_to_ollama
    backend_url, backend_type, vision_model, _rerouted = maybe_route_to_ollama(
        backend_url=backend_url,
        backend_type=backend_type,
        vision_model=vision_model,
    )
    if _rerouted:
        log_message(
            f"🔀 Vision routed to Ollama side-channel: {vision_model} "
            f"(avoids llama-swap model-swap)"
        )

    log_message(f"📐 Reading model capabilities for Vision-LLM ({vision_model})...")

    # Cloud API doesn't have /api/show endpoint - use sensible defaults
    intrinsic_num_ctx: Optional[int]
    if backend_type == "cloud_api":
        supports_chat_template = True  # Cloud APIs always support chat format
        intrinsic_num_ctx = 128000     # Most cloud models have 128K+ context
        log_message("📋 Cloud API: assuming chat support, 128K context")
    else:
        assert backend_url is not None, "backend_url required for non-cloud backends"
        supports_chat_template, intrinsic_num_ctx = await get_vision_model_capabilities(backend_url, vision_model)

    # === Calculate VRAM-based context limit (same as Main-LLM) ===
    # This prevents OOM errors when models have large intrinsic context (e.g., 262K for Ministral-3)
    from .gpu_utils import calculate_vram_based_context, get_model_size_from_cache

    # Get model size from Ollama API (for VRAM calculation)
    model_size_bytes = 0
    model_is_loaded = False

    if backend_type == "ollama":
        from ..backends import BackendFactory
        backend = BackendFactory.create("ollama", base_url=backend_url)

        # Get model metadata (size + loaded state)
        _, model_size_bytes = await backend.get_model_context_limit(vision_model)

        # If size not in API, try cache
        if model_size_bytes == 0:
            model_size_bytes = get_model_size_from_cache(vision_model)

        # Check if model is already loaded
        model_is_loaded = await backend.is_model_loaded(vision_model)

    # Calculate practical num_ctx based on VRAM (with MoE auto-detection)
    vram_num_ctx, debug_msgs = await calculate_vram_based_context(
        model_name=vision_model,
        model_size_bytes=model_size_bytes,
        model_context_limit=intrinsic_num_ctx or 4096,  # Fallback to 4K if detection failed
        model_is_loaded=model_is_loaded,
        backend_type=backend_type,
        backend=backend if backend_type == "ollama" else None  # Pass backend for auto-unloading
    )

    # === Dynamic Vision Context Calculation (v2.5.3) ===
    # Same logic as Main-LLM: min(calculated, VRAM-based, Model-Limit)
    # This ensures we use only as much context as needed, saving VRAM!

    # Send debug messages to UI console (same as Main-LLM)
    for msg in debug_msgs:
        yield {"type": "debug", "message": msg}

    # === Vision num_ctx: Use calibrated value from VRAM cache ===
    # The calibrated max_context is the experimentally measured maximum that fits
    # in GPU memory without CPU offloading. This is the ONLY reliable source.
    #
    # Why not calculate dynamically?
    # - Thinking models need the FULL context for <think> blocks (can be 40K+ tokens)
    # - Hardcoded "response reserves" are arbitrary guesses
    # - The calibration was done with THIS model on THIS hardware = accurate
    #
    # vram_num_ctx comes from calculate_vram_based_context() which already
    # checks the calibration cache first (see gpu_utils.py line 543-566)

    # Model limit (fallback to 128K if detection failed)
    model_limit = intrinsic_num_ctx or 131072

    # VLM-Context: Default aus config.py (VLM_NUM_CTX = SSOT). Der
    # „Erweitert → Vision"-Toggle in der UI kann ihn als Override
    # hochfahren — sinnvoll zum Experimentieren mit großen Modellen
    # / hoher Bild-Auflösung. Default-Wert reicht für Türsteher-
    # Beschreibungen.
    from .config import VLM_NUM_CTX
    from .agent_settings import get_agent_setting
    if state and get_agent_setting(state, "vision", "num_ctx_manual_enabled", False):
        num_ctx = int(get_agent_setting(state, "vision", "num_ctx_manual", VLM_NUM_CTX))
        ctx_msg1 = f"🎯 Vision Context: {format_number(num_ctx)} tok (MANUAL OVERRIDE)"
    else:
        num_ctx = VLM_NUM_CTX
        ctx_msg1 = f"🎯 Vision Context: {format_number(num_ctx)} tok (VLM_NUM_CTX)"
    ctx_msg2 = f"   (Model-max: {format_number(model_limit)})"

    # Log the context choice
    yield {"type": "debug", "message": ctx_msg1}
    yield {"type": "debug", "message": ctx_msg2}

    # ============================================================
    # SEQUENTIAL IMAGE PROCESSING (v2.6.0)
    # Process images one at a time for better model accuracy!
    # Even large Vision models produce inconsistent results with
    # multiple images in one request.
    # ============================================================

    num_images = len(images)
    vision_timer = Timer()
    corrected_json = None  # Will be set after processing
    vision_metrics = None  # Will hold metrics from last image
    all_results = []  # Collect results from each image

    if num_images == 1:
        # === SINGLE IMAGE: Direct processing (original behavior) ===
        log_message("📷 Single image mode - direct processing")

        result = await _process_single_image_vision(
            image=images[0],
            image_index=0,
            vision_model=vision_model,
            backend_type=backend_type,
            backend_url=backend_url,
            num_ctx=num_ctx,
            supports_chat_template=supports_chat_template,
            lang=lang,
            llm_options=llm_options,
            provider=provider
        )

        if not result["success"]:
            yield {"type": "error", "content": f"❌ Vision-LLM error: {result.get('error', 'Unknown error')}"}
            return

        vision_metrics = result.get("metrics")
        vision_response = result["raw"]

        # Process result same as before
        if result["json"]:
            parsed_json = result["json"]
            vision_time = result["time"]

            log_message(f"✅ JSON successfully parsed: {parsed_json.get('type', 'unknown')} ({vision_time:.1f}s)")

            human_readable, corrected_json = _json_to_readable(parsed_json, lang)

            yield {"type": "thinking", "content": json.dumps(corrected_json, indent=2, ensure_ascii=False), "label": "📊 Structured Data"}
            yield {"type": "response", "content": human_readable}

            if vision_metrics:
                yield {"type": "done", "metrics": vision_metrics}
        else:
            # No JSON - try fallbacks
            if '<table' in vision_response.lower():
                log_message("🔄 Detected HTML table, attempting conversion to Markdown...")
                try:
                    markdown_table = _html_table_to_markdown(vision_response)
                    yield {"type": "response", "content": markdown_table}
                    if vision_metrics:
                        yield {"type": "done", "metrics": vision_metrics}
                except Exception as html_err:
                    log_message(f"⚠️ HTML conversion failed: {html_err}")
                    yield {"type": "response", "content": vision_response}
            else:
                log_message("⚠️ Returning raw Vision-LLM output")
                yield {"type": "response", "content": vision_response}
                if vision_metrics:
                    yield {"type": "done", "metrics": vision_metrics}

    else:
        # === MULTI-IMAGE: Sequential processing ===
        log_message(f"📷 Sequential mode: Processing {num_images} images one at a time")
        yield {"type": "debug", "message": f"📷 Sequential processing: {num_images} images one at a time"}

        for i, img in enumerate(images):
            img_name = img.get("name", f"image_{i + 1}")
            yield {"type": "debug", "message": f"🔄 [{i + 1}/{num_images}] Processing: {img_name}"}

            result = await _process_single_image_vision(
                image=img,
                image_index=i,
                vision_model=vision_model,
                backend_type=backend_type,
                backend_url=backend_url,
                num_ctx=num_ctx,
                supports_chat_template=supports_chat_template,
                lang=lang,
                llm_options=llm_options,
                provider=provider
            )

            all_results.append(result)

            if result["success"]:
                yield {"type": "debug", "message": f"✅ [{i + 1}/{num_images}] {img_name} done ({format_duration_s(result['time'])})"}
            else:
                yield {"type": "debug", "message": f"⚠️ [{i + 1}/{num_images}] {img_name} error: {result.get('error', 'Unknown')}"}

        # Capture metrics from last successful result
        for r in reversed(all_results):
            if r.get("metrics"):
                vision_metrics = r["metrics"]
                break

        total_time = vision_timer.elapsed()
        log_message(f"✅ All {num_images} images processed ({total_time:.1f}s total)")

        # === Combine results into multi_image JSON format ===
        combined_images = []
        combined_readable_parts = []

        for i, result in enumerate(all_results):
            img_name = result.get("image_name", f"image_{i + 1}")

            if result["json"]:
                # Add to combined JSON
                image_entry = {
                    "image_name": img_name,
                    **result["json"]  # Merge parsed JSON fields
                }
                combined_images.append(image_entry)

                # Generate human-readable for this image
                human_part, _ = _json_to_readable(result["json"], lang)
                combined_readable_parts.append(f"### 📷 {img_name}\n\n{human_part}")

            elif result["raw"]:
                # No JSON but has raw output
                combined_images.append({
                    "image_name": img_name,
                    "type": "raw_text",
                    "content": result["raw"][:500]  # Truncate for JSON
                })
                combined_readable_parts.append(f"### 📷 {img_name}\n\n{result['raw']}")

            else:
                # Failed
                error_msg = result.get("error", "Processing failed")
                combined_images.append({
                    "image_name": img_name,
                    "type": "error",
                    "error": error_msg
                })
                combined_readable_parts.append(f"### 📷 {img_name}\n\n❌ Error: {error_msg}")

        # Build final combined JSON
        corrected_json = {
            "type": "multi_image",
            "count": num_images,
            "processing_mode": "sequential",
            "total_time_seconds": round(total_time, 1),
            "images": combined_images
        }

        # Combine human-readable output
        human_readable = "\n\n---\n\n".join(combined_readable_parts)

        # Yield results
        yield {"type": "thinking", "content": json.dumps(corrected_json, indent=2, ensure_ascii=False), "label": f"📊 Structured Data ({num_images} images)"}
        yield {"type": "response", "content": human_readable}

        if vision_metrics:
            yield {"type": "done", "metrics": vision_metrics}

    # === PHASE 2: Signal completion - state.py will route to Automatik if needed ===
    # Send vision_complete signal with user_text flag
    # state.py will decide whether to route to chat_interactive_mode based on user_text
    yield {
        "type": "vision_complete",
        "vision_json": corrected_json,
        "metrics": vision_metrics,
        "has_user_text": bool(user_text and user_text.strip())  # Tell state.py if routing needed
    }

    if user_text and user_text.strip():
        log_message("📋 Vision done - continuing to Automatik/Research")
    else:
        log_message("✅ Vision extraction complete (no follow-up question)")
