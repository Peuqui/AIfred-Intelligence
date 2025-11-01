"""
Reflex State Management for AIfred Intelligence

Main state for chat, settings, and backend management
"""

import reflex as rx
from typing import List, Tuple, Dict
import threading
import uuid
from pydantic import BaseModel
from .backends import BackendFactory, LLMMessage, LLMOptions
from .lib import (
    initialize_debug_log,
    log_message,
    clear_console,
    set_research_cache,
    perform_agent_research
)
from .lib.formatting import format_debug_message

# ============================================================
# Module-Level Cache (außerhalb State, da Lock nicht pickle-bar)
# ============================================================
_research_cache: Dict = {}
_cache_lock = threading.Lock()


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

    # Processing Progress (Automatik, Scraping, LLM)
    progress_active: bool = False
    progress_phase: str = ""  # "automatik", "scraping", "llm"
    progress_current: int = 0
    progress_total: int = 0
    progress_failed: int = 0  # Anzahl fehlgeschlagener URLs

    async def on_load(self):
        """Called when page loads - initialize backend"""
        # Initialize debug log (reset on first load, append afterwards)
        initialize_debug_log(force_reset=False)

        # Initialize research cache FIRST (module-level, nicht in State)
        # WICHTIG: Muss IMMER gesetzt werden, auch bei Hot-Reload!
        set_research_cache(_research_cache, _cache_lock)

        # Generate session ID
        if not self.session_id:
            self.session_id = str(uuid.uuid4())
            self.add_debug(f"🆔 Session ID: {self.session_id[:8]}...")
        else:
            # Session ID already exists (e.g., hot reload)
            self.add_debug(f"🔄 Existing Session: {self.session_id[:8]}... (cache re-initialized)")

        # Initialize backend
        await self.initialize_backend()

    async def initialize_backend(self):
        """Initialize LLM backend"""
        try:
            # Update URL based on backend type
            if self.backend_type == "ollama":
                self.backend_url = "http://localhost:11434"
            elif self.backend_type == "vllm":
                # CHANGE THIS to Aragon's IP when vLLM is running there
                self.backend_url = "http://localhost:8000/v1"

            # Create backend
            backend = BackendFactory.create(
                self.backend_type,
                base_url=self.backend_url
            )

            # Health check (quick!)
            self.backend_healthy = await backend.health_check()

            if self.backend_healthy:
                # Set basic info immediately (UI shows up faster!)
                self.backend_info = f"{self.backend_type} backend ready"
                self.add_debug(f"✅ {self.backend_type} backend ready")

                # Load models in background (don't block UI!)
                import asyncio
                async def load_models_in_background():
                    from aifred.backends import BackendFactory
                    bg_backend = BackendFactory.create(self.backend_type, self.backend_url)
                    try:
                        self.available_models = await bg_backend.list_models()
                        if not self.selected_model and self.available_models:
                            self.selected_model = self.available_models[0]
                        self.backend_info = f"{self.backend_type} - {len(self.available_models)} models available"
                        self.add_debug(f"✅ {len(self.available_models)} Models geladen")
                    finally:
                        await bg_backend.close()

                asyncio.create_task(load_models_in_background())

                # ============================================================
                # PERFORMANCE-OPTIMIERUNG: Automatik-LLM beim Start vorladen (NON-BLOCKING!)
                # ============================================================
                # Lade Automatik-LLM in VRAM im Hintergrund (blockiert NICHT den Startup!)
                if self.automatik_model:
                    import asyncio
                    self.add_debug(f"🚀 Lade Automatik-LLM ({self.automatik_model}) im Hintergrund vor...")

                    # Fire-and-forget: Blockiert NICHT den Startup!
                    # WICHTIG: Erstelle neues Backend-Objekt, da das aktuelle gleich geschlossen wird!
                    async def preload_in_background():
                        from aifred.backends import BackendFactory
                        preload_backend = BackendFactory.create(self.backend_type, self.backend_url)
                        try:
                            preload_success = await preload_backend.preload_model(self.automatik_model)
                            if preload_success:
                                self.add_debug(f"✅ Automatik-LLM ({self.automatik_model}) vorgeladen")
                            else:
                                self.add_debug("⚠️ Automatik-LLM Preload fehlgeschlagen")
                        finally:
                            await preload_backend.close()

                    asyncio.create_task(preload_in_background())
            else:
                self.backend_info = f"{self.backend_type} not reachable"
                self.add_debug(f"❌ {self.backend_type} backend not reachable at {self.backend_url}")

            await backend.close()

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
                self.add_debug("🤖 Automatik Mode: KI entscheidet über Recherche...")

                # Import chat_interactive_mode
                from .lib.conversation_handler import chat_interactive_mode

                # REAL STREAMING: Call async generator directly
                async for item in chat_interactive_mode(
                    user_text=user_msg,
                    stt_time=0.0,
                    model_choice=self.selected_model,
                    automatik_model=self.automatik_model,
                    history=self.chat_history,
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
                    elif item["type"] == "result":
                        result_data = item["data"]
                        # Extract and update history IMMEDIATELY
                        ai_text, updated_history, inference_time = result_data
                        self.chat_history = updated_history
                        yield  # Update UI to show new history entry first
                        # Clear AI response and user message windows IMMEDIATELY
                        self.current_ai_response = ""
                        self.current_user_message = ""
                        self.is_generating = False  # Stop spinner, switch UI to history display
                        yield  # Force immediate UI update to clear both windows
                        # Give UI time to render the cleared state before continuing
                        import asyncio
                        await asyncio.sleep(0.1)  # 100ms delay for UI to update
                        yield  # Final yield after delay
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

            elif self.research_mode in ["quick", "deep"]:
                # Direct research mode (quick/deep)
                self.add_debug(f"🔍 Research Mode: {self.research_mode}")

                # REAL STREAMING: Call async generator directly
                async for item in perform_agent_research(
                    user_text=user_msg,
                    stt_time=0.0,  # Kein STT in Reflex (noch)
                    mode=self.research_mode,
                    model_choice=self.selected_model,
                    automatik_model=self.automatik_model,
                    history=self.chat_history,
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
                    elif item["type"] == "result":
                        result_data = item["data"]
                        # Extract and update history IMMEDIATELY
                        ai_text, updated_history, inference_time = result_data
                        self.chat_history = updated_history
                        yield  # Update UI to show new history entry first
                        # Clear AI response and user message windows IMMEDIATELY
                        self.current_ai_response = ""
                        self.current_user_message = ""
                        self.is_generating = False  # Stop spinner, switch UI to history display
                        yield  # Force immediate UI update to clear both windows
                        # Give UI time to render the cleared state before continuing
                        import asyncio
                        await asyncio.sleep(0.1)  # 100ms delay for UI to update
                        yield  # Final yield after delay

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

                # Build messages
                messages = []

                # Add chat history
                for user_turn, ai_turn in self.chat_history:
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



                # Add to history (only for non-research mode)
                self.chat_history.append((user_msg, full_response))

            # Clear response display
            self.current_ai_response = ""
            yield  # Final update to clear AI response window

            # Debug-Zeile entfernt - User wollte das nicht sehen
            # self.add_debug(f"✅ Response complete ({len(full_response)} chars)")

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            self.current_ai_response = error_msg
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
        """Restart AIfred - clear all caches and histories"""
        # Clear lib console FIRST (before adding new message!)
        clear_console()

        self.chat_history = []
        self.current_user_input = ""
        self.current_user_message = ""
        self.current_ai_response = ""
        self.debug_messages = []
        self.is_generating = False

        # Clear research cache
        global _research_cache
        with _cache_lock:
            _research_cache.clear()

        # Reinitialize debug log
        from .lib import initialize_debug_log
        initialize_debug_log(force_reset=True)

        # Add restart message AFTER clearing
        self.add_debug("🔄 AIfred restarted - all caches and histories cleared")


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
        # Map display string to internal mode
        mode_map = {
            "🧠 Eigenes Wissen (schnell)": "none",
            "⚡ Web-Suche Schnell (3 beste)": "quick",
            "🔍 Web-Suche Ausführlich (7 beste)": "deep",
            "🤖 Automatik (KI entscheidet)": "automatik"
        }
        self.research_mode_display = display_value
        self.research_mode = mode_map.get(display_value, "automatik")
        self.add_debug(f"🔍 Research mode: {self.research_mode}")

    def set_automatik_model(self, model: str):
        """Set automatik model for decision and query optimization"""
        self.automatik_model = model
        self.add_debug(f"⚡ Automatik model: {model}")

    def toggle_tts(self):
        """Toggle TTS on/off"""
        self.enable_tts = not self.enable_tts
        self.add_debug(f"🔊 TTS: {'enabled' if self.enable_tts else 'disabled'}")
