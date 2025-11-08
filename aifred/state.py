"""
Reflex State Management for AIfred Intelligence

Main state for chat, settings, and backend management
"""

import reflex as rx
from typing import List, Tuple, Dict
import threading
import uuid
import os
from pydantic import BaseModel
from .backends import BackendFactory, LLMMessage, LLMOptions
from .lib import (
    initialize_debug_log,
    log_message,
    console_separator,
    clear_console,
    # set_research_cache removed - cache system deprecated
    perform_agent_research,
    set_language,
    detect_language
)
from .lib.formatting import format_debug_message

# ============================================================
# Module-Level Vector Cache V2 (Worker Thread Pattern)
# ============================================================
# FIXED: Using v2 with dedicated worker thread to avoid blocking event loop
from .lib.vector_cache_v2 import get_worker, query_cache_async, add_to_cache_async, get_cache_stats_async

def initialize_vector_cache_worker():
    """
    Initialize Vector Cache Worker (NON-BLOCKING)

    This starts the dedicated worker thread for ChromaDB operations.
    Call this during app startup (lifespan task) to warm up the cache.

    Returns immediately - initialization happens in background thread.
    """
    import time

    try:
        log_message(f"🚀 Vector Cache Worker: Starting (PID: {os.getpid()})")
        worker = get_worker(persist_directory="./aifred_vector_cache")

        # Give worker thread 2 seconds to initialize ChromaDB in background
        # This prevents blocking on the first query
        log_message("⏳ Vector Cache Worker: Waiting for ChromaDB warmup (2s)...")
        time.sleep(2.0)

        log_message(f"✅ Vector Cache Worker: Started successfully")
        return worker
    except Exception as e:
        log_message(f"⚠️ Vector Cache Worker failed to start: {e}")
        return None


class ChatMessage(BaseModel):
    """Single chat message"""
    role: str  # "user" or "assistant"
    content: str
    timestamp: str = ""


class AIState(rx.State):
    """Main application state"""

    # Chat History
    chat_history: List[Tuple[str, str]] = []  # [(user_msg, ai_msg), ...]
    current_user_input: str = ""
    current_user_message: str = ""  # Die Nachricht die gerade verarbeitet wird
    current_ai_response: str = ""
    is_generating: bool = False
    is_compressing: bool = False  # NEU: Zeigt ob History-Kompression läuft

    # Backend Settings
    backend_type: str = "ollama"  # "ollama", "vllm"
    backend_url: str = "http://localhost:11434"  # Default Ollama URL
    selected_model: str = "qwen3:8b"
    available_models: List[str] = []

    # Automatik-LLM (für Decision und Query-Optimierung)
    automatik_model: str = "qwen2.5:3b"

    # LLM Options
    temperature: float = 0.2
    num_ctx: int = 32768

    # Cached Model Metadata (to avoid repeated API calls)
    _automatik_model_context_limit: int = 0  # Cached context limit for automatik model

    # Research Settings
    research_mode: str = "automatik"  # "quick", "deep", "automatik", "none"
    research_mode_display: str = "🤖 Automatik (KI entscheidet)"  # UI display value

    # TTS Settings
    enable_tts: bool = False

    # Session Management
    session_id: str = ""

    # Backend Status
    backend_healthy: bool = False
    backend_info: str = ""

    # Debug Console
    debug_messages: List[str] = []
    auto_refresh_enabled: bool = True  # Für Debug Console + Chat History + AI Response Area

    # UI Language Settings
    ui_language: str = "de"  # "de" or "en" - für UI Sprache

    # Processing Progress (Automatik, Scraping, LLM)
    progress_active: bool = False
    progress_phase: str = ""  # "automatik", "scraping", "llm"
    progress_current: int = 0
    progress_total: int = 0
    progress_failed: int = 0  # Anzahl fehlgeschlagener URLs

    # Initialization flags
    _backend_initialized: bool = False
    _model_preloaded: bool = False

    async def on_load(self):
        """
        Called when page loads - initialize backend and load models

        NOTE: We initialize synchronously here WITHOUT yielding,
        because WebSocket may not be fully connected yet.
        """
        if not self._backend_initialized:
            print("🔥 on_load() CALLED - Starting initialization...")

            # Synchronous initialization (NO yields, no generator pattern)
            # This populates available_models for dropdowns
            print("=" * 60)
            print("🚀 Initializing backend on page load...")
            print("=" * 60)

            # Initialize debug log (only once)
            initialize_debug_log(force_reset=False)

            # Initialize language settings
            from .lib.config import DEFAULT_LANGUAGE
            set_language(DEFAULT_LANGUAGE)
            self.add_debug(f"🌍 Language mode: {DEFAULT_LANGUAGE}")

            # Initialize Vector Cache Worker
            initialize_vector_cache_worker()
            self.add_debug("💾 Vector Cache Worker: Initialized")

            # Generate session ID
            if not self.session_id:
                self.session_id = str(uuid.uuid4())
                self.add_debug(f"🆔 Session ID: {self.session_id[:8]}...")

            # Initialize backend
            self.add_debug("🔧 Initializing backend...")
            try:
                await self.initialize_backend()
                self.add_debug("✅ Backend initialization complete")
            except Exception as e:
                self.add_debug(f"❌ Backend initialization failed: {e}")
                log_message(f"❌ Backend initialization failed: {e}")
                import traceback
                log_message(traceback.format_exc())

            self._backend_initialized = True
            print("✅ Initialization complete")

    async def initialize_backend(self):
        """Initialize LLM backend"""
        # Debug message already logged by caller (_ensure_backend_initialized)
        try:
            # Update URL based on backend type
            if self.backend_type == "ollama":
                self.backend_url = "http://localhost:11434"
            elif self.backend_type == "vllm":
                # CHANGE THIS to Aragon's IP when vLLM is running there
                self.backend_url = "http://localhost:8000/v1"

            # add_debug() already logs to file, so we only need one call
            self.add_debug(f"🔧 Creating backend: {self.backend_type}")
            # Detailed info only in log file (not in UI)
            log_message(f"   URL: {self.backend_url}")

            # SKIP health check - causes async deadlock in on_load context!
            # Assume backend is healthy and proceed
            self.backend_healthy = True
            self.backend_info = f"{self.backend_type} initializing..."
            self.add_debug(f"⚡ Backend: {self.backend_type} (skip health check)")

            # Load models SYNCHRONOUSLY via curl (no async deadlock!)
            import subprocess
            import json
            try:
                # Synchronous curl call to get model list
                result = subprocess.run(
                    ['curl', '-s', f'{self.backend_url}/api/tags'],
                    capture_output=True,
                    text=True,
                    timeout=5.0
                )

                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    self.available_models = [m["name"] for m in data.get("models", [])]

                    if not self.selected_model and self.available_models:
                        self.selected_model = self.available_models[0]
                    if not self.automatik_model and self.available_models:
                        self.automatik_model = self.available_models[0]

                    self.backend_info = f"{self.backend_type} - {len(self.available_models)} models"
                    self.backend_healthy = True
                    self.add_debug(f"✅ {len(self.available_models)} Models geladen")
                else:
                    self.backend_healthy = False
                    self.backend_info = f"{self.backend_type} not reachable"
                    self.add_debug(f"❌ Backend not reachable (curl failed)")
                    log_message(f"❌ Backend not reachable (curl exit code: {result.returncode})")

            except subprocess.TimeoutExpired:
                self.backend_healthy = False
                self.backend_info = f"{self.backend_type} timeout"
                self.add_debug(f"⏱️ Model loading timeout")
                log_message(f"⏱️ Model loading timeout (curl)")
            except Exception as e:
                self.backend_healthy = False
                self.backend_info = f"{self.backend_type} error"
                self.add_debug(f"❌ Model loading failed: {e}")
                log_message(f"❌ Model loading failed: {e}")

            # Preload Automatik-LLM via curl in background (simple & non-blocking!)
            if self.automatik_model:
                import subprocess
                preload_cmd = f'curl -s http://localhost:11434/api/chat -d \'{{"model":"{self.automatik_model}","messages":[{{"role":"user","content":"hi"}}],"stream":false,"options":{{"num_predict":1}}}}\' > /dev/null 2>&1 &'
                try:
                    subprocess.Popen(preload_cmd, shell=True)
                    log_message(f"🚀 Preloading {self.automatik_model} via curl (background)")
                    self.add_debug(f"🚀 Preloading {self.automatik_model}...")
                except Exception as e:
                    log_message(f"⚠️ Preload failed: {e}")
                    # Not critical, continue anyway

        except Exception as e:
            self.backend_healthy = False
            self.backend_info = f"Error: {str(e)}"
            self.add_debug(f"❌ Backend initialization failed: {e}")

    async def switch_backend(self, new_backend: str):
        """Switch to different backend"""
        self.add_debug(f"🔄 Switching backend from {self.backend_type} to {new_backend}...")
        self.backend_type = new_backend
        await self.initialize_backend()

    def set_progress(self, phase: str, current: int = 0, total: int = 0, failed: int = 0):
        """Update processing progress"""
        self.progress_active = True
        self.progress_phase = phase
        self.progress_current = current
        self.progress_total = total
        self.progress_failed = failed

    def clear_progress(self):
        """Clear processing progress"""
        self.progress_active = False
        self.progress_phase = ""
        self.progress_current = 0
        self.progress_total = 0
        self.progress_failed = 0

    def add_debug(self, message: str):
        """Add message to debug console"""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted_msg = f"{timestamp} | {message}"

        # Add to Reflex State
        self.debug_messages.append(formatted_msg)

        # Also add to lib console (for agent_core logging)
        log_message(message)

        # Keep only last 100 messages
        if len(self.debug_messages) > 100:
            self.debug_messages = self.debug_messages[-100:]

    def set_user_input(self, text: str):
        """Update user input"""
        self.current_user_input = text

    async def _ensure_backend_initialized(self):
        """
        Ensure backend is initialized (called from send_message)

        This is now a no-op since initialization happens in on_load().
        Kept for backwards compatibility.
        """
        if self._backend_initialized:
            return  # Already initialized by on_load()

        # Fallback: Initialize now if on_load() didn't run
        print("⚠️ Fallback initialization (on_load didn't run)")
        # Re-use on_load() logic
        await self.on_load()

    async def send_message(self):
        """
        Send message to LLM with optional web research

        Portiert von Gradio chat_interactive_mode() mit Research-Integration
        """
        if not self.current_user_input.strip():
            return

        if self.is_generating:
            self.add_debug("⚠️ Already generating, please wait...")
            return

        # Ensure backend is initialized (should already be done by on_load)
        await self._ensure_backend_initialized()

        user_msg = self.current_user_input.strip()
        self.current_user_input = ""  # Clear input
        self.current_user_message = user_msg  # Zeige sofort die Eingabe an
        self.is_generating = True
        self.current_ai_response = ""
        yield  # Update UI sofort (Eingabefeld leeren + Spinner zeigen + Eingabe anzeigen)

        # Debug message wird von agent_core.py geloggt, nicht hier!

        try:
            # ============================================================
            # PHASE 1: Research/Automatik Mode - REAL STREAMING
            # ============================================================
            research_result = None
            result_data = None

            if self.research_mode == "automatik":
                # Automatik mode: AI decides if research is needed
                # Debug message is already logged in conversation_handler.py

                # Import chat_interactive_mode
                from .lib.conversation_handler import chat_interactive_mode

                # Initialize temporary history entry for real-time display
                temp_history_index = len(self.chat_history)
                self.chat_history.append((user_msg, self.current_ai_response))
                
                # REAL STREAMING: Call async generator directly
                async for item in chat_interactive_mode(
                    user_text=user_msg,
                    stt_time=0.0,
                    model_choice=self.selected_model,
                    automatik_model=self.automatik_model,
                    history=self.chat_history[:-1],  # Exclude current temporary entry
                    session_id=self.session_id,
                    temperature_mode='auto',
                    temperature=self.temperature,
                    llm_options=None
                ):
                    # Route messages based on type
                    if item["type"] == "debug":
                        self.debug_messages.append(format_debug_message(item["message"]))
                        if len(self.debug_messages) > 100:
                            self.debug_messages = self.debug_messages[-100:]
                    elif item["type"] == "content":
                        self.current_ai_response += item["text"]
                        # Update the temporary entry in chat history with the new content
                        if temp_history_index < len(self.chat_history):
                            self.chat_history[temp_history_index] = (user_msg, self.current_ai_response)
                    elif item["type"] == "result":
                        result_data = item["data"]
                        # Extract and update history IMMEDIATELY
                        ai_text, updated_history, inference_time = result_data
                        # Replace chat history with updated one from research - message is already in history
                        self.chat_history = updated_history
                        # The message is already in the history from the streaming, no need to re-add
                        yield  # Update UI to show new history entry
                        # Clear AI response and user message windows IMMEDIATELY
                        self.current_ai_response = ""
                        self.current_user_message = ""
                        self.is_generating = False  # Stop spinner, switch UI to history display
                        yield  # Force immediate UI update to clear both windows
                        # NOTE: Loop continues for cache metadata generation (important!)
                    elif item["type"] == "progress":
                        # Update processing progress
                        if item.get("clear", False):
                            self.clear_progress()
                        else:
                            self.set_progress(
                                phase=item.get("phase", ""),
                                current=item.get("current", 0),
                                total=item.get("total", 0),
                                failed=item.get("failed", 0)
                            )
                    elif item["type"] == "history_update":
                        # Update chat history (e.g. from summarization)
                        updated_history = item["data"]
                        self.chat_history = updated_history
                        self.add_debug(f"📊 History aktualisiert: {len(updated_history)} Messages")

                    yield  # Update UI after each item

                # Set research_result flag if we got a result
                if result_data:
                    ai_text, updated_history, inference_time = result_data
                    research_result = ai_text
                    # History and clearing already handled in loop above

                # Separator nach Automatik-Mode Research
                console_separator()  # Schreibt in Log-File
                self.add_debug("────────────────────")  # Zeigt in Debug-Console
                yield

            elif self.research_mode in ["quick", "deep"]:
                # Direct research mode (quick/deep)
                self.add_debug(f"🔍 Research Mode: {self.research_mode}")

                # Initialize temporary history entry for real-time display
                temp_history_index = len(self.chat_history)
                self.chat_history.append((user_msg, self.current_ai_response))

                # REAL STREAMING: Call async generator directly
                async for item in perform_agent_research(
                    user_text=user_msg,
                    stt_time=0.0,  # Kein STT in Reflex (noch)
                    mode=self.research_mode,
                    model_choice=self.selected_model,
                    automatik_model=self.automatik_model,
                    history=self.chat_history[:-1],  # Exclude current temporary entry
                    session_id=self.session_id,
                    temperature_mode='auto',
                    temperature=self.temperature,
                    llm_options=None
                ):
                    # Route messages based on type
                    if item["type"] == "debug":
                        self.debug_messages.append(format_debug_message(item["message"]))
                        # Limit debug messages
                        if len(self.debug_messages) > 100:
                            self.debug_messages = self.debug_messages[-100:]
                    elif item["type"] == "content":
                        # REAL-TIME streaming to UI!
                        self.current_ai_response += item["text"]
                        # Update the temporary entry in chat history with the new content
                        if temp_history_index < len(self.chat_history):
                            self.chat_history[temp_history_index] = (user_msg, self.current_ai_response)
                    elif item["type"] == "result":
                        result_data = item["data"]
                        # Extract and update history IMMEDIATELY
                        ai_text, updated_history, inference_time = result_data
                        # Replace chat history with updated one from research - message is already in history
                        self.chat_history = updated_history
                        # The message is already in the history from the streaming, no need to re-add
                        yield  # Update UI to show new history entry
                        # Clear AI response and user message windows IMMEDIATELY
                        self.current_ai_response = ""
                        self.current_user_message = ""
                        self.is_generating = False  # Stop spinner, switch UI to history display
                        yield  # Force immediate UI update to clear both windows
                        # NOTE: Loop continues for cache metadata generation (important!)

                    yield  # Update UI after each item

                # Set research_result flag if we got a result
                if result_data:
                    ai_text, updated_history, inference_time = result_data
                    research_result = ai_text
                    # History and clearing already handled in loop above

            # ============================================================
            # PHASE 2: LLM Response Generation (nur wenn kein Research)
            # ============================================================

            # Initialize full_response for history
            full_response = ""

            if research_result:
                # Research already provided answer
                # History already updated by research, don't re-add
                # current_ai_response was already cleared after history update
                pass

            else:
                # Kein Research oder Research ohne Antwort → Normaler Chat
                backend = BackendFactory.create(
                    self.backend_type,
                    base_url=self.backend_url
                )

                # Initialize temporary history entry for real-time display if not already done
                if not research_result:
                    temp_history_index = len(self.chat_history)
                    self.chat_history.append((user_msg, self.current_ai_response))

                # Build messages
                messages = []

                # Add chat history (excluding the temporary entry)
                for user_turn, ai_turn in self.chat_history[:-1]:
                    messages.append(LLMMessage(role="user", content=user_turn))
                    messages.append(LLMMessage(role="assistant", content=ai_turn))

                # Add current message
                messages.append(LLMMessage(role="user", content=user_msg))

                # LLM Options
                options = LLMOptions(
                    temperature=self.temperature,
                    num_ctx=self.num_ctx
                )

                self.add_debug(f"🤖 Calling {self.backend_type} ({self.selected_model})...")

                # Stream response
                metrics = None

                async for chunk in backend.chat_stream(self.selected_model, messages, options):
                    if chunk["type"] == "content":
                        full_response += chunk["text"]
                        self.current_ai_response = full_response
                        # Update the temporary entry in chat history with the new content
                        if temp_history_index < len(self.chat_history):
                            self.chat_history[temp_history_index] = (user_msg, self.current_ai_response)

                        yield  # Update UI in real-time
                    elif chunk["type"] == "done":
                        metrics = chunk["metrics"]

                await backend.close()

                # Log metrics if available
                if metrics:
                    tokens_per_sec = metrics.get("tokens_per_second", 0)
                    inference_time = metrics.get("inference_time", 0)
                    tokens_generated = metrics.get("tokens_generated", 0)
                    self.add_debug(
                        f"✅ Generation complete: {tokens_generated} tokens, "
                        f"{inference_time:.1f}s, {tokens_per_sec:.1f} tok/s"
                    )

                # Separator nach Generation/Cache-Metadata
                console_separator()  # Schreibt in Log-File
                self.add_debug("────────────────────")  # Zeigt in Debug-Console
                yield

                # The response is already in the history from streaming, no need to update

            # ============================================================
            # POST-RESPONSE: History Summarization Check (im Hintergrund)
            # ============================================================
            # Kompression läuft NACH der Antwort, während User liest
            # Eingabefelder werden während Kompression disabled
            self.add_debug(f"🔍 Checking compression: {len(self.chat_history)} messages, min required: {2}")
            yield

            try:
                from .lib.context_manager import summarize_history_if_needed
                from .lib.config import HISTORY_MIN_MESSAGES_BEFORE_COMPRESSION
                from .backends import BackendFactory

                # Nur prüfen wenn History signifikant ist
                if len(self.chat_history) >= HISTORY_MIN_MESSAGES_BEFORE_COMPRESSION:
                    self.add_debug(f"✅ Compression check passed: {len(self.chat_history)} >= {HISTORY_MIN_MESSAGES_BEFORE_COMPRESSION}")
                    yield
                    # Backend für Summarization
                    temp_backend = BackendFactory.create(
                        self.backend_type,
                        base_url=self.backend_url
                    )

                    # Context-Limit des aktuellen Models
                    try:
                        context_limit = await temp_backend.get_model_context_limit(self.selected_model)
                    except:
                        context_limit = 8192  # Fallback

                    # Setze Kompression-Flag (disabled Input-Felder)
                    self.is_compressing = True
                    self.add_debug("🔍 Prüfe ob History-Kompression nötig ist...")
                    yield

                    # Summarization check (yields events wenn nötig)
                    compressed = False
                    async for event in summarize_history_if_needed(
                        history=self.chat_history,
                        llm_client=temp_backend,
                        model_name=self.automatik_model,  # Schnelles Model
                        context_limit=context_limit
                    ):
                        compressed = True
                        if event["type"] == "history_update":
                            self.chat_history = event["data"]
                            self.add_debug(f"✅ History komprimiert: {len(self.chat_history)} Messages")
                            yield
                        elif event["type"] == "debug":
                            self.add_debug(event["message"])
                            yield
                        elif event["type"] == "progress":
                            self.set_progress(phase="compress")
                            yield

                    await temp_backend.close()

                    # Progress clearen falls gesetzt
                    if self.progress_phase == "compress":
                        self.clear_progress()
                        yield

                    # Kompression fertig - Input-Felder wieder enablen
                    self.is_compressing = False
                    if compressed:
                        self.add_debug("✅ History-Kompression abgeschlossen - Eingabe wieder möglich")
                    else:
                        self.add_debug("ℹ️ Keine Kompression nötig")
                    yield
                else:
                    self.add_debug(f"❌ History zu kurz: {len(self.chat_history)} < {HISTORY_MIN_MESSAGES_BEFORE_COMPRESSION}")
                    yield

            except Exception as e:
                # Nicht kritisch - einfach weitermachen
                import traceback
                self.add_debug(f"⚠️ History compression check failed: {e}")
                self.add_debug(f"Traceback: {traceback.format_exc()}")
                self.is_compressing = False
                yield

            # Separator nach Compression-Check
            console_separator()  # Schreibt in Log-File
            self.add_debug("────────────────────")  # Zeigt in Debug-Console
            yield

            # Clear response display
            self.current_ai_response = ""
            yield  # Final update to clear AI response window

            # Debug-Zeile entfernt - User wollte das nicht sehen
            # self.add_debug(f"✅ Response complete ({len(full_response)} chars)")

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            self.current_ai_response = error_msg
            # Update the temporary entry in chat history with the error
            if temp_history_index < len(self.chat_history):
                self.chat_history[temp_history_index] = (user_msg, error_msg)
            self.add_debug(f"❌ Generation failed: {e}")
            import traceback
            self.add_debug(f"Traceback: {traceback.format_exc()}")

        finally:
            self.is_generating = False
            # Final debug sync


    def clear_chat(self):
        """Clear chat history"""
        self.chat_history = []
        self.current_ai_response = ""
        self.current_user_message = ""
        self.debug_messages = []  # Debug Console auch leeren!
        self.add_debug("🗑️ Chat cleared")


    def toggle_auto_refresh(self):
        """Toggle auto-scroll for all areas (Debug Console, Chat History, AI Response)"""
        self.auto_refresh_enabled = not self.auto_refresh_enabled

    def restart_ollama(self):
        """Restart Ollama service"""
        import subprocess
        try:
            self.add_debug("🔄 Restarting Ollama service...")
            subprocess.run(["systemctl", "restart", "ollama"], check=True)  # Ohne sudo - Polkit regelt das
            self.add_debug("✅ Ollama restarted successfully")

        except Exception as e:
            self.add_debug(f"❌ Ollama restart failed: {e}")


    def restart_aifred(self):
        """Restart AIfred - choose between production service restart or development hot-reload"""

        # Import configuration from central config file
        from .lib.config import USE_SYSTEMD_RESTART

        if USE_SYSTEMD_RESTART:
            # PRODUCTION: Restart via systemd service
            self._restart_aifred_systemd()
        else:
            # DEVELOPMENT: Soft restart for hot-reload
            self._soft_restart()

    def _restart_aifred_systemd(self):
        """Production: Restart AIfred service via systemctl"""
        import subprocess
        try:
            self.add_debug("🔄 Restarting AIfred service...")
            # Restart the actual systemd service (Polkit allows this without sudo)
            subprocess.run(["systemctl", "restart", "aifred-intelligence"], check=True)
            self.add_debug("✅ AIfred service restart initiated")

            # Note: The service restart will reload the entire application,
            # so clearing state here is not necessary - the app will reinitialize

        except Exception as e:
            self.add_debug(f"❌ AIfred service restart failed: {e}")
            # Fallback to soft restart if systemctl fails
            self.add_debug("⚠️ Falling back to soft restart...")
            self._soft_restart()

    def _soft_restart(self):
        """Development: Soft restart - clear all caches and histories without restarting service"""
        # Clear lib console FIRST (before adding new message!)
        clear_console()

        self.chat_history = []
        self.current_user_input = ""
        self.current_user_message = ""
        self.current_ai_response = ""
        self.debug_messages = []
        self.is_generating = False

        # Note: Vector Cache is persistent (ChromaDB) - not cleared on soft restart
        # To clear Vector Cache, delete ./aifred_vector_cache directory manually

        # Reinitialize debug log
        from .lib import initialize_debug_log
        initialize_debug_log(force_reset=True)

        # Add restart message AFTER clearing
        self.add_debug("🔄 AIfred soft restart - histories cleared (Hot-Reload Mode)")


    def set_selected_model(self, model: str):
        """Set selected model"""
        self.selected_model = model
        self.add_debug(f"📝 Model changed to: {model}")

    def set_temperature(self, temp: list[float]):
        """Set temperature (from slider which returns list[float])"""
        self.temperature = temp[0] if isinstance(temp, list) else temp

    def set_research_mode(self, mode: str):
        """Set research mode"""
        self.research_mode = mode
        self.add_debug(f"🔍 Research mode: {mode}")

    def set_research_mode_display(self, display_value: str):
        """Set research mode from UI display value"""
        from .lib import TranslationManager
        
        # Use translation manager to get the internal mode value
        self.research_mode_display = display_value
        self.research_mode = TranslationManager.get_research_mode_value(display_value)
        self.add_debug(f"🔍 Research mode: {self.research_mode}")

    def set_automatik_model(self, model: str):
        """Set automatik model for decision and query optimization"""
        self.automatik_model = model
        self.add_debug(f"⚡ Automatik model: {model}")

        # Preload new model in background (via curl)
        import subprocess
        preload_cmd = f'curl -s http://localhost:11434/api/chat -d \'{{"model":"{model}","messages":[{{"role":"user","content":"hi"}}],"stream":false,"options":{{"num_predict":1}}}}\' > /dev/null 2>&1 &'
        try:
            subprocess.Popen(preload_cmd, shell=True)
            log_message(f"🚀 Preloading new Automatik-LLM: {model}")
            self.add_debug(f"🚀 Preloading {model}...")
        except Exception as e:
            log_message(f"⚠️ Preload failed: {e}")

        # Note: Context limit will be queried on first use (fast ~30ms) and cached by httpx

    def toggle_tts(self):
        """Toggle TTS on/off"""
        self.enable_tts = not self.enable_tts
        self.add_debug(f"🔊 TTS: {'enabled' if self.enable_tts else 'disabled'}")

    def set_ui_language(self, lang: str):
        """Set UI language"""
        if lang in ["de", "en"]:
            self.ui_language = lang
            self.add_debug(f"🌐 UI Language changed to: {lang}")
        else:
            self.add_debug(f"❌ Invalid language: {lang}. Use 'de' or 'en'")

    def get_text(self, key: str):
        """Get translated text based on current UI language"""
        from .lib import TranslationManager
        return TranslationManager.get_text(key, self.ui_language)
