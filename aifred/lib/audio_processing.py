"""
Audio Processing Module - TTS and STT functionality

This module handles Text-to-Speech (Edge TTS, Piper TTS) and Speech-to-Text
(Whisper) operations, including text cleanup for TTS.
"""

import os
import re
import subprocess
import atexit
import time
import httpx
from pathlib import Path
from .config import DATA_DIR, TTS_MEDIA_TYPE_EXTENSIONS
from .i18n import t
from .logging_utils import log_message
from .tts_text_filter import (
    HTML_COMMENT,
    NUMBER_PARENTHESES,
    PERFORMANCE_FOOTER,
    ROUND_LABEL,
    TIMING_PARENTHESES,
    collapsible_tags as _collapsible_tags,
    strip_collapsible_blocks as _strip_collapsible_blocks,
)

# TTS Audio output directory (temporary chunks, 24h cleanup)
# Located in data/ directory which is excluded from hot-reload
# Served via /_upload/ endpoint
TTS_AUDIO_DIR = DATA_DIR / "tts_audio"
TTS_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Session audio directory (permanent, deleted with session)
# Structure: data/audio/{session_id}/
SESSION_AUDIO_DIR = DATA_DIR / "audio"
SESSION_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Current agent and engine for filename prefixing (set by state before TTS calls)

# Streaming content hint tracking - ensures hints are only spoken once per block
# Reset when regular text is detected, so next occurrence gets announced again
_table_hint_announced: bool = False
_formula_hint_announced: bool = False
_code_hint_announced: bool = False
# Collapsible tag whose content we're skipping mid-stream (None = outside one).
# Generalised from the old <details>-only flag: the raw stream carries the
# SOURCE tags (<think>, <vlm_output>, <data>, …), not the rendered <details>.
_skip_block_tag: "str | None" = None
_list_streaming_count: int = 0       # consecutive list items seen (streaming mode)
_list_hint_announced: bool = False   # the "and more items" hint is emitted once per overflow


# ---------------------------------------------------------------------------
# Shared audio utilities (deduplicated from multiple TTS functions)
# ---------------------------------------------------------------------------


def reset_content_hint_flags() -> None:
    """Reset all content hint flags (for new streaming session or after regular text)."""
    global _table_hint_announced, _formula_hint_announced, _code_hint_announced, _skip_block_tag
    global _list_streaming_count, _list_hint_announced
    _table_hint_announced = False
    _formula_hint_announced = False
    _code_hint_announced = False
    _skip_block_tag = None
    _list_streaming_count = 0
    _list_hint_announced = False


def _get_tts_list_max_items() -> int:
    """Read tts_list.full_max_items from audio_player settings.json.

    Threshold for the TTS list filter: lists with > max_items entries are
    replaced with a spoken hint instead of being read aloud. Browser
    display is unaffected (filter only runs in TTS path).
    """
    from .audio_player_settings import load_audio_player_settings
    try:
        return int(
            load_audio_player_settings().get("tts_list", {}).get("full_max_items", 5)
        )
    except (TypeError, ValueError):
        return 5


_LIST_ITEM_RE = re.compile(r'^[ \t]*(?:[-*+]|\d+[.)])\s+')


def _generate_tts_filename(extension: str = "wav", agent: str = "aifred", engine: str = "tts") -> str:
    """
    Generate TTS audio filename with agent, engine, and human-readable timestamp.

    Format: audio_{agent}_{engine}_{YYYYMMDD-HHmmss-ms}.{ext}
    Example: audio_aifred_xtts_20260220-114047-553.ogg

    Args:
        extension: File extension (wav, mp3, ogg)
        agent: Agent name (the joined audio of a reply carries the defaults)
        engine: Engine key

    Returns:
        Filename string
    """
    from datetime import datetime

    now = datetime.now()
    timestamp = now.strftime("%Y%m%d-%H%M%S") + f"-{now.microsecond // 1000:03d}"
    return f"audio_{agent}_{engine}_{timestamp}.{extension}"


def store_tts_audio(audio: bytes, media_type: str, agent: str, engine: str) -> str:
    """Put audio delivered by the TTS service into the TTS audio folder (24 h cleanup like every
    TTS file); returns its URL (``/_upload/tts_audio/<name>``).

    ``engine`` is the service's entry name (``engine`` or ``engine@host``).

    Raises:
        ValueError: ``media_type`` is not one the service is known to deliver.
    """
    extension = TTS_MEDIA_TYPE_EXTENSIONS.get(media_type)
    if extension is None:
        raise ValueError(f"unknown media type of TTS audio: {media_type!r}")
    engine_key = engine.partition("@")[0]
    filename = _generate_tts_filename(extension, agent, engine_key)
    # Two segments within the same millisecond would share a name: wait for the next one.
    while (TTS_AUDIO_DIR / filename).exists():
        time.sleep(0.001)
        filename = _generate_tts_filename(extension, agent, engine_key)
    (TTS_AUDIO_DIR / filename).write_bytes(audio)
    return f"/_upload/tts_audio/{filename}"


def _resolve_under(base: Path, relative: str) -> Path | None:
    """Resolve ``relative`` under ``base`` and reject path traversal.

    Returns the resolved path only if it stays inside ``base`` — otherwise
    logs and returns None. Guards URL→filesystem conversion (and the os.remove
    in concatenate_wav_files) against ``../`` escapes.
    """
    root = base.resolve()
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        log_message(f"⚠️ Rejected audio path traversal: {relative!r}")
        return None
    return candidate


def _resolve_tts_urls_to_paths(wav_urls: list[str]) -> list[str]:
    """Convert TTS audio URLs to local file paths, filtering non-existent files."""
    file_paths: list[str] = []
    for url in wav_urls:
        if "/_upload/tts_audio/" in url:
            filename = url.split("/_upload/tts_audio/")[-1]
            file_path = _resolve_under(TTS_AUDIO_DIR, filename)
            if file_path is None:
                continue
            if file_path.exists():
                file_paths.append(str(file_path))
            else:
                log_message(f"⚠️ Audio file not found: {file_path}")
    return file_paths


def _ffmpeg_concat(file_paths: list[str], output_path: str) -> bool:
    """Concatenate audio files using ffmpeg. Returns True on success."""
    input_args = []
    for fp in file_paths:
        input_args.extend(["-i", fp])

    filter_inputs = "".join(f"[{i}:a]" for i in range(len(file_paths)))
    filter_str = f"{filter_inputs}concat=n={len(file_paths)}:v=0:a=1"

    cmd = ["ffmpeg", "-y", *input_args, "-filter_complex", filter_str, output_path]
    result = subprocess.run(cmd, capture_output=True, timeout=None)
    return result.returncode == 0 and os.path.exists(output_path)


def concatenate_wav_files(wav_urls: list[str], delete_originals: bool = True) -> str | None:
    """
    Concatenate multiple WAV files into a single WAV file using ffmpeg.

    Uses ffmpeg because XTTS generates IEEE Float WAV files which Python's
    wave module cannot read (only supports PCM format).

    Args:
        wav_urls: List of WAV URLs (format: /_upload/tts_audio/filename.wav)
        delete_originals: If True, delete the original chunk files after concatenation

    Returns:
        URL of the combined WAV file, or None on error. Fail-loud: any
        error (missing chunk files, ffmpeg failure, timeout) returns None
        instead of a truncated partial result — the caller must not report
        success for an incomplete concatenation.
    """
    if not wav_urls:
        return None
    if len(wav_urls) == 1:
        return wav_urls[0]

    file_paths = _resolve_tts_urls_to_paths(wav_urls)
    if len(file_paths) < len(wav_urls):
        log_message(
            f"❌ WAV concat: only {len(file_paths)}/{len(wav_urls)} chunk files found — aborting"
        )
        return None

    output_filename = _generate_tts_filename("wav").replace(".wav", "_combined.wav")
    output_path = str(TTS_AUDIO_DIR / output_filename)

    try:
        if _ffmpeg_concat(file_paths, output_path):
            log_message(f"✅ WAV concat: {len(file_paths)} files → {output_filename}")
            if delete_originals:
                for fp in file_paths:
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
            return f"/_upload/tts_audio/{output_filename}"
        else:
            log_message("❌ WAV concat ffmpeg error")
            return None
    except (subprocess.TimeoutExpired, OSError) as e:
        log_message(f"❌ WAV concat error: {e}")
        return None


def save_audio_to_session(wav_urls: list[str], session_id: str) -> str | None:
    """
    Save TTS audio to session directory for permanent storage.

    For single audio files: copies to session directory
    For multiple chunks: concatenates and saves to session directory

    Audio in session directory is NOT subject to 24h cleanup.
    It's deleted when the session is deleted.

    Args:
        wav_urls: List of WAV URLs (format: /_upload/tts_audio/filename.wav)
        session_id: Session ID for directory structure

    Returns:
        URL of the session audio file (/_upload/audio/{session_id}/filename.wav)
        or None on error
    """
    import shutil

    if not wav_urls or not session_id:
        return None

    # Ensure session audio directory exists
    session_audio_dir = SESSION_AUDIO_DIR / session_id
    session_audio_dir.mkdir(parents=True, exist_ok=True)

    # Detect format from input files (preserve OGG if all inputs are OGG)
    first_url = wav_urls[0] if wav_urls else ""
    ext = "ogg" if first_url.endswith(".ogg") else "wav"
    output_filename = _generate_tts_filename(ext)

    if len(wav_urls) == 1:
        # Single file: copy to session directory
        url = wav_urls[0]
        if "/_upload/tts_audio/" in url:
            filename = url.split("/_upload/tts_audio/")[-1]
            source_path = _resolve_under(TTS_AUDIO_DIR, filename)
            if source_path is not None and source_path.exists():
                dest_path = session_audio_dir / output_filename
                shutil.copy2(str(source_path), str(dest_path))
                log_message(f"📁 Audio copied to session: {output_filename}")
                return f"/_upload/audio/{session_id}/{output_filename}"
            else:
                log_message(f"⚠️ Audio file not found: {source_path}")
                return None
        return None

    # Multiple files: concatenate to session directory
    file_paths = _resolve_tts_urls_to_paths(wav_urls)

    if len(file_paths) < 2:
        if file_paths:
            dest_path = session_audio_dir / output_filename
            shutil.copy2(file_paths[0], str(dest_path))
            log_message(f"📁 Audio copied to session: {output_filename}")
            return f"/_upload/audio/{session_id}/{output_filename}"
        return wav_urls[0] if wav_urls else None

    output_path = str(session_audio_dir / output_filename)

    try:
        if _ffmpeg_concat(file_paths, output_path):
            log_message(f"✅ Session audio: {len(file_paths)} chunks → {output_filename}")
            return f"/_upload/audio/{session_id}/{output_filename}"
        else:
            log_message("❌ Session audio ffmpeg error")
            return wav_urls[0] if wav_urls else None
    except (subprocess.TimeoutExpired, OSError) as e:
        log_message(f"❌ Session audio error: {e}")
        return wav_urls[0] if wav_urls else None


def cleanup_session_audio(session_id: str) -> int:
    """
    Delete all audio files for a session.

    Called when session is deleted.
    Removes the audio directory under data/audio/{session_id}/.

    Args:
        session_id: Session identifier

    Returns:
        Number of files deleted
    """
    import shutil

    audio_dir = SESSION_AUDIO_DIR / session_id
    if not audio_dir.exists():
        return 0

    # Count files before deletion
    files = list(audio_dir.glob("*"))
    count = len(files)

    try:
        shutil.rmtree(audio_dir)
        log_message(f"🗑️ Deleted {count} audio file(s) for session {session_id[:8]}...")
    except OSError as e:
        log_message(f"⚠️ Could not delete session audio: {e}")
        return 0

    return count


def load_audio_url_as_base64(audio_url: str) -> str | None:
    """
    Load audio from URL and return as Base64 data URI.

    Converts internal URLs (/_upload/audio/...) to filesystem paths
    and returns the audio as a data: URI for HTML embedding.

    Args:
        audio_url: Internal audio URL (e.g., /_upload/audio/{session_id}/file.wav)

    Returns:
        Data URI string (data:audio/wav;base64,...) or None if failed
    """
    import base64
    import re

    # Extract path part after /_upload/audio/
    match = re.search(r'/_upload/audio/(.+)$', audio_url)
    if not match:
        log_message(f"⚠️ Invalid audio URL format: {audio_url}")
        return None

    relative_path = match.group(1)
    file_path = _resolve_under(SESSION_AUDIO_DIR, relative_path)

    if file_path is None or not file_path.exists():
        log_message(f"⚠️ Audio file not found: {SESSION_AUDIO_DIR / relative_path}")
        return None

    try:
        # Determine MIME type from extension
        suffix = file_path.suffix.lower()
        mime_types = {
            '.wav': 'audio/wav',
            '.mp3': 'audio/mpeg',
            '.ogg': 'audio/ogg',
            '.m4a': 'audio/mp4',
            '.flac': 'audio/flac',
        }
        mime_type = mime_types.get(suffix, 'audio/wav')

        with open(file_path, 'rb') as f:
            audio_bytes = f.read()

        base64_data = base64.b64encode(audio_bytes).decode('utf-8')
        return f"data:{mime_type};base64,{base64_data}"
    except OSError as e:
        log_message(f"⚠️ Failed to load audio: {e}")
        return None


# ============================================================
# STREAMING TTS - Sentence Detection
# ============================================================

# Common abbreviations that should NOT be treated as sentence endings
# Pattern: word ending with period that is NOT a sentence end
ABBREVIATIONS_DE = {
    "z.b.", "z. b.", "d.h.", "d. h.", "u.a.", "u. a.", "o.ä.", "o. ä.",
    "bzw.", "ca.", "etc.", "evtl.", "ggf.", "inkl.", "max.", "min.",
    "nr.", "s.", "str.", "tel.", "usw.", "vgl.", "vs.", "z.t.",
    "dr.", "prof.", "hr.", "fr.", "ing.", "dipl.",  # Titles
    "jan.", "feb.", "mär.", "apr.", "jun.", "jul.", "aug.", "sep.", "okt.", "nov.", "dez.",  # Months
    "mo.", "di.", "mi.", "do.", "sa.", "so.",  # Days ("fr." already in titles)
}

ABBREVIATIONS_EN = {
    "e.g.", "i.e.", "etc.", "vs.", "mr.", "mrs.", "ms.", "dr.", "prof.",
    "inc.", "ltd.", "corp.", "co.", "jr.", "sr.",
    "jan.", "feb.", "mar.", "apr.", "jun.", "jul.", "aug.", "sep.", "oct.", "nov.", "dec.",
    "mon.", "tue.", "wed.", "thu.", "fri.", "sat.", "sun.",
    "no.", "vol.", "ch.", "pg.", "pp.", "fig.", "approx.", "dept.",
}

# Combined set (lowercase)
ABBREVIATIONS = ABBREVIATIONS_DE | ABBREVIATIONS_EN


# Sätze mit weniger Wörtern werden mit dem nächsten zusammengelegt (die TTS-Engine
# halluziniert bei sehr kurzen Sätzen). Gilt für das Streaming im Browser.


def extract_complete_paragraphs(buffer: str) -> tuple[list[str], str]:
    """Wie ``extract_complete_sentences``, aber an Absätzen (Leerzeilen): alles vor dem
    letzten Absatzende ist vollständig, der angefangene letzte Absatz bleibt im Puffer."""
    pieces = re.split(r"\n[ \t]*\n", buffer.replace("\r\n", "\n").replace("\r", "\n"))
    return [piece.strip() for piece in pieces[:-1] if piece.strip()], pieces[-1]


def extract_complete_sentences(buffer: str) -> tuple[list[str], str]:
    """
    Extract complete sentences from a text buffer.

    This function is designed for streaming TTS: it accumulates text tokens
    and returns sentences as soon as they are complete, keeping incomplete
    text in the buffer for the next call.

    Handles:
    - Standard sentence endings: . ! ?
    - German/English abbreviations (z.B., e.g., Dr., etc.)
    - Quotations and parentheses
    - Numbers with decimals (3.14, 1.5)
    - URLs (http://example.com)
    - Code blocks (```...```) - skipped entirely

    Args:
        buffer: Text accumulated so far

    Returns:
        Tuple of (list of complete sentences, remaining buffer)

    Example:
        >>> extract_complete_sentences("Hello world. This is a test")
        (['Hello world.'], ' This is a test')
        >>> extract_complete_sentences("Dr. Smith said hello. How are you?")
        (['Dr. Smith said hello.', 'How are you?'], '')
    """
    if not buffer or not buffer.strip():
        return [], buffer

    sentences = []
    remaining = buffer

    # Skip if we're in a code block (``` ... ```)
    if "```" in remaining:
        # Count occurrences - odd number means we're inside a code block
        if remaining.count("```") % 2 == 1:
            # Inside code block - don't extract sentences
            return [], buffer

    # ============================================================
    # IMPORTANT: Newline detection must happen BEFORE clean_text_for_tts()!
    #
    # clean_text_for_tts() uses .strip() which removes trailing newlines.
    # In streaming mode, the buffer might be "Heading text\n\n" (waiting for
    # next paragraph). If we clean first, the \n\n gets stripped and the
    # next chunk gets concatenated directly: "Heading textNext paragraph"
    #
    # Solution: First normalize newlines and detect paragraph breaks,
    # then clean each extracted sentence individually.
    # ============================================================

    # Normalize all newline formats to \n (LF) FIRST
    # Different systems use different line endings:
    # - Unix/Linux/macOS: \n (LF, ASCII 10)
    # - Windows: \r\n (CRLF, ASCII 13 + 10)
    # - Old Mac (pre-OS X): \r (CR, ASCII 13)
    # LLMs typically output \n, but API responses might vary
    remaining = remaining.replace('\r\n', '\n').replace('\r', '\n')

    # Regex pattern for sentence boundaries
    # Matches: . ! ? followed by:
    #   - Whitespace and uppercase letter (new sentence)
    #   - End of string (final sentence)
    #   - Quotation marks/parentheses then whitespace
    # Negative lookbehind for common abbreviations handled separately

    # Simple approach: find potential sentence ends and validate
    i = 0
    sentence_start = 0

    while i < len(remaining):
        char = remaining[i]

        # Check for paragraph break (double newline) - treat as sentence boundary
        # This handles headings without punctuation: "Ein Marmeladenbrot\n\nEin Text..."
        # The heading should be spoken separately, not concatenated with the next paragraph
        if char == '\n':
            # Check if this is a double newline (paragraph break)
            if i + 1 < len(remaining) and remaining[i + 1] == '\n':
                sentence = remaining[sentence_start:i].strip()
                if sentence and len(sentence) > 1:
                    # Add period if sentence doesn't end with punctuation
                    # This ensures XTTS gets proper sentence boundaries
                    # NOTE: Don't clean here - cleaning happens in _tts_generate_sentence_async()
                    if sentence[-1] not in '.!?:;':
                        sentence += '.'
                    sentences.append(sentence)
                # Skip past all the newlines (even if sentence was filtered/empty)
                next_content_start = i + 1
                while next_content_start < len(remaining) and remaining[next_content_start] in ' \t\n':
                    next_content_start += 1
                sentence_start = next_content_start
                i = next_content_start - 1  # -1 because loop will increment
            else:
                # Single newline - treat as sentence boundary if current line has no punctuation
                # This handles headings like "Ein Marmeladenbrot\nEin Text..."
                # where there's no blank line between heading and content
                sentence = remaining[sentence_start:i].strip()
                if sentence and len(sentence) > 1:
                    # If line doesn't end with punctuation, it's likely a heading
                    # Treat newline as sentence boundary and add period
                    # NOTE: Don't clean here - cleaning happens in _tts_generate_sentence_async()
                    if sentence[-1] not in '.!?:;':
                        sentence += '.'
                        sentences.append(sentence)
                        # Always update sentence_start to skip past this content
                        sentence_start = i + 1
                    # If it DOES end with punctuation, it was already extracted
                    # by the normal punctuation handling below

        # Check for colon followed by double newline (intro sentence before list/table)
        # "Hier nun die gewünschte Tabelle:\n\n| ..." → extract as sentence
        if char == ':':
            after_colon = remaining[i+1:i+3] if i+1 < len(remaining) else ""
            # Colon followed by newline(s) = sentence end (intro before list/table)
            if after_colon.startswith('\n\n') or after_colon.startswith('\n'):
                sentence = remaining[sentence_start:i+1].strip()
                # NOTE: Don't clean here - cleaning happens in _tts_generate_sentence_async()
                if sentence and len(sentence) > 10:  # Minimum length to avoid false positives
                    sentences.append(sentence)
                sentence_start = i + 1
                # Skip the newlines
                while i + 1 < len(remaining) and remaining[i + 1] in '\n\t ':
                    i += 1

        # Check for sentence-ending punctuation
        if char in '.!?':
            # ============================================================
            # STREAMING TABLE DETECTION: Don't extract inside table rows
            #
            # In streaming mode, table rows arrive piece by piece:
            #   "| Marmelade (z." → period here is NOT a sentence end!
            #
            # Check if we're currently inside a table row by looking at
            # the current line (from last newline to current position).
            # ============================================================
            current_line_start = remaining.rfind('\n', 0, i) + 1
            current_line = remaining[current_line_start:i+1]

            # If current line starts with | (table row), skip punctuation detection
            # Wait until the row is complete (newline handler will process it)
            if current_line.lstrip().startswith('|'):
                i += 1
                continue

            # Get context around this character
            before = remaining[max(0, i-10):i+1].lower()
            after = remaining[i+1:i+3] if i+1 < len(remaining) else ""

            # Check if this is a real sentence end
            is_sentence_end = False

            if char in '!?':
                # Exclamation and question marks are almost always sentence ends
                is_sentence_end = True
            elif char == '.':
                # Period needs more careful checking
                is_abbreviation = False

                # Check against known abbreviations. The abbreviation must start a word:
                # "Haus." ends in "s.", "noch." in "ch." without being abbreviations.
                for abbr in ABBREVIATIONS:
                    if before.endswith(abbr) and (len(before) == len(abbr) or not before[-len(abbr) - 1].isalnum()):
                        is_abbreviation = True
                        break

                # Check for decimal numbers (1.5, 3.14)
                if not is_abbreviation and i > 0 and i < len(remaining) - 1:
                    char_before = remaining[i-1]
                    char_after = remaining[i+1] if i+1 < len(remaining) else ""
                    if char_before.isdigit() and char_after.isdigit():
                        is_abbreviation = True  # It's a decimal number

                # Check for URLs
                if not is_abbreviation:
                    url_context = remaining[max(0, i-20):i+10].lower()
                    if "http" in url_context or "www." in url_context or ".com" in url_context:
                        is_abbreviation = True

                # Check for ellipsis (...)
                if not is_abbreviation and i >= 2:
                    if remaining[i-2:i+1] == "...":
                        # Ellipsis at end of sentence IS a sentence end
                        # But only if followed by space+uppercase or end
                        if after and after[0].isupper():
                            is_sentence_end = True
                        elif not after.strip():
                            is_sentence_end = True
                        is_abbreviation = True  # Don't double-check

                if not is_abbreviation:
                    # Real sentence end if followed by:
                    # - Whitespace (or end of string)
                    # - Whitespace + uppercase letter
                    # - Closing quote/paren then whitespace
                    if not after:
                        # End of buffer - might be complete sentence
                        is_sentence_end = True
                    elif after[0] in ' \n\t':
                        # Followed by whitespace
                        if len(after) > 1 and after[1].isupper():
                            is_sentence_end = True
                        elif len(after) == 1:
                            # Just whitespace at end - likely sentence end
                            is_sentence_end = True
                    elif after[0] in '"\')»"':
                        # Closing quote/paren - check what's after that
                        is_sentence_end = True

            if is_sentence_end:
                # Extract the sentence
                sentence = remaining[sentence_start:i+1].strip()

                # Handle closing quotes/parens that belong to the sentence
                j = i + 1
                while j < len(remaining) and remaining[j] in '"\')»"':
                    j += 1

                if j > i + 1:
                    sentence = remaining[sentence_start:j].strip()
                    i = j - 1

                # NOTE: Don't clean here - cleaning happens in _tts_generate_sentence_async()
                if sentence and len(sentence) > 1:
                    sentences.append(sentence)

                sentence_start = i + 1

        i += 1

    # Whatever remains goes back to the buffer
    # IMPORTANT: Only strip LEADING whitespace, not trailing!
    # Trailing newlines are needed to detect paragraph breaks in the next call.
    # If we have "Heading\n\n" and strip(), the \n\n gets removed, and
    # the next chunk "Text" gets concatenated directly: "HeadingText"
    remaining = remaining[sentence_start:].lstrip()

    return sentences, remaining


def clean_text_for_tts(text, language):
    """
    Prepare text for TTS output: Remove elements that sound bad when read aloud.

    This function handles CONTENT FILTERING only. TTS-specific normalization
    (punctuation, special characters) is handled by the XTTS server's
    normalize_text_for_tts() function.

    Removes:
    - <think> tags (raw LLM thinking)
    - <details>/<summary> blocks (collapsible UI elements)
    - HTML/XML tags (all generic tags like <br>, <span>, etc.)
    - Code blocks (``` ... ```) and inline code (`...`)
    - Markdown tables (| ... |) → replaced with spoken hint
    - LaTeX formulas ($...$ and $$...$$)
    - Emojis (keeps laughter emojis for XTTS to convert to "hahaha")
    - Markdown formatting (**, *, #, etc.)
    - URLs, timing metadata
    - Markdown links [text](url) → keeps text, removes URL
    - Blockquotes (> text)

    NOTE: Punctuation is NOT added here - XTTS server handles that to prevent
    issues with partial text in streaming mode.

    Args:
        text: Raw text from AI response
        language: Language of the text (de, en); selects the spoken hints that replace
            code, tables, formulas and long lists, taken from the i18n files

    Returns:
        str: Cleaned text suitable for TTS
    """
    global _skip_block_tag

    def hint(key: str) -> str:
        return t(key, lang=language)

    # Detect multi-line content (Re-Synth/regeneration mode) vs single-line (streaming mode)
    # Multi-line content should skip streaming state logic and use regex-based removal
    is_multiline = '\n' in text or len(text) > 500

    # Handle collapsible blocks in STREAMING mode (tags arrive chunk by chunk).
    # The raw stream carries the SOURCE tags (<think>, <vlm_output>, <data>, …),
    # not the rendered <details>. Track which one we're inside across chunks;
    # a block that opens AND closes within one chunk falls through to the regex
    # strip below. In multi-line (regeneration) mode the regex handles it all.
    if not is_multiline:
        low = text.lower()
        if _skip_block_tag is not None:
            if f'</{_skip_block_tag}>' in low:
                _skip_block_tag = None
            return ""  # still inside a collapsible block opened in an earlier chunk
        for _tag in _collapsible_tags():
            if f'<{_tag}' in low and f'</{_tag}>' not in low:
                _skip_block_tag = _tag  # opens here, continues into next chunk(s)
                return ""
        # complete blocks (open+close in this chunk) fall through to the regex below
    else:
        # Reset ALL streaming state when processing full content (regeneration)
        # This prevents stale state from previous streaming sessions affecting regeneration
        reset_content_hint_flags()

    # Remove HTML comments (<!--USED_SOURCES:...-->, <!--FAILED_SOURCES:...-->, etc.)
    # These contain JSON metadata that should never be read aloud
    clean_text = HTML_COMMENT.sub('', text).strip()

    # Fix "AIfred" pronunciation: capital I causes TTS to say "A-I-fred" instead of "Alfred"
    clean_text = clean_text.replace('AIfred', 'Alfred')

    # Remove ALL collapsible blocks WITH their content (think, vlm_output, data,
    # …, plus the rendered <details>) via the shared SSoT helper — their content
    # is hidden behind a collapsible in the UI, so it must never be spoken.
    clean_text = _strip_collapsible_blocks(clean_text).strip()

    # Remove ALL HTML/XML tags but keep content between them
    # Catches <br>, <span>, <div>, <p>, <b>, <i>, <u>, <strong>, <em>, etc.
    # Also catches self-closing tags like <br/>, <hr/>
    clean_text = re.sub(r'<[^>]+/?>', '', clean_text)

    # Replace code blocks (``` ... ```) with spoken hint - code sounds terrible when read aloud
    # Use .*? (non-greedy) to match content including backticks inside code
    # Use GLOBAL flag to persist across multiple calls (streaming chunks)
    def replace_code_block(m):
        global _code_hint_announced
        if not _code_hint_announced:
            _code_hint_announced = True
            return f'\n{hint("tts_hint_code")}\n'
        return '\n'
    clean_text = re.sub(r'```.*?```', replace_code_block, clean_text, flags=re.DOTALL).strip()

    # Replace markdown tables with spoken hint
    # Tables are unreadable as speech: "pipe Name pipe Age pipe newline pipe dash dash..."
    # Instead of just removing, add a spoken cue so listener knows a table was shown
    #
    # Handle THREE cases:
    # 1. Complete table rows: starts AND ends with | (e.g., "| Name | Age |")
    # 2. Partial table rows in streaming: starts with | but incomplete (e.g., "| Marmelade (z.")
    # 3. Multi-line table blocks in non-streaming mode
    #
    # IMPORTANT for streaming: Table cells come in piece by piece, so we must detect
    # PARTIAL table content, not just complete rows!

    stripped = clean_text.strip()

    # Detect if this is multi-line content (Re-Synth mode) vs single-line (streaming mode)
    # Multi-line content should use table block replacement, not early returns
    is_multiline = '\n' in stripped

    # Checks 1-4 are for STREAMING mode only (single-line sentences)
    # In Re-Synth mode (multi-line), we skip these and use table_block_pattern below
    global _table_hint_announced, _formula_hint_announced, _code_hint_announced
    global _list_streaming_count, _list_hint_announced

    if not is_multiline:
        # Check for table content (4 patterns)
        is_table = (
            re.match(r'^\s*\|.*\|\s*$', stripped) or  # Complete table line
            stripped.startswith('|') or  # Partial table row
            stripped.count('|') >= 2 or  # Multiple pipes
            re.match(r'^\s*\|[-:\s|]+\|\s*$', stripped)  # Separator row
        )

        if is_table:
            if not _table_hint_announced:
                _table_hint_announced = True
                return hint("tts_hint_table")
            return ""

        # Check for inline formula ($...$)
        if re.search(r'\$[^$]+\$', stripped):
            if not _formula_hint_announced:
                _formula_hint_announced = True
                return hint("tts_hint_formula")
            return ""

        # Check for inline code (`...`)
        if re.search(r'`[^`]+`', stripped):
            if not _code_hint_announced:
                _code_hint_announced = True
                return hint("tts_hint_code")
            return ""

        # List-item detection (streaming mode): count consecutive items;
        # once threshold is exceeded, suppress the rest with a single hint.
        # Items 1..N pass through to TTS, item N+1 becomes the hint, and
        # any further consecutive items are dropped until a non-list line.
        if _LIST_ITEM_RE.match(stripped):
            _list_streaming_count += 1
            if _list_streaming_count > _get_tts_list_max_items():
                if not _list_hint_announced:
                    _list_hint_announced = True
                    return hint("tts_hint_more_items")
                return ""
            # within threshold → fall through, item gets read normally
        elif _list_streaming_count > 0 and stripped and re.search(r'[a-zA-ZäöüÄÖÜß]{2,}', stripped):
            # Non-list readable line ended the run — reset list state
            _list_streaming_count = 0
            _list_hint_announced = False

        # Regular text detected - reset all flags for next block
        # Only reset if there's actual readable content (words), not just:
        # - Empty strings (from filtered decorative lines ═══)
        # - Pure punctuation or formatting remnants
        # This prevents false resets between table rows
        if stripped and re.search(r'[a-zA-ZäöüÄÖÜß]{2,}', stripped) and not _LIST_ITEM_RE.match(stripped):
            reset_content_hint_flags()

    # Multi-line: replace table blocks with hint (Re-Synth / non-streaming full response)
    # Use GLOBAL flag to persist across multiple calls (streaming chunks)
    table_block_pattern = re.compile(r'(\|[^\n]+\|\n?)+', flags=re.MULTILINE)
    if table_block_pattern.search(clean_text):
        def replace_table(m):
            global _table_hint_announced
            if not _table_hint_announced:
                _table_hint_announced = True
                return f'\n{hint("tts_hint_table")}\n'
            return '\n'
        clean_text = table_block_pattern.sub(replace_table, clean_text)

    # Multi-line: replace LONG markdown list blocks with a spoken hint.
    # Threshold from settings.json (audio_player → tts_list.full_max_items).
    # Short lists (≤ threshold) pass through unchanged so TTS reads them.
    # A "list block" = ≥ 2 consecutive lines starting with -, *, +, or N. / N).
    list_block_pattern = re.compile(
        r'(?:^[ \t]*(?:[-*+]|\d+[.)])\s+[^\n]*\n?){2,}',
        flags=re.MULTILINE,
    )
    _list_max_multi = _get_tts_list_max_items()

    def _replace_long_list(m: 're.Match[str]') -> str:
        block = m.group(0)
        item_count = len(re.findall(
            r'^[ \t]*(?:[-*+]|\d+[.)])\s+', block, flags=re.MULTILINE,
        ))
        if item_count > _list_max_multi:
            return f'\n{t("tts_hint_list", lang=language, items=item_count)}\n'
        return block

    clean_text = list_block_pattern.sub(_replace_long_list, clean_text)

    # Clean up multiple empty lines left by table/list replacement
    clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()

    # Replace LaTeX formulas with spoken hint - both inline ($...$) and block ($$...$$)
    # Formulas like "$E = mc^2$" sound like "dollar E equals m c caret 2 dollar"
    # Use GLOBAL flag to persist across multiple calls (streaming chunks)
    def replace_formula(m):
        global _formula_hint_announced
        if not _formula_hint_announced:
            _formula_hint_announced = True
            return f' {hint("tts_hint_formula")} '
        return ' '
    clean_text = re.sub(r'\$\$[^$]+\$\$', replace_formula, clean_text, flags=re.DOTALL)  # Block formulas
    clean_text = re.sub(r'\$[^$]+\$', replace_formula, clean_text)  # Inline formulas

    # Remove markdown links [text](url) → keep "text", remove URL
    clean_text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean_text)

    # Remove markdown images ![alt](url) → remove entirely
    clean_text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', clean_text)

    # Remove blockquotes (> at start of line) but keep the text
    clean_text = re.sub(r'^>\s*', '', clean_text, flags=re.MULTILINE)

    # Note: Multi-Agent consensus tags ([LGTM], [WEITER]) are translated to natural
    # language in add_agent_panel() BEFORE they reach chat_history. TTS speaks
    # what the user sees in the UI - no duplicate translation needed here.

    # Remove Multi-Agent round labels - these are UI markers prepended to messages
    # e.g., "[Auto-Konsens: Synthese R1]", "[Tribunal: Kritische Prüfung R2]"
    clean_text = ROUND_LABEL.sub('', clean_text)

    # Remove remaining square bracket content like [VERTEIDIGUNG], [Quelle], etc.
    # Structural markers not meant to be read aloud.
    # MUST come AFTER markdown link processing ([text](url) → text) to preserve link text
    clean_text = re.sub(r'\[.*?\]', '', clean_text)

    # Remove most emojis, but KEEP laughter emojis for XTTS to convert to "hahaha"
    # Laughter emojis: 😂🤣😆😄😅😁🙂😊 (handled by XTTS server.py)
    # First, remove all emojis EXCEPT laughter ones
    emoji_pattern = re.compile(
        "["
        "\U0001F300-\U0001F5FF"  # Symbols & Pictographs (incl. clock faces 🕐-🕧)
        "\U0001F680-\U0001F6FF"  # Transport & Maps
        "\U0001F700-\U0001F77F"  # Alchemy Symbols
        "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
        "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
        "\U0001F900-\U0001F9FF"  # Supplemental Symbols & Pictographs
        "\U0001FA00-\U0001FA6F"  # Chess Symbols
        "\U0001FA70-\U0001FAFF"  # Symbols & Pictographs Extended-A
        "\U0001F1E0-\U0001F1FF"  # Flags
        "\U00002600-\U000027BF"  # Misc Symbols (☀️⭐)
        "\U0000FE00-\U0000FE0F"  # Variation Selectors
        "\U0001F018-\U0001F270"  # Additional symbols
        "\U0000238C-\U00002454"  # Misc Technical
        "\u200d"                  # Zero Width Joiner
        "\ufe0f"                  # Variation Selector
        "\u3030"                  # Wavy Dash
        "]+",
        flags=re.UNICODE
    )
    clean_text = emoji_pattern.sub(r'', clean_text).strip()

    # Remove non-laughter emoticons (U+1F600-1F64F) but keep 😂🤣😆😄😅😁🙂😊
    # These are: U+1F602, U+1F923, U+1F606, U+1F604, U+1F605, U+1F601, U+1F642, U+1F60A
    laughter_emojis = {'😂', '🤣', '😆', '😄', '😅', '😁', '🙂', '😊'}
    # Remove other emoticons one by one (safer than regex for this range)
    for codepoint in range(0x1F600, 0x1F650):
        char = chr(codepoint)
        if char not in laughter_emojis:
            clean_text = clean_text.replace(char, '')

    # Replace decorative separator lines with pause (cause crackling in Piper TTS)
    # Unicode box-drawing characters: ─ (U+2500), ═ (U+2550), │ (U+2502), etc.
    # Replace with newline to create a natural pause in speech
    clean_text = re.sub(r'[─━═┄┅┈┉╌╍]+', '\n', clean_text)  # Horizontal lines → pause
    clean_text = re.sub(r'[│┃║┆┇┊┋╎╏]+', '', clean_text)   # Vertical lines → remove
    clean_text = re.sub(r'[-]{3,}', '\n', clean_text)  # ASCII dashes (---) → pause
    clean_text = re.sub(r'[=]{3,}', '\n', clean_text)  # ASCII equals (===) → pause
    clean_text = re.sub(r'[_]{3,}', '\n', clean_text)  # ASCII underscores (___) → pause

    # Remove the performance footer "*( TTFT: ...    Source: AIfred (Modell) [vllm] ... )*"
    # (formatting.format_metadata) as a whole, BEFORE the asterisks go: it contains
    # parentheses of its own ("PP: ... (5.685 tok)", "AIfred (Modell)"), so the
    # timing filter below would cut at the first ")" and leave the rest to be read.
    clean_text = PERFORMANCE_FOOTER.sub('', clean_text)

    # Remove markdown formatting and special characters
    clean_text = re.sub(r'\*\*', '', clean_text)  # Bold **text**
    clean_text = re.sub(r'\*', '', clean_text)    # Italic *text* or bullet points
    # Replace inline code with hint (if no code block hint was given already)
    # Uses same GLOBAL flag as code blocks to avoid duplicate hints
    def replace_inline_code(m):
        global _code_hint_announced
        if not _code_hint_announced:
            _code_hint_announced = True
            return f' {hint("tts_hint_code")} '
        return ''
    clean_text = re.sub(r'`[^`]+`', replace_inline_code, clean_text)
    clean_text = re.sub(r'`', '', clean_text)     # Stray backticks
    clean_text = re.sub(r'#+\s', '', clean_text)  # Markdown Headers ### Text

    # Remove list markers but keep text
    clean_text = re.sub(r'^[-*+]\s+', '', clean_text, flags=re.MULTILINE)  # Bullet points
    clean_text = re.sub(r'^\d+\.\s+', '', clean_text, flags=re.MULTILINE)  # Numbered lists

    # Remove URLs (http://, https://, www.)
    clean_text = re.sub(r'https?://\S+', '', clean_text)  # http:// and https://
    clean_text = re.sub(r'www\.\S+', '', clean_text)      # www.example.com

    # Remove timing information in parentheses (TTS should not read these!)
    # Examples: "(STT: 2.5s)", "(Inference: 1.3s)", "(Agent: 45.2s, Quick, 5 sources)",
    #           "(Cache-Hit: 2.5s = LLM 2.3s)", "(Decision: 2.5s, Inference: 1.3s)"
    #           "( TTFT: 0,32s  Inference: 6,6s  133,4 tok/s  Source: Training data )"
    clean_text = TIMING_PARENTHESES.sub('', clean_text)

    # Remove any remaining parentheses with numbers/timing patterns
    # Catches edge cases like "(2.5s)" or "(123 tok/s)" that might slip through
    clean_text = NUMBER_PARENTHESES.sub('', clean_text)

    # Remove problematic Unicode characters that Piper can't handle
    # Zero-width characters, non-breaking spaces, etc.
    clean_text = re.sub(r'[\u200b\u200c\u200d\u2060\ufeff]', '', clean_text)  # Zero-width chars
    clean_text = re.sub(r'\u00a0', ' ', clean_text)  # Non-breaking space → normal space

    # Convert dashes to punctuation BEFORE the general filter removes them
    # Gedankenstriche (EN DASH, EM DASH) mark pauses - convert to comma for natural TTS pause
    # Without this, "Text – mehr Text" becomes "Text mehr Text" (no pause, words run together)
    clean_text = clean_text.replace('–', ',')  # EN DASH (U+2013) → comma
    clean_text = clean_text.replace('—', ',')  # EM DASH (U+2014) → comma
    clean_text = clean_text.replace('‒', ',')  # FIGURE DASH (U+2012) → comma
    clean_text = clean_text.replace('―', ',')  # HORIZONTAL BAR (U+2015) → comma

    # Remove other special characters that cause "quirzel" sounds in TTS
    # Keep basic punctuation and letters (including German/French/Spanish chars)
    # Also keep: ° (degree), − (minus sign U+2212), / (for "km/h" etc.), % (percent)
    # This catches arrows (→←↑↓), math symbols (±×÷), etc.
    clean_text = re.sub(r'[^\w\s.,!?;:\-\'\"()\[\]äöüÄÖÜßàáâãèéêëìíîïòóôõùúûýÿñçÀÁÂÃÈÉÊËÌÍÎÏÒÓÔÕÙÚÛÝŸÑÇ°−/%\n]', ' ', clean_text)

    # Clean up multiple spaces
    clean_text = re.sub(r'  +', ' ', clean_text)

    # Clean up trailing whitespace and excessive newlines (Piper crackling fix)
    clean_text = re.sub(r'\n{3,}', '\n\n', clean_text)  # Max 2 newlines
    clean_text = clean_text.strip()  # Remove leading/trailing whitespace

    # NOTE: Punctuation is handled by XTTS server's normalize_text_for_tts()
    # This avoids issues with partial text in streaming mode where "S" became "S."

    return clean_text


def cleanup_old_tts_audio(max_age_hours: int = 24) -> int:
    """
    Delete old TTS audio files from uploaded_files/tts_audio/.

    Args:
        max_age_hours: Maximum age in hours (default: 24)

    Returns:
        int: Number of deleted files
    """
    import time

    if not TTS_AUDIO_DIR.exists():
        return 0

    max_age_seconds = max_age_hours * 3600
    current_time = time.time()
    deleted = 0

    try:
        for file_path in TTS_AUDIO_DIR.glob("audio_*"):
            if file_path.is_file():
                file_age = current_time - file_path.stat().st_mtime
                if file_age > max_age_seconds:
                    file_path.unlink()
                    deleted += 1
                    log_message(f"🗑️ Deleted old TTS audio: {file_path.name} (age: {file_age / 3600:.1f}h)")

        if deleted > 0:
            log_message(f"🧹 TTS Audio Cleanup: {deleted} old files deleted")

    except Exception as e:
        log_message(f"❌ TTS cleanup error: {e}")

    return deleted


def release_whisper_gpu(reason: str) -> bool:
    """End the STT-Suite GPU workers (transcription and diarization) so their
    VRAM can't collide with an LLM cold start (calibrated splits assume empty
    cards). ``reason`` goes to the service's log.

    A transcription in flight is granted WHISPER_RELEASE_WAIT_MAX_S to
    finish (the service answers 409 while busy); after the deadline the
    worker is ended by force — interactive chat has priority. The
    transcription is told the reason and goes on on the CPU (device=auto).

    Returns True if the transcription worker was actually ended. No-op (False)
    when the service is down or no GPU worker is running.
    """
    import time
    from .config import WHISPER_RELEASE_POLL_S, WHISPER_RELEASE_WAIT_MAX_S
    from .stt_client import STTBusyError, STTError, get_default_client

    client = get_default_client()
    deadline = time.monotonic() + WHISPER_RELEASE_WAIT_MAX_S
    try:
        while True:
            try:
                return "gpu-worker" in client.gpu_release(force=False, reason=reason)
            except STTBusyError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(WHISPER_RELEASE_POLL_S)
        log_message(
            f"⚠️ Whisper transcription still running after "
            f"{WHISPER_RELEASE_WAIT_MAX_S}s — force-killing GPU worker "
            f"for model load", "warning",
        )
        return "gpu-worker" in client.gpu_release(force=True, reason=reason)
    except STTError as error:
        log_message(f"⚠️ Whisper GPU release failed: {type(error).__name__}: {error}", "warning")
        return False


def whisper_gpu_footprint() -> dict[str, int]:
    """VRAM (MiB) the STT-Suite GPU worker holds, per GPU UUID — space a
    model load gets back, because the load releases the worker first
    (release_whisper_gpu in the backends' GPU guard). Empty when no worker
    runs or the service is unreachable.

    The service reports its worker's PID inside the container; nvidia-smi
    reports host PIDs. ``NSpid`` in /proc/<pid>/status lists a process's PID
    in every namespace — its last entry is the container's view, which
    identifies the worker without counting other GPU containers (TTS).
    """
    from .nvidia_smi import compute_apps
    from .stt_client import STTError, get_default_client

    try:
        status = get_default_client().status()
    except STTError as error:
        log_message(f"⚠️ Whisper footprint unknown: {type(error).__name__}: {error}", "warning")
        return {}
    worker = status.get("gpu_worker_pid")
    if not worker:
        return {}
    footprint: dict[str, int] = {}
    for row in compute_apps():
        try:
            with open(f"/proc/{row['pid']}/status", encoding="ascii") as f:
                nspid = next(line.split()[1:] for line in f if line.startswith("NSpid:"))
        except (OSError, StopIteration):
            continue
        if nspid and int(nspid[-1]) == int(worker):
            uuid = str(row["gpu_uuid"])
            footprint[uuid] = footprint.get(uuid, 0) + int(row["used_memory"])
    return footprint


def get_audio_duration(audio_path: str) -> float:
    """Audio duration in seconds via ffprobe (0.0 if not determinable)."""
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        pass
    return 0.0


def transcribe_audio_auto(
    audio_path: str, language: str = "de", log_result: bool = True,
    diarize: bool = False,
) -> tuple[str, float, str]:
    """Transcription on the device the user prefers — SSOT for what the service is asked.

    The preference (``stt_device`` in settings.json) goes to the service as it is: ``auto`` (the
    service walks its own list and takes the first entry that fits), ``cpu`` or ``cuda`` (GPU only;
    no room is an error, STTGPUUnavailableError, not a silent change to the CPU). Engine, model and
    quality are never sent: they are the service's business. One exception: a long recording
    (bigger than WHISPER_GPU_MIN_FILE_MB) always goes to the CPU, also with ``auto`` — the service's
    memory table is measured for dictations of about a minute, a long file needs far more on a card.

    Returns:
        tuple: (transcribed_text, time_in_seconds, device_used) — device_used is ``cpu`` or
        ``cuda`` as the service reports it.
    """
    from .config import WHISPER_GPU_MIN_FILE_MB
    from .settings import persisted_settings

    device = persisted_settings().get("stt_device", "auto")
    if device == "auto" and os.path.getsize(audio_path) / (1024 * 1024) > WHISPER_GPU_MIN_FILE_MB:
        device = "cpu"
    return transcribe_audio(audio_path, language=language, device=device, log_result=log_result, diarize=diarize)


def transcribe_audio(audio_path: str, language: str = "de", device: str = "cpu",
                     log_result: bool = True, diarize: bool = False) -> tuple[str, float, str]:
    """Transcribe audio to text via Whisper Docker service.

    Args:
        audio_path: Path to audio file (WAV, MP3, M4A, OGG, FLAC, WebM)
        language: Language code ("de" or "en")
        device: "cpu", "cuda" or "auto" (the service picks, see ``transcribe_audio_auto``)
        diarize: Label speakers ([SPEAKER_00] …). Only worth it for
                 recordings with several voices — a dictation has one
                 speaker and would just pay the extra model load.

    Returns:
        tuple: (transcribed_text, time_in_seconds, device_used) — with diarize the text
        carries speaker markers, so callers need no separate handling.
    """
    if not audio_path:
        return "", 0.0, device

    from .config import WHISPER_TRANSCRIBE_TIMEOUT_S
    from .stt_client import (
        STTError,
        STTTimeoutError,
        get_default_client,
    )

    try:
        data = get_default_client().transcribe(audio_path, language, device, diarize)
    except STTTimeoutError:
        minutes = WHISPER_TRANSCRIBE_TIMEOUT_S // 60
        log_message(
            f"❌ Whisper transcription timed out after {minutes} min "
            f"(device={device}) — file too long for this engine", "error",
        )
        raise TimeoutError(
            f"Transcription timed out after {minutes} min ({device} engine)"
        )
    except STTError as e:
        # Surface the real reason (e.g. "no GPU with enough VRAM") instead of
        # returning empty text — the caller shows the error as a toast.
        log_message(f"❌ Whisper service error: {e}", "error")
        raise

    user_text = data.get("text", "").strip()
    stt_time = data.get("time", 0.0)
    device_used = str(data["device"])
    for fallback in data.get("fallback", []):
        log_message(f"⚠️ STT {fallback['tried']} not used: {fallback['reason']}", "warning")
    if device_used == "cpu" and device != "cpu":
        log_message("⚠️ Transcribed on the CPU engine (slower)", "warning")
    # Speaker-labelled variant wins when diarization succeeded; a
    # failure keeps the plain transcript (the service reports why).
    if diarize:
        speaker_text = (data.get("text_speakers") or "").strip()
        if speaker_text:
            speakers = data.get("speakers") or []
            user_text = speaker_text
            log_message(
                f"🗣️ Diarization: {len(speakers)} speaker(s) "
                f"in {data.get('diarize_time', 0):.1f}s"
            )
        elif data.get("diarize_error"):
            log_message(f"⚠️ Diarization failed: {data['diarize_error']}", "warning")
    if log_result:
        log_message(
            f"✅ STT Transcription: {user_text[:100]}"
            f"{'...' if len(user_text) > 100 else ''} "
            f"(Time: {stt_time:.1f}s)"
        )
    return user_text, stt_time, device_used


# ============================================================
# CLEANUP: Delete old TTS audio files on app startup/shutdown
# ============================================================

@atexit.register
def _cleanup_tts_on_exit():
    """Cleanup old TTS audio files on app exit"""
    try:
        cleanup_old_tts_audio(max_age_hours=24)
    except (OSError, httpx.HTTPError):
        pass

# Run cleanup on module import (app startup)
try:
    cleanup_old_tts_audio(max_age_hours=24)
except (OSError, ValueError):
    pass
