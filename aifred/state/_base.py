"""
Reflex State Management for AIfred Intelligence

Main state for chat, settings, and backend management
"""

import re
import reflex as rx
from typing import List, Any, Dict, TypedDict
import os
import asyncio
from ..lib import (
    log_message,
    console_separator
)
from ..lib.logging_utils import CONSOLE_SEPARATOR
from ..lib import config


# Pattern for structured data that STT likely transcribes incorrectly.
# Whisper often transcribes "@" as "at" and "." as "punkt"/"dot".
_STRUCTURED_DATA_RE = re.compile(
    r"@"                                    # email: literal @
    r"|\bat\b.*\b(?:punkt|dot)\b"           # email: "at ... punkt/dot" (STT transcription)
    r"|www\s*\.\s*"                         # URL: www. (with possible spaces)
    r"|www\s+punkt\s+"                      # URL: "www punkt" (STT)
    r"|\.\s*(?:com|de|org|net|io|edu)\b"    # URL: .com, .de etc.
    r"|\bpunkt\s*(?:com|de|org|net|io|edu)\b"  # URL: "punkt de" (STT)
    r"|https?\s*:\s*/\s*/"                  # URL: http(s)://
)


def _transcription_needs_review(text: str) -> bool:
    """Check if STT transcription contains structured data that needs manual review."""
    return bool(_STRUCTURED_DATA_RE.search(text.lower()))

# ============================================================
# TTS Audio Broker - Bridge between create_task and Frontend
# ============================================================
# TypedDicts for Reflex (foreach requires typed dicts)
# ============================================================

class FailedSourceDict(TypedDict):
    """A single failed source entry"""
    url: str
    error: str
    method: str

class ChatMessage(TypedDict):
    """Single chat message in new dict-based format.

    Each message is standalone - no more (user, ai) tuples.
    User messages and assistant messages are separate entries.
    """
    role: str           # "user" | "assistant" | "system" (for summaries)
    content: str        # Message content (with markers for UI display)
    agent: str          # "" | "aifred" | "sokrates" | "salomo"
    mode: str           # "" | "direct" | "synthesis" | "tribunal" | "refinement" | ...
    round_num: int | None  # None/0 = no round, 1+ = round number
    metadata: Dict[str, Any]  # ttft, inference_time, tokens_per_sec, etc.
    timestamp: str      # ISO timestamp
    # Web research sources (top-level for Reflex UI access - also in metadata for export)
    used_sources: List[Dict[str, Any]]    # [{"url": str, "word_count": int}]
    failed_sources: List[Dict[str, Any]]  # [{"url": str, "error": str, "method": str}]
    # Audio replay (top-level for Reflex UI access)
    has_audio: bool  # True if audio_urls is non-empty
    audio_urls_json: str  # JSON string of audio URLs (for JS playback)

# ============================================================
# Module-Level Backend State (Global across all sessions)
# ============================================================
# Prevents re-initialization on page reload
# Backend is initialized once at server startup
_global_backend_initialized = False
_global_backend_state: dict[str, Any] = {
    "backend_type": None,
    "backend_url": None,
    "aifred_model": None,
    "automatik_model": None,
    "available_models": [],
    "gpu_info": None,
    "vllm_manager": None,  # Global vLLM process manager (persists across reloads)
}

# Lock to prevent race conditions during backend initialization
# (e.g., two browser tabs starting simultaneously)
_backend_init_lock = asyncio.Lock()

# ============================================================
# Whisper STT - Now in aifred/lib/audio_processing.py
# ============================================================
# Import from audio_processing module
from ..lib.audio_processing import (  # noqa: E402
    initialize_whisper_model,
    get_whisper_model
)

# Mixins
from ._auth_mixin import AuthMixin  # noqa: E402
from ._image_mixin import ImageMixin  # noqa: E402
from ._document_mixin import DocumentMixin  # noqa: E402
from ._export_mixin import ExportMixin  # noqa: E402
from ._session_mixin import SessionMixin  # noqa: E402
from ._tts_config_mixin import TTSConfigMixin  # noqa: E402
from ._tts_streaming_mixin import TTSStreamingMixin  # noqa: E402
from ._agent_config_mixin import AgentConfigMixin  # noqa: E402
from ._settings_mixin import SettingsMixin  # noqa: E402
from ._calibration_mixin import CalibrationMixin  # noqa: E402
from ._backend_mixin import BackendMixin  # noqa: E402
from ._chat_mixin import ChatMixin  # noqa: E402
from ._ui_config_mixin import UIConfigMixin  # noqa: E402

class AIState(  # type: ignore[misc]
    AuthMixin,
    ImageMixin,
    DocumentMixin,
    ExportMixin,
    SessionMixin,
    TTSConfigMixin,
    TTSStreamingMixin,
    AgentConfigMixin,
    SettingsMixin,
    CalibrationMixin,
    BackendMixin,
    ChatMixin,
    UIConfigMixin,
    rx.State,
):
    """Main application state - composed from mixins.

    State variables and methods are distributed across mixins:
    - ChatMixin: message sending, streaming, agent panels, debug console
    - BackendMixin: backend init, model selection, GPU detection
    - TTSConfigMixin: TTS engine/voice configuration
    - TTSStreamingMixin: TTS audio generation, streaming, queue
    - AgentConfigMixin: personality, reasoning, thinking, sampling, multi-agent
    - SettingsMixin: settings save/load, user preferences
    - CalibrationMixin: context calibration, backend restart
    - UIConfigMixin: temperature, context, research mode, whisper
    - AuthMixin: login/logout, user authentication
    - ImageMixin: image upload, crop, lightbox
    - ExportMixin: chat export (HTML)
    - SessionMixin: session management, persistence
    """

    # ── State Variables (only those NOT in any mixin) ────────────
    # NOTE: chat_history and llm_history live in ChatHistoryState (separate React context)

    # Web Research Sources State (for current request - shown in UI)
    all_sources: List[Dict[str, Any]] = []
    used_sources: List[Dict[str, Any]] = []
    failed_sources: List[Dict[str, str]] = []
    _pending_used_sources: List[Dict[str, Any]] = []
    _pending_failed_sources: List[Dict[str, str]] = []

    # Last detected language from Intent Detection (used across mixins)
    _last_detected_language: str = ""

    # ── SubState Accessors ─────────────────────────────────────────

    def _chat_sub(self):
        """Get ChatHistoryState substate instance (sync, for history access)."""
        from aifred.state._chat_history_state import ChatHistoryState
        return self._get_state_from_cache(ChatHistoryState)

    def _log_history_utilization(self, effective_limit: int) -> None:
        """Log history token utilization and compression warning to debug console."""
        from ..lib.context_manager import estimate_tokens_from_llm_history
        from ..lib.config import HISTORY_COMPRESSION_TRIGGER
        from ..lib.formatting import format_number

        _llm_hist = self._chat_sub().llm_history
        if _llm_hist and effective_limit > 0:
            estimated_tokens = estimate_tokens_from_llm_history(_llm_hist)
            utilization = (estimated_tokens / effective_limit) * 100
            self.add_debug(  # type: ignore[attr-defined]
                f"   \u2514\u2500 History: {format_number(estimated_tokens)} / "
                f"{format_number(effective_limit)} tok ({int(utilization)}%)"
            )
            if utilization >= HISTORY_COMPRESSION_TRIGGER * 100:
                self.add_debug(  # type: ignore[attr-defined]
                    f"\u26a0\ufe0f History compression will trigger on next message "
                    f"(>{int(HISTORY_COMPRESSION_TRIGGER * 100)}%)"
                )
        elif not _llm_hist:
            self.add_debug("   \u2514\u2500 History: empty")  # type: ignore[attr-defined]
        else:
            self.add_debug(f"   \u2514\u2500 Effective limit: {format_number(effective_limit)} tokens")  # type: ignore[attr-defined]

    # ── Methods (only those NOT in any mixin) ────────────────────

    def refresh_debug_console(self):
        """
        Refresh debug console to propagate background task updates

        Background tasks (like InactivityMonitor) can modify self.debug_messages
        but without yield, changes don't propagate to UI. This event handler
        forces a UI refresh by yielding.

        Called periodically from UI via rx.moment() interval.

        Also checks for API update flags - if flag exists for this session_id,
        triggers browser reload to sync session data from API changes.
        """
        # Check if settings.json was modified (mtime-based, multi-browser safe)
        # Each browser tracks its own last-seen mtime - no race conditions
        import os
        from ..lib.settings import SETTINGS_FILE
        try:
            current_mtime = os.path.getmtime(SETTINGS_FILE)
            if current_mtime > self._last_settings_mtime:
                self._reload_settings_from_file()
                self._last_settings_mtime = current_mtime
                self.add_debug("⚙️ Settings reloaded")
                yield
                return
        except OSError:
            pass  # File doesn't exist or not accessible

        # Check for pending message from API (message injection)
        if self.session_id and not self.is_generating:
            from ..lib.session_storage import get_and_clear_pending_message
            pending_msg = get_and_clear_pending_message(self.session_id)
            if pending_msg:
                self.current_user_input = pending_msg
                self.add_debug(f"📨 API: Message injected ({len(pending_msg)} chars)")
                yield  # Update UI with debug message and input field
                # Trigger send_message as next event in chain
                return AIState.send_message

        # Check for global Message Hub notifications FIRST (toast should appear immediately)
        from ..lib.message_processor import read_and_clear_hub_notification
        from ..lib.i18n import t as _t
        notification = read_and_clear_hub_notification()
        toast_event = None
        if notification:
            channel = notification.get("channel", "?")
            sender = notification.get("sender", "")
            status = notification.get("status", "received")
            self.refresh_session_list()

            # Ghost browser controls as soon as Hub message arrives (not just processing)
            if status in ("received", "processing"):
                self.is_generating = True
            elif status in ("done", "error"):
                self.is_generating = False

            # Phase-dependent toast (same id="hub" → replaces previous)
            toast_style = {"width": "420px"}
            toast_kwargs = dict(id="hub", position="top-center", style=toast_style)
            if status == "received":
                toast_msg = _t("hub_toast_received", lang=self.ui_language, channel=channel, sender=sender)
                toast_event = rx.toast.info(toast_msg, duration=120000, **toast_kwargs)
            elif status == "processing":
                toast_msg = _t("hub_toast_processing", lang=self.ui_language, channel=channel, sender=sender)
                toast_event = rx.toast.loading(toast_msg, duration=120000, **toast_kwargs)
            elif status == "done":
                toast_msg = _t("hub_toast_done", lang=self.ui_language, channel=channel, sender=sender)
                toast_event = rx.toast.success(toast_msg, duration=5000, **toast_kwargs)
            elif status == "error":
                toast_msg = _t("hub_toast_error", lang=self.ui_language, channel=channel, sender=sender)
                toast_event = rx.toast.error(toast_msg, duration=8000, **toast_kwargs)

        # SSOT MTIME WATCH: Check if session file was modified externally
        # (other tab, API, channel, message_processor, debug_bus).
        #
        # This is the single source of truth for detecting session changes —
        # replaces the legacy update_flag mechanism. Every writer (browser,
        # API, hub) updates the session file → mtime changes → all tabs
        # detect and reload on the next tick.
        #
        # Skipped during generation (is_generating) because local state
        # is ahead of disk (user message added before LLM call).
        if self.session_id and not self.is_generating:
            from ..lib.session_storage import get_session_path, load_session
            try:
                session_path = get_session_path(self.session_id)
                if session_path.exists():
                    session_mtime = os.path.getmtime(session_path)
                    if session_mtime > self._last_session_mtime:
                        # External write detected → full session reload
                        session = load_session(self.session_id)
                        if session and session.get("data"):
                            # Use file's debug_messages directly (no prepend of startup
                            # messages which are already in browser memory).
                            file_debug = session["data"].pop("debug_messages", None)
                            if file_debug is not None:
                                self.debug_messages = file_debug
                            self._restore_session(session)
                            msg_count = len(self._chat_sub().chat_history)
                            self.add_debug(
                                f"🔄 Session synced ({msg_count} messages)"
                            )
                        self._last_session_mtime = session_mtime
                        # Force scroll + toast (if any) in one yield
                        if toast_event:
                            yield toast_event
                        yield rx.call_script("forceScrollToBottom()")
                        return
            except (OSError, ValueError):
                pass

        # Toast without session change (notification for a different session)
        if toast_event:
            yield toast_event
            return

        # Just yield to propagate any state changes to UI
        # No need to modify anything - self.debug_messages already has the data
        yield

    def _get_backend_url(self) -> str:
        """Get current backend URL based on backend_type."""
        # Use already imported config from ..lib.config (top of file)
        if self.backend_type == "ollama":
            return config.DEFAULT_OLLAMA_URL
        elif self.backend_type == "vllm":
            return config.DEFAULT_VLLM_URL
        elif self.backend_type == "tabbyapi":
            return config.DEFAULT_TABBYAPI_URL
        elif self.backend_type == "llamacpp":
            return config.DEFAULT_LLAMACPP_URL
        elif self.backend_type == "cloud_api":
            # Cloud API URL is determined by provider in BackendFactory
            return ""
        return config.DEFAULT_OLLAMA_URL

    # Image Handlers → ImageMixin
    # ============================================================
    # AUDIO UPLOAD HANDLER (STT)
    # ============================================================

    async def handle_audio_upload(self, files: List[rx.UploadFile]):
        """Handle audio file uploads and transcribe with Whisper STT"""
        # Lazy load Whisper model if not already loaded
        whisper_model = get_whisper_model()
        if whisper_model is None:
            self.add_debug("🎤 Loading Whisper model...")
            whisper_model = initialize_whisper_model(self.whisper_model_key)  # type: ignore[arg-type]
            if whisper_model is None:
                self.add_debug("❌ Failed to load Whisper model")
                return

        # Validate file
        if not files or len(files) == 0:
            self.add_debug("⚠️ No audio file provided")
            return

        file = files[0]  # Only process first file

        # Validate audio file type
        allowed_extensions = [".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"]
        file_ext = os.path.splitext(file.filename or "")[1].lower()
        if file_ext not in allowed_extensions:
            self.add_debug(f"⚠️ Unsupported audio format: {file_ext}")
            return

        # Read file content
        content = await file.read()
        file_size_mb = len(content) / (1024 * 1024)

        # Size limit: 25 MB (Whisper can handle longer files)
        if file_size_mb > 25:
            from ..lib.formatting import format_number
            self.add_debug(f"⚠️ Audio file too large: {format_number(file_size_mb, 1)} MB (max 25 MB)")
            return

        # Save to temporary file for Whisper processing
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as tmp_file:
            tmp_file.write(content)
            tmp_path = tmp_file.name

        try:
            # Transcribe with Whisper
            from ..lib.audio_processing import transcribe_audio
            from ..lib.formatting import format_number

            # Show KB for small files, MB for larger files (German number format)
            if file_size_mb < 1:
                file_size_kb = len(content) / 1024
                size_display = f"{format_number(file_size_kb, 0)} KB"
            else:
                size_display = f"{format_number(file_size_mb, 1)} MB"

            self.add_debug(f"🎤 Transcribing audio: {file.filename} ({size_display})...")

            user_text, stt_time = transcribe_audio(tmp_path, whisper_model, self.ui_language)

            if user_text:
                # German number format: 0,2s instead of 0.2s
                from ..lib.formatting import format_number
                self.add_debug(f"✅ Transcription complete ({format_number(stt_time, 1)}s)")

                # Auto-enable edit mode if transcription contains structured data
                # (email addresses, URLs) that STT likely got wrong
                force_edit = _transcription_needs_review(user_text)
                if force_edit and not self.show_transcription:
                    self.add_debug("✏️ Email/URL detected → edit mode enabled")

                # Show Transcription Workflow
                if self.show_transcription or force_edit:
                    # Mode: Edit text → Send manually
                    # Append to existing text (multiple recordings)
                    if self.current_user_input:
                        self.current_user_input += " " + user_text
                    else:
                        self.current_user_input = user_text
                    # Append to uncontrolled textarea via JavaScript
                    import json
                    yield rx.call_script(
                        f"var el = document.getElementById('user-text-input');"
                        f" var t = {json.dumps(user_text)};"
                        f" el.value = el.value.trim() ? el.value + ' ' + t : t"
                    )
                    self.add_debug("✏️ Text in input field → Ready for editing")
                    # Separator after STT complete (user will edit + send manually)
                    self.add_debug(CONSOLE_SEPARATOR)
                    console_separator()  # Log-File
                else:
                    # Mode: Direct to AI (no append, send immediately)
                    self.current_user_input = user_text
                    self.add_debug("🚀 Sending text directly to AI...")
                    # Separator after STT, before send_message starts
                    self.add_debug(CONSOLE_SEPARATOR)
                    console_separator()  # Log-File
                    # Forward yields from send_message() to update UI in real-time
                    async for _ in self.send_message():
                        yield  # Forward to UI for real-time updates
            else:
                self.add_debug("⚠️ Transcription returned empty text")

        except (ImportError, RuntimeError, ValueError, OSError) as e:
            self.add_debug(f"❌ Audio transcription failed: {e}")
            log_message(f"❌ Audio transcription error: {e}")
        finally:
            # Clean up temporary file
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    async def clear_vector_cache(self):
        """Clear Vector DB by deleting all documents (keeps collection intact)"""
        try:
            self.add_debug("🗑️ Clearing Vector DB...")
            yield  # Update UI immediately

            import chromadb
            client = chromadb.HttpClient(host='localhost', port=8000)

            # Get collection (must match name in vector_cache.py)
            collection = client.get_collection('research_cache')

            # Get all document IDs
            all_ids = collection.get(include=[])["ids"]

            if all_ids:
                count = len(all_ids)
                self.add_debug(f"   📊 Deleting {count} entries...")
                yield  # Update UI

                # Delete all documents (keeps collection structure intact)
                collection.delete(ids=all_ids)

                self.add_debug(f"✅ Vector DB cleared successfully ({count} entries deleted)")
                # Separator after clear operation
                self.add_debug(CONSOLE_SEPARATOR)
                console_separator()  # Log-File
                yield  # Update UI
            else:
                self.add_debug("✅ Vector DB is already empty")
                # Separator after clear operation
                self.add_debug(CONSOLE_SEPARATOR)
                console_separator()  # Log-File
                yield  # Update UI

        except Exception as e:
            self.add_debug(f"❌ Vector DB clear failed: {e}")
            # Separator after error
            self.add_debug(CONSOLE_SEPARATOR)
            console_separator()  # Log-File
            yield  # Update UI even on error

    def toggle_yarn(self):
        """Toggle YaRN context extension"""
        self.enable_yarn = not self.enable_yarn
        status = "enabled" if self.enable_yarn else "disabled"
        self.add_debug(f"📏 YaRN Context Extension {status} (Factor: {self.yarn_factor}x)")
        if self.enable_yarn:
            self.add_debug("⚠️ Click 'Apply YaRN' to start backend with new factor!")
        self._save_settings()

    def set_yarn_factor_input(self, factor: str):
        """Update YaRN factor input field (temporary, not applied yet)"""
        self.yarn_factor_input = factor
        # Calculate estimated context for preview
        try:
            # Normalize comma to point for German locale
            factor_normalized = factor.replace(',', '.')
            factor_float = float(factor_normalized)
            if 1.0 <= factor_float <= 8.0 and self.vllm_max_tokens > 0:
                estimated_context = int(self.vllm_max_tokens * factor_float)
                self.add_debug(f"📏 YaRN factor: {factor_float}x (~{estimated_context} tokens)")
        except ValueError:
            pass  # Ignore invalid input during typing

    async def apply_yarn_factor(self):
        """Apply YaRN factor and restart backend"""
        try:
            # Normalize comma to point for German locale
            factor_normalized = self.yarn_factor_input.replace(',', '.')
            factor_float = float(factor_normalized)
            if not (1.0 <= factor_float <= 8.0):
                self.add_debug(f"❌ YaRN factor must be between 1.0 and 8.0 (entered: {factor_float})")
                return

            old_factor = self.yarn_factor
            self.yarn_factor = factor_float
            self._save_settings()

            estimated_context = int(self.vllm_max_tokens * factor_float)
            self.add_debug(f"✅ YaRN factor set: {old_factor}x → {factor_float}x (~{estimated_context} tokens)")

            # Warn if factor is high (potential VRAM overflow)
            if factor_float > 2.0:
                self.add_debug(f"⚠️ High YaRN factor ({factor_float}x) may exceed VRAM → possible crash!")
                self.add_debug("💡 Tip: For VRAM issues, reduce factor or use more GPU RAM")

            # Force restart backend for YaRN change (vLLM/TabbyAPI)
            if self.backend_type in ["vllm", "tabbyapi"]:
                self.add_debug("🔄 Backend restart for YaRN change...")

                # Show loading spinner
                self.vllm_restarting = True
                yield  # Update UI to show spinner

                try:
                    if self.backend_type == "vllm":
                        await self._restart_vllm_with_new_config()
                    else:  # tabbyapi
                        await self.initialize_backend()  # TabbyAPI might not need full restart

                    # Show actual factor after restart (might have been reduced by auto-calibration)
                    actual_factor = self.yarn_factor
                    if actual_factor != factor_float:
                        self.add_debug(f"✅ Backend restarted (YaRN: {factor_float}x → {actual_factor}x after auto-calibration)")
                    else:
                        self.add_debug(f"✅ Backend restarted with YaRN {actual_factor}x")

                finally:
                    # Hide loading spinner
                    self.vllm_restarting = False
                    yield  # Update UI to hide spinner

        except ValueError:
            self.add_debug(f"❌ Invalid YaRN factor: {self.yarn_factor_input}")

