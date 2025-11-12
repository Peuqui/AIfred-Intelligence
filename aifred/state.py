"""
Reflex State Management for AIfred Intelligence

Main state for chat, settings, and backend management
"""

import reflex as rx
from typing import List, Tuple, Optional
import uuid
import os
from pydantic import BaseModel
from .lib import (
    initialize_debug_log,
    log_message,
    console_separator,
    clear_console,
    # set_research_cache removed - cache system deprecated
    perform_agent_research,
    set_language
)
from .lib.formatting import format_debug_message
from .lib import config
from .lib.vllm_manager import vLLMProcessManager

# ============================================================
# Module-Level Vector Cache (ChromaDB Server Mode)
# ============================================================
# NEW: Using ChromaDB server mode via Docker - thread-safe by design
from .lib.vector_cache import get_cache

def initialize_vector_cache():
    """
    Initialize Vector Cache (Server Mode)

    Connects to ChromaDB Docker container via HTTP.
    Thread-safe by design - no worker threads needed.

    Returns immediately after testing connection.
    """
    try:
        log_message(f"🚀 Vector Cache: Connecting to ChromaDB server (PID: {os.getpid()})")
        cache = get_cache()
        log_message("✅ Vector Cache: Connected successfully")
        return cache
    except Exception as e:
        log_message(f"⚠️ Vector Cache connection failed: {e}")
        log_message("💡 Make sure ChromaDB is running: docker-compose up -d chromadb")
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
    backend_type: str = "ollama"  # "ollama", "vllm", "tabbyapi"
    backend_url: str = "http://localhost:11434"  # Default Ollama URL
    selected_model: str = config.BACKEND_DEFAULT_MODELS["ollama"]["selected_model"]
    available_models: List[str] = []

    # Automatik-LLM (für Decision und Query-Optimierung)
    automatik_model: str = config.BACKEND_DEFAULT_MODELS["ollama"]["automatik_model"]

    # LLM Options
    temperature: float = 0.2
    num_ctx: int = 32768

    # Cached Model Metadata (to avoid repeated API calls)
    _automatik_model_context_limit: int = 0  # Cached context limit for automatik model

    # Research Settings
    research_mode: str = "automatik"  # "quick", "deep", "automatik", "none"
    research_mode_display: str = "🤖 Automatik (KI entscheidet)"  # UI display value

    # Qwen3 Thinking Mode (Chain-of-Thought Reasoning)
    enable_thinking: bool = True  # True = Thinking Mode (temp=0.6), False = Non-Thinking (temp=0.7)

    # TTS Settings
    enable_tts: bool = False

    # Session Management
    session_id: str = ""

    # Backend Status
    backend_healthy: bool = False
    backend_info: str = ""
    backend_switching: bool = False  # True während Backend-Wechsel (UI wird disabled)

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

    # vLLM Process Manager (non-serializable, managed separately)
    _vllm_manager: Optional[vLLMProcessManager] = None

    # GPU Detection (for backend compatibility warnings)
    gpu_detected: bool = False
    gpu_name: str = ""
    gpu_compute_cap: float = 0.0
    gpu_warnings: List[str] = []

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

            # Load saved settings
            from .lib.settings import load_settings
            saved_settings = load_settings()

            if saved_settings:
                # Use saved settings
                self.backend_type = saved_settings.get("backend_type", self.backend_type)
                self.research_mode = saved_settings.get("research_mode", self.research_mode)
                self.temperature = saved_settings.get("temperature", self.temperature)
                self.enable_thinking = saved_settings.get("enable_thinking", self.enable_thinking)

                # NEW: Load per-backend models (if available)
                backend_models = saved_settings.get("backend_models", {})
                if self.backend_type in backend_models:
                    # Use per-backend saved models
                    self.selected_model = backend_models[self.backend_type].get("selected_model", self.selected_model)
                    self.automatik_model = backend_models[self.backend_type].get("automatik_model", self.automatik_model)
                else:
                    # Fallback: Use old-style global model settings (backward compatibility)
                    self.selected_model = saved_settings.get("selected_model", self.selected_model)
                    self.automatik_model = saved_settings.get("automatik_model", self.automatik_model)

                self.add_debug(f"⚙️ Settings loaded from file (backend: {self.backend_type})")
            else:
                # Use config.py defaults
                self.add_debug("⚙️ Using default settings from config.py")

            # Initialize language settings
            from .lib.config import DEFAULT_LANGUAGE
            set_language(DEFAULT_LANGUAGE)
            self.add_debug(f"🌍 Language mode: {DEFAULT_LANGUAGE}")

            # Initialize Vector Cache
            initialize_vector_cache()
            self.add_debug("💾 Vector Cache: Connected")

            # Generate session ID
            if not self.session_id:
                self.session_id = str(uuid.uuid4())
                self.add_debug(f"🆔 Session ID: {self.session_id[:8]}...")

            # GPU Detection
            self.add_debug("🔍 Detecting GPU capabilities...")
            try:
                from .lib.gpu_detection import detect_gpu
                gpu_info = detect_gpu()
                if gpu_info:
                    self.gpu_detected = True
                    self.gpu_name = gpu_info.name
                    self.gpu_compute_cap = gpu_info.compute_capability
                    self.gpu_warnings = gpu_info.warnings
                    self.add_debug(f"✅ GPU: {gpu_info.name} (Compute {gpu_info.compute_capability})")

                    # Log warnings
                    if gpu_info.unsupported_backends:
                        self.add_debug(f"⚠️ Incompatible backends: {', '.join(gpu_info.unsupported_backends)}")
                    if gpu_info.warnings:
                        for warning in gpu_info.warnings[:2]:  # Show first 2 warnings
                            self.add_debug(f"⚠️ {warning}")
                else:
                    self.add_debug("ℹ️ No GPU detected or nvidia-smi not available")
            except Exception as e:
                self.add_debug(f"⚠️ GPU detection failed: {e}")
                log_message(f"⚠️ GPU detection failed: {e}")

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
                # Use port 8001 for development (8000 will be used on production MiniPC)
                self.backend_url = "http://localhost:8001/v1"
            elif self.backend_type == "tabbyapi":
                self.backend_url = "http://localhost:5000/v1"

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
                # For vLLM/TabbyAPI: Get models from HuggingFace cache (local files)
                # For Ollama: Get models from server API
                if self.backend_type in ["vllm", "tabbyapi"]:
                    # Scan HuggingFace cache for downloaded models
                    from pathlib import Path
                    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"

                    if hf_cache.exists():
                        # Find all model directories (format: models--Org--ModelName)
                        model_dirs = [d for d in hf_cache.iterdir() if d.is_dir() and d.name.startswith("models--")]
                        # Convert directory names to model IDs (e.g., models--Qwen--Qwen3-8B-AWQ -> Qwen/Qwen3-8B-AWQ)
                        self.available_models = [
                            d.name.replace("models--", "").replace("--", "/", 1)
                            for d in model_dirs
                        ]
                        self.add_debug(f"📂 Found {len(self.available_models)} models in HuggingFace cache")
                    else:
                        self.available_models = []
                        self.add_debug("⚠️ HuggingFace cache not found")

                else:
                    # Ollama: Query server API
                    endpoint = f'{self.backend_url}/api/tags'

                    # Synchronous curl call to get model list
                    result = subprocess.run(
                        ['curl', '-s', endpoint],
                        capture_output=True,
                        text=True,
                        timeout=5.0
                    )

                    if result.returncode == 0:
                        data = json.loads(result.stdout)
                        self.available_models = [m["name"] for m in data.get("models", [])]
                    else:
                        self.available_models = []

                # Common validation for all backends
                # Validate that configured models exist, fallback to first available if not
                if self.selected_model not in self.available_models and self.available_models:
                    log_message(f"⚠️ Configured model '{self.selected_model}' not found, using '{self.available_models[0]}'")
                    self.selected_model = self.available_models[0]

                if self.automatik_model not in self.available_models and self.available_models:
                    log_message(f"⚠️ Configured automatik model '{self.automatik_model}' not found, using '{self.available_models[0]}'")
                    self.automatik_model = self.available_models[0]

                self.backend_info = f"{self.backend_type} - {len(self.available_models)} models"
                self.backend_healthy = True
                self.add_debug(f"✅ {len(self.available_models)} Models vorhanden (Main: {self.selected_model}, Automatik: {self.automatik_model})")

            except Exception as e:
                self.backend_healthy = False
                self.backend_info = f"{self.backend_type} error"
                self.add_debug(f"❌ Model loading failed: {e}")
                log_message(f"❌ Model loading failed: {e}")

            # Start vLLM process if backend is vLLM
            if self.backend_type == "vllm":
                await self._start_vllm_server()

            # Preload Automatik-LLM via curl in background (simple & non-blocking!)
            # Note: For vLLM, skip preload curl since server was just started with the model
            if self.automatik_model and self.backend_type != "vllm":
                import subprocess
                try:
                    if self.backend_type == "ollama":
                        # Ollama-specific preload
                        preload_cmd = f'curl -s http://localhost:11434/api/chat -d \'{{"model":"{self.automatik_model}","messages":[{{"role":"user","content":"hi"}}],"stream":false,"options":{{"num_predict":1}}}}\' > /dev/null 2>&1 &'
                        subprocess.Popen(preload_cmd, shell=True)
                        log_message(f"🚀 Preloading {self.automatik_model} via curl (background)")
                        self.add_debug(f"🚀 Preloading {self.automatik_model}...")
                    elif self.backend_type == "tabbyapi":
                        # OpenAI-compatible preload (TabbyAPI)
                        preload_cmd = f'curl -s {self.backend_url}/chat/completions -H "Content-Type: application/json" -d \'{{"model":"{self.automatik_model}","messages":[{{"role":"user","content":"hi"}}],"max_tokens":1}}\' > /dev/null 2>&1 &'
                        subprocess.Popen(preload_cmd, shell=True)
                        log_message(f"🚀 Preloading {self.automatik_model} via {self.backend_type} (background)")
                        self.add_debug(f"🚀 Preloading {self.automatik_model}...")
                except Exception as e:
                    log_message(f"⚠️ Preload failed: {e}")
                    # Not critical, continue anyway

        except Exception as e:
            self.backend_healthy = False
            self.backend_info = f"Error: {str(e)}"
            self.add_debug(f"❌ Backend initialization failed: {e}")

    def _save_settings(self):
        """Save current settings to file (per-backend models)"""
        from .lib.settings import save_settings, load_settings

        # Load existing settings to preserve other backends
        existing = load_settings() or {}
        backend_models = existing.get("backend_models", {})

        # Update current backend's models
        backend_models[self.backend_type] = {
            "selected_model": self.selected_model,
            "automatik_model": self.automatik_model,
        }

        settings = {
            "backend_type": self.backend_type,
            "research_mode": self.research_mode,
            "temperature": self.temperature,
            "enable_thinking": self.enable_thinking,
            "backend_models": backend_models,  # Merged: preserves all backends
        }
        save_settings(settings)

    async def switch_backend(self, new_backend: str):
        """Switch to different backend and restore last used models"""
        # Prevent concurrent backend switches
        if self.backend_switching:
            self.add_debug("⚠️ Backend switch already in progress, please wait...")
            return

        self.backend_switching = True
        yield  # Update UI to disable controls

        try:
            self.add_debug(f"🔄 Switching backend from {self.backend_type} to {new_backend}...")

            # Save current backend's models before switching
            self._save_settings()

            # Clean up old backend resources (unload models, stop servers)
            old_backend = self.backend_type
            await self._cleanup_old_backend(old_backend)

            # Load saved settings for target backend BEFORE switching
            from .lib.settings import load_settings
            settings = load_settings() or {}
            backend_models = settings.get("backend_models", {})

            # Determine which models to use for new backend
            target_main_model = None
            target_auto_model = None

            if new_backend in backend_models:
                # Use saved models from backend_models.json
                saved_models = backend_models[new_backend]
                target_main_model = saved_models.get("selected_model")
                target_auto_model = saved_models.get("automatik_model")
                self.add_debug(f"📝 Found saved models for {new_backend}: Main={target_main_model}, Auto={target_auto_model}")
            else:
                # Use backend-specific defaults from config.py
                default_models = config.BACKEND_DEFAULT_MODELS.get(new_backend, {})
                target_main_model = default_models.get("selected_model")
                target_auto_model = default_models.get("automatik_model")
                self.add_debug(f"📝 Using default models for {new_backend}: Main={target_main_model}, Auto={target_auto_model}")

            # Set target models BEFORE initialize_backend() so validation doesn't override them
            if target_main_model:
                self.selected_model = target_main_model
            if target_auto_model:
                self.automatik_model = target_auto_model

            # Switch backend and load models
            self.backend_type = new_backend
            await self.initialize_backend()

            # vLLM and TabbyAPI can only load ONE model at a time
            if new_backend in ["vllm", "tabbyapi"] and self.automatik_model != self.selected_model:
                self.add_debug(f"⚠️ {new_backend} can only load one model - using {self.selected_model} for both Main and Automatik")
                self.automatik_model = self.selected_model

            # Save settings for new backend
            self._save_settings()

        finally:
            # Re-enable UI controls
            self.backend_switching = False
            yield  # Force UI update to re-enable controls and refresh model dropdowns

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

        # Keep only last 500 messages
        if len(self.debug_messages) > 500:
            self.debug_messages = self.debug_messages[-500:]

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

    async def _start_vllm_server(self):
        """Start vLLM server process with selected model"""
        try:
            self.add_debug(f"🚀 Starting vLLM server with {self.selected_model}...")

            # Initialize vLLM Process Manager
            self._vllm_manager = vLLMProcessManager(
                port=8001,
                max_model_len=16384,
                gpu_memory_utilization=0.85
            )

            # Start server with selected model (timeout 120s - first load can take ~70s)
            await self._vllm_manager.start(self.selected_model, timeout=120)

            self.add_debug("✅ vLLM server ready on port 8001")

        except Exception as e:
            self.add_debug(f"❌ Failed to start vLLM: {e}")
            self._vllm_manager = None
            raise

    async def _stop_vllm_server(self):
        """Stop vLLM server process gracefully"""
        if self._vllm_manager and self._vllm_manager.is_running():
            self.add_debug("🛑 Stopping vLLM server...")
            await self._vllm_manager.stop()
            self._vllm_manager = None
            self.add_debug("✅ vLLM server stopped")

    async def _cleanup_old_backend(self, old_backend: str):
        """
        Clean up resources from previous backend before switching

        Args:
            old_backend: Backend type to clean up ("ollama", "vllm", etc.)
        """
        if old_backend == "ollama":
            # Unload all Ollama models from VRAM
            self.add_debug("🧹 Unloading Ollama models from VRAM...")
            try:
                # Create Ollama backend instance to call unload_all_models
                from .lib.llm_client import LLMClient
                llm_client = LLMClient(backend_type="ollama", base_url="http://localhost:11434")
                backend = llm_client.backend

                if hasattr(backend, 'unload_all_models'):
                    count = await backend.unload_all_models()
                    if count > 0:
                        self.add_debug(f"✅ Unloaded {count} Ollama model(s)")
                    else:
                        self.add_debug("ℹ️ No Ollama models were loaded")
            except Exception as e:
                self.add_debug(f"⚠️ Error unloading Ollama models: {e}")

        elif old_backend == "vllm":
            # Stop vLLM server to free VRAM
            await self._stop_vllm_server()

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
            result_data = None

            if self.research_mode == "automatik":
                # Automatik mode: AI decides if research is needed
                # Debug message is already logged in conversation_handler.py

                # Import chat_interactive_mode
                from .lib.conversation_handler import chat_interactive_mode

                # Initialize temporary history entry for real-time display
                temp_history_index = len(self.chat_history)
                self.chat_history.append((user_msg, self.current_ai_response))
                
                # Build LLM options (include enable_thinking toggle)
                llm_options = {
                    'enable_thinking': self.enable_thinking
                }

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
                    llm_options=llm_options,
                    backend_type=self.backend_type,
                    backend_url=self.backend_url
                ):
                    # Route messages based on type
                    if item["type"] == "debug":
                        self.debug_messages.append(format_debug_message(item["message"]))
                        if len(self.debug_messages) > 500:
                            self.debug_messages = self.debug_messages[-500:]
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
                    llm_options=None,
                    backend_type=self.backend_type,
                    backend_url=self.backend_url
                ):
                    # Route messages based on type
                    if item["type"] == "debug":
                        self.debug_messages.append(format_debug_message(item["message"]))
                        # Limit debug messages
                        if len(self.debug_messages) > 500:
                            self.debug_messages = self.debug_messages[-500:]
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
                    except Exception:
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

    def restart_backend(self):
        """Restart current LLM backend service"""
        import subprocess
        try:
            backend_name = self.backend_type.upper()
            self.add_debug(f"🔄 Restarting {backend_name} service...")

            if self.backend_type == "ollama":
                subprocess.run(["systemctl", "restart", "ollama"], check=True)
            elif self.backend_type == "vllm":
                # vLLM läuft nicht als systemd service, nur Info-Message
                self.add_debug("ℹ️ vLLM läuft nicht als Service - bitte manuell neu starten")
                return
            elif self.backend_type == "tabbyapi":
                self.add_debug("ℹ️ TabbyAPI läuft nicht als Service - bitte manuell neu starten")
                return

            self.add_debug(f"✅ {backend_name} restarted successfully")

        except Exception as e:
            self.add_debug(f"❌ {backend_name} restart failed: {e}")

    def restart_ollama(self):
        """Legacy method - calls restart_backend()"""
        self.restart_backend()


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
        self._save_settings()

    def toggle_thinking_mode(self):
        """Toggle Qwen3 Thinking Mode"""
        self.enable_thinking = not self.enable_thinking
        mode_name = "Thinking Mode" if self.enable_thinking else "Non-Thinking Mode"
        temp = "0.6" if self.enable_thinking else "0.7"
        self.add_debug(f"🧠 {mode_name} aktiviert (temp={temp})")
        self._save_settings()

    def set_temperature(self, temp: list[float]):
        """Set temperature (from slider which returns list[float])"""
        self.temperature = temp[0] if isinstance(temp, list) else temp
        self._save_settings()

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
        self._save_settings()

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
