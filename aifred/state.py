"""
Reflex State Management for AIfred Intelligence

Main state for chat, settings, and backend management
"""

import reflex as rx
from typing import List, Tuple, Optional, Any
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


# ============================================================
# Module-Level Backend State (Global across all sessions)
# ============================================================
# Prevents re-initialization on page reload
# Backend is initialized once at server startup
_global_backend_initialized = False
_global_backend_state: dict[str, Any] = {
    "backend_type": None,
    "backend_url": None,
    "selected_model": None,
    "automatik_model": None,
    "available_models": [],
    "gpu_info": None,
    "vllm_manager": None,  # Global vLLM process manager (persists across reloads)
}


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
    # NOTE: Models loaded from settings.json first, fallback to config.py only if settings don't exist
    selected_model: str = ""  # Initialized in on_load() from settings.json or config.py
    available_models: List[str] = []

    # Automatik-LLM (für Decision und Query-Optimierung)
    # NOTE: Loaded from settings.json first, fallback to config.py only if settings don't exist
    automatik_model: str = ""  # Initialized in on_load() from settings.json or config.py

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
    thinking_mode_warning: str = ""  # Empty = no warning, otherwise show model name that doesn't support thinking

    # vLLM YaRN Settings (RoPE Scaling for Context Extension)
    enable_yarn: bool = False  # Enable YaRN context extension
    yarn_factor: float = 1.0  # Scaling factor (1.0 = disabled, 2.0 = 2x context, 4.0 = 4x context)
    vllm_max_tokens: int = 0  # vLLM max_model_len (0 = auto-detect on first run, then saved)
    vllm_native_context: int = 0  # Native model context (0 = auto-detect from config.json)

    # TTS Settings
    enable_tts: bool = False

    # Session Management
    session_id: str = ""

    # Backend Status
    backend_healthy: bool = False
    backend_info: str = ""
    backend_switching: bool = False  # True während Backend-Wechsel (UI wird disabled)
    backend_initializing: bool = True  # True während erster Initialisierung (zeigt Loading Spinner)

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
    available_backends: List[str] = ["ollama", "vllm", "tabbyapi"]  # Filtered by GPU compatibility

    async def on_load(self):
        """
        Called when page loads - initialize backend and load models

        NEW: Backend is initialized once globally at server startup.
        Page reloads simply restore state from global variables.
        """
        global _global_backend_initialized, _global_backend_state

        print(f"🔥 on_load() CALLED - Global init: {_global_backend_initialized}, Session init: {self._backend_initialized}")

        # FIRST-TIME GLOBAL INITIALIZATION (once per server start)
        if not _global_backend_initialized:
            print("=" * 60)
            print("🚀 FIRST-TIME SERVER INITIALIZATION...")
            print("=" * 60)

            # Initialize debug log (only once)
            initialize_debug_log(force_reset=False)

            # Initialize language settings
            from .lib.config import DEFAULT_LANGUAGE
            set_language(DEFAULT_LANGUAGE)
            log_message(f"🌍 Language mode: {DEFAULT_LANGUAGE}")

            # Initialize Vector Cache
            initialize_vector_cache()
            log_message("💾 Vector Cache: Connected")

            # GPU Detection (once per server)
            log_message("🔍 Detecting GPU capabilities...")
            try:
                from .lib.gpu_detection import detect_gpu
                gpu_info = detect_gpu()
                if gpu_info:
                    _global_backend_state["gpu_info"] = gpu_info
                    log_message(f"✅ GPU: {gpu_info.name} (Compute {gpu_info.compute_capability})")
                    if gpu_info.unsupported_backends:
                        log_message(f"⚠️ Incompatible backends: {', '.join(gpu_info.unsupported_backends)}")
                    if gpu_info.warnings:
                        for warning in gpu_info.warnings[:2]:
                            log_message(f"⚠️ {warning}")
                else:
                    log_message("ℹ️ No GPU detected or nvidia-smi not available")
            except Exception as e:
                log_message(f"⚠️ GPU detection failed: {e}")

            _global_backend_initialized = True
            print("✅ Global initialization complete")

        # PER-SESSION INITIALIZATION (every user/tab/reload)
        if not self._backend_initialized:
            print("📱 Initializing session...")

            # Load saved settings
            from .lib.settings import load_settings
            saved_settings = load_settings()

            if saved_settings:
                # Use saved settings
                self.backend_type = saved_settings.get("backend_type", self.backend_type)
                self.research_mode = saved_settings.get("research_mode", self.research_mode)
                self.temperature = saved_settings.get("temperature", self.temperature)
                self.enable_thinking = saved_settings.get("enable_thinking", self.enable_thinking)

                # Load vLLM YaRN & Context Settings
                self.enable_yarn = saved_settings.get("enable_yarn", self.enable_yarn)
                self.yarn_factor = saved_settings.get("yarn_factor", self.yarn_factor)
                self.vllm_max_tokens = saved_settings.get("vllm_max_tokens", self.vllm_max_tokens)
                self.vllm_native_context = saved_settings.get("vllm_native_context", self.vllm_native_context)

                # Load per-backend models (if available)
                backend_models = saved_settings.get("backend_models", {})
                if self.backend_type in backend_models:
                    self.selected_model = backend_models[self.backend_type].get("selected_model", self.selected_model)
                    self.automatik_model = backend_models[self.backend_type].get("automatik_model", self.automatik_model)
                else:
                    # Fallback: Use old-style global model settings
                    self.selected_model = saved_settings.get("selected_model", self.selected_model)
                    self.automatik_model = saved_settings.get("automatik_model", self.automatik_model)

                self.add_debug(f"⚙️ Settings loaded (backend: {self.backend_type})")

            # Apply config.py defaults as final fallback (only if settings.json didn't provide values)
            backend_defaults = config.BACKEND_DEFAULT_MODELS.get(self.backend_type, {})

            if not self.selected_model:
                self.selected_model = backend_defaults.get("selected_model", "")
                if self.selected_model:
                    self.add_debug(f"⚙️ Using default selected_model from config.py: {self.selected_model}")
                else:
                    self.add_debug("⚠️ No selected_model configured")

            if not self.automatik_model:
                self.automatik_model = backend_defaults.get("automatik_model", "")
                if self.automatik_model:
                    self.add_debug(f"⚙️ Using default automatik_model from config.py: {self.automatik_model}")
                else:
                    self.add_debug("⚠️ No automatik_model configured")

            # Generate session ID
            if not self.session_id:
                self.session_id = str(uuid.uuid4())
                self.add_debug(f"🆔 Session: {self.session_id[:8]}...")

            # Restore GPU info from global state
            gpu_info = _global_backend_state.get("gpu_info")
            if gpu_info:
                self.gpu_detected = True
                self.gpu_name = gpu_info.name
                self.gpu_compute_cap = gpu_info.compute_capability
                self.gpu_warnings = gpu_info.warnings

                # Filter available backends based on GPU compatibility
                # Only show backends that are actually compatible with the GPU
                if gpu_info.recommended_backends:
                    self.available_backends = gpu_info.recommended_backends
                    self.add_debug(f"✅ Compatible backends: {', '.join(self.available_backends)}")

                    # If current backend is not compatible, switch to first available
                    if self.backend_type not in self.available_backends:
                        old_backend = self.backend_type
                        self.backend_type = self.available_backends[0]
                        self.add_debug(f"⚠️ Backend '{old_backend}' not compatible with {gpu_info.name}")
                        self.add_debug(f"🔄 Auto-switched to '{self.backend_type}'")

            # Initialize backend (or restore from global state)
            self.add_debug("🔧 Initializing backend...")
            try:
                await self.initialize_backend()
                self.add_debug("✅ Backend ready")
            except Exception as e:
                self.add_debug(f"❌ Backend init failed: {e}")
                log_message(f"❌ Backend init failed: {e}")
                import traceback
                log_message(traceback.format_exc())

            self._backend_initialized = True
            print("✅ Session initialization complete")

    async def initialize_backend(self):
        """
        Initialize LLM backend

        NEW: Uses global state to prevent re-initialization on page reload.
        - First call: Load models, start vLLM if needed, store in global state
        - Subsequent calls: Restore from global state (fast!)
        """
        global _global_backend_state

        # Check if this backend was already initialized globally
        is_same_backend = (_global_backend_state["backend_type"] == self.backend_type)

        if is_same_backend and _global_backend_state["available_models"]:
            # FAST PATH: Restore from global state (page reload case)
            print(f"⚡ Backend '{self.backend_type}' already initialized, restoring from global state...")

            self.backend_url = _global_backend_state["backend_url"]
            self.available_models = _global_backend_state["available_models"]
            self.selected_model = _global_backend_state["selected_model"]
            self.automatik_model = _global_backend_state["automatik_model"]

            # Restore vLLM manager if exists
            if self.backend_type == "vllm":
                self._vllm_manager = _global_backend_state["vllm_manager"]
                if self._vllm_manager and self._vllm_manager.is_running():
                    self.add_debug("✅ vLLM server already running (restored from global state)")
                else:
                    self.add_debug("⚠️ vLLM manager exists but server not running")

            self.backend_healthy = True
            self.backend_info = f"{self.backend_type} - {len(self.available_models)} models"
            self.add_debug(f"✅ Backend ready (restored: {len(self.available_models)} models)")

            # Hide loading spinner (fast path = already initialized)
            self.backend_initializing = False

            return  # Done! No expensive initialization needed

        # SLOW PATH: Full initialization (first time or backend switch)
        print(f"🔧 Full backend initialization for '{self.backend_type}'...")

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

            # Store in global state for future page reloads
            _global_backend_state["backend_type"] = self.backend_type
            _global_backend_state["backend_url"] = self.backend_url
            _global_backend_state["selected_model"] = self.selected_model
            _global_backend_state["automatik_model"] = self.automatik_model
            _global_backend_state["available_models"] = self.available_models
            _global_backend_state["vllm_manager"] = self._vllm_manager

            print(f"✅ Backend '{self.backend_type}' fully initialized and stored in global state")

            # Mark initialization as complete (hide loading spinner)
            self.backend_initializing = False

        except Exception as e:
            self.backend_healthy = False
            self.backend_info = f"Error: {str(e)}"
            self.add_debug(f"❌ Backend initialization failed: {e}")
            self.backend_initializing = False  # Hide spinner even on error

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
            # vLLM YaRN & Context Settings
            "enable_yarn": self.enable_yarn,
            "yarn_factor": self.yarn_factor,
            "vllm_max_tokens": self.vllm_max_tokens,
            "vllm_native_context": self.vllm_native_context,
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
            # Clean up old backend resources (unload models, stop servers)
            old_backend = self.backend_type
            self.add_debug(f"🔄 Switching backend from {old_backend} to {new_backend}...")

            # Save current backend's models before switching
            self._save_settings()

            # Now clean up the old backend
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
            self.add_debug("✅ Backend switch complete")
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
        global _global_backend_state

        try:
            # Check if vLLM is already running from global state
            existing_manager = _global_backend_state.get("vllm_manager")
            if existing_manager and existing_manager.is_running():
                self.add_debug("✅ vLLM server already running (using existing process)")
                self._vllm_manager = existing_manager
                _global_backend_state["vllm_manager"] = existing_manager
                return

            self.add_debug(f"🚀 Starting vLLM server with {self.selected_model}...")

            # Auto-detect context from model config.json (no hardcoded values!)
            # Build YaRN config if enabled
            yarn_config = None
            if self.enable_yarn and self.yarn_factor > 1.0:
                yarn_config = {
                    "factor": self.yarn_factor,
                    "original_max_position_embeddings": self.vllm_native_context
                }
                self.add_debug(f"🔧 YaRN: {self.yarn_factor}x scaling ({self.vllm_native_context:,} → {int(self.vllm_native_context * self.yarn_factor):,} tokens)")

            # Initialize vLLM Process Manager
            # Use saved vllm_max_tokens if available (from previous auto-detection)
            # Otherwise use None to trigger auto-detection
            max_len = self.vllm_max_tokens if self.vllm_max_tokens > 0 else None
            if max_len:
                self.add_debug(f"📋 Using saved context limit: {max_len:,} tokens (aus Settings)")

            self._vllm_manager = vLLMProcessManager(
                port=8001,
                max_model_len=max_len,  # Use saved value or None for auto-detect
                gpu_memory_utilization=0.90,  # 90% safe on modern GPUs
                yarn_config=yarn_config  # YaRN context extension (if enabled)
            )

            # Start server with automatic context detection (only if max_len=None)
            # If saved value exists: Direct start with known limit (no crash)
            # If no saved value: Auto-detection cycle:
            #   1. Try native context (40K)
            #   2. If fail → extract hardware limit from error
            #   3. Restart with hardware limit + save to settings

            success, context_info = await self._vllm_manager.start_with_auto_detection(
                model=self.selected_model,
                timeout=120,
                feedback_callback=self.add_debug
            )

            if success and context_info:
                # Check if values changed (i.e., new detection occurred)
                values_changed = (
                    self.vllm_native_context != context_info["native_context"] or
                    self.vllm_max_tokens != context_info["hardware_limit"]
                )

                # Update state with detected values
                self.vllm_native_context = context_info["native_context"]
                self.vllm_max_tokens = context_info["hardware_limit"]

                # Persist detected values to settings file (only if changed)
                if values_changed:
                    self._save_settings()

                self.add_debug(f"📊 Context Info:")
                self.add_debug(f"  • Native: {context_info['native_context']:,} tokens (config.json)")
                self.add_debug(f"  • Hardware Limit: {context_info['hardware_limit']:,} tokens (VRAM)")
                self.add_debug(f"  • Used: {context_info['used_context']:,} tokens")
                if values_changed:
                    self.add_debug(f"💾 Werte in Settings gespeichert (kein erneuter Crash-Detection-Zyklus beim nächsten Start)")

                # Store in global state so it persists across page reloads
                _global_backend_state["vllm_manager"] = self._vllm_manager

                self.add_debug("✅ vLLM server ready on port 8001")
            else:
                raise RuntimeError("vLLM failed to start with auto-detection")

        except Exception as e:
            self.add_debug(f"❌ Failed to start vLLM: {e}")
            self._vllm_manager = None
            _global_backend_state["vllm_manager"] = None

    async def _stop_vllm_server(self):
        """Stop vLLM server process gracefully"""
        global _global_backend_state

        if self._vllm_manager and self._vllm_manager.is_running():
            self.add_debug("🛑 Stopping vLLM server...")
            await self._vllm_manager.stop()
            self._vllm_manager = None
            _global_backend_state["vllm_manager"] = None  # Clear from global state
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
                backend = llm_client._get_backend()

                if hasattr(backend, 'unload_all_models'):
                    count = await backend.unload_all_models()
                    if count > 0:
                        self.add_debug(f"✅ Unloaded {count} Ollama model(s)")
                    else:
                        self.add_debug("ℹ️ No Ollama models were loaded")
            except Exception as e:
                self.add_debug(f"⚠️ Error unloading Ollama models: {e}")

        elif old_backend == "vllm":
            # Stop vLLM server to free VRAM - ALWAYS use pkill for reliability
            self.add_debug("🛑 Stopping vLLM server...")
            try:
                import subprocess

                # Check if vLLM is running
                result = subprocess.run(["pgrep", "-f", "vllm serve"], capture_output=True, text=True)
                if result.returncode == 0:
                    # Kill vLLM process
                    subprocess.run(["pkill", "-f", "vllm serve"])
                    self.add_debug("✅ vLLM server stopped")

                    # Clean up manager reference
                    if self._vllm_manager:
                        self._vllm_manager = None
                        _global_backend_state["vllm_manager"] = None
                else:
                    self.add_debug("ℹ️ vLLM server was not running")

            except Exception as e:
                self.add_debug(f"❌ Failed to stop vLLM: {e}")

        elif old_backend == "tabbyapi":
            # Stop TabbyAPI server to free VRAM
            self.add_debug("🛑 Stopping TabbyAPI server...")
            try:
                import subprocess

                # Check if TabbyAPI is running (main.py or start.sh)
                result = subprocess.run(["pgrep", "-f", "tabbyapi"], capture_output=True, text=True)
                if result.returncode == 0:
                    # Kill TabbyAPI process
                    subprocess.run(["pkill", "-f", "tabbyapi"])
                    self.add_debug("✅ TabbyAPI server stopped")
                else:
                    self.add_debug("ℹ️ TabbyAPI server was not running")

            except Exception as e:
                self.add_debug(f"❌ Failed to stop TabbyAPI: {e}")

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
                    elif item["type"] == "thinking_warning":
                        # Show thinking mode warning (model doesn't support reasoning)
                        self.thinking_mode_warning = item["model"]

                    yield  # Update UI after each item

                # Separator wird bereits von conversation_handler gesendet
                # console_separator()  # Schreibt in Log-File
                # self.add_debug("────────────────────")  # Zeigt in Debug-Console
                # yield

            elif self.research_mode in ["quick", "deep"]:
                # Direct research mode (quick/deep)
                self.add_debug(f"🔍 Research Mode: {self.research_mode}")

                # Initialize temporary history entry for real-time display
                temp_history_index = len(self.chat_history)
                self.chat_history.append((user_msg, self.current_ai_response))

                # Build LLM options (include enable_thinking toggle)
                llm_options = {
                    'enable_thinking': self.enable_thinking
                }

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
                    llm_options=llm_options,
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
                    elif item["type"] == "thinking_warning":
                        # Show thinking mode warning (model doesn't support reasoning)
                        self.thinking_mode_warning = item["model"]

                    yield  # Update UI after each item

                # Set research_result flag if we got a result
                if result_data:
                    ai_text, updated_history, inference_time = result_data
                    # History and clearing already handled in loop above

            elif self.research_mode == "none":
                # No research mode: Direct LLM inference without web search
                self.add_debug(f"🧠 Eigenes Wissen (keine Websuche)")

                # Initialize temporary history entry for real-time display
                temp_history_index = len(self.chat_history)
                self.chat_history.append((user_msg, self.current_ai_response))

                # Build messages from history
                from .lib.message_builder import build_messages_from_history
                messages = build_messages_from_history(
                    history=self.chat_history[:-1],  # Exclude current temporary entry
                    current_user_text=user_msg
                )

                # Create backend instance
                from .backends import BackendFactory, LLMOptions, LLMMessage
                backend = BackendFactory.create(
                    self.backend_type,
                    base_url=self.backend_url
                )

                # Build LLM options
                llm_options = LLMOptions(
                    temperature=self.temperature,
                    enable_thinking=self.enable_thinking
                )

                # Convert to LLMMessage format
                llm_messages = [LLMMessage(role=m["role"], content=m["content"]) for m in messages]

                # Stream response directly from LLM
                import time
                inference_start = time.time()
                full_response = ""

                async for chunk in backend.chat_stream(
                    model=self.selected_model,
                    messages=llm_messages,
                    options=llm_options
                ):
                    if chunk["type"] == "content":
                        # Stream content to UI in real-time
                        self.current_ai_response += chunk["text"]
                        full_response += chunk["text"]
                        # Update the temporary entry in chat history
                        if temp_history_index < len(self.chat_history):
                            self.chat_history[temp_history_index] = (user_msg, self.current_ai_response)
                        yield  # Update UI

                inference_time = time.time() - inference_start

                # Add timing metadata to response
                timing_metadata = f'\n\n<span style="color: #888; font-size: 0.9em;">( Inferenz: {inference_time:.1f}s )</span>'
                final_response = full_response + timing_metadata

                # Update chat history with final response
                self.chat_history[temp_history_index] = (user_msg, final_response)
                yield  # Update UI

                # Clear response windows
                self.current_ai_response = ""
                self.current_user_message = ""
                self.is_generating = False
                yield  # Force UI update

                await backend.close()

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

                    # Separator nach Compression-Check (nur wenn Check durchgeführt wurde)
                    console_separator()  # Schreibt in Log-File
                    self.add_debug("────────────────────")  # Zeigt in Debug-Console
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

            # Separator nach Compression-Check (immer, auch wenn zu kurz)
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

    async def restart_backend(self):
        """Restart current LLM backend service and reload model list"""
        import subprocess
        import json
        import asyncio
        global _global_backend_state

        # Prevent concurrent restarts
        if self.backend_switching:
            self.add_debug("⚠️ Backend restart already in progress, please wait...")
            return

        self.backend_switching = True
        yield  # Update UI to disable buttons

        try:
            backend_name = self.backend_type.upper()
            self.add_debug(f"🔄 Restarting {backend_name} service...")
            yield  # Update UI

            if self.backend_type == "ollama":
                subprocess.run(["systemctl", "restart", "ollama"], check=True)
                self.add_debug(f"✅ {backend_name} service restarted")
                yield  # Update UI after restart

                # Wait for Ollama to be ready (active polling with retry)
                self.add_debug("⏳ Waiting for Ollama API to be ready...")
                yield  # Update UI

                max_retries = 10
                ollama_ready = False

                for attempt in range(max_retries):
                    try:
                        endpoint = f'{self.backend_url}/api/tags'
                        result = subprocess.run(
                            ['curl', '-s', endpoint],
                            capture_output=True,
                            text=True,
                            timeout=2.0
                        )

                        if result.returncode == 0:
                            # Try to parse JSON to verify API is actually ready
                            data = json.loads(result.stdout)
                            self.available_models = [m["name"] for m in data.get("models", [])]

                            # Update global state
                            _global_backend_state["available_models"] = self.available_models

                            elapsed_time = (attempt + 1) * 0.5
                            self.add_debug(f"✅ Ollama ready after {elapsed_time:.1f}s ({len(self.available_models)} models found)")
                            ollama_ready = True
                            break
                    except Exception:
                        pass  # Retry on any error

                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5)  # Short polling interval
                        yield  # Update UI during polling

                if not ollama_ready:
                    self.add_debug("⚠️ Ollama API might not be ready yet (timeout after 5s)")
                    yield

            elif self.backend_type == "vllm":
                # vLLM: Stop and restart with current model
                self.add_debug("⏹️ Stopping vLLM server...")
                yield  # Update UI
                await self._stop_vllm_server()

                self.add_debug("🚀 Starting vLLM server...")
                yield  # Update UI
                await self._start_vllm_server()

                self.add_debug(f"✅ {backend_name} restarted successfully")
                yield  # Update UI
            elif self.backend_type == "tabbyapi":
                # TabbyAPI: Unload and reload model via API
                self.add_debug("⏹️ Unloading TabbyAPI model...")
                yield  # Update UI

                try:
                    # Unload current model
                    import requests
                    response = requests.post(
                        f"{self.backend_url}/v1/model/unload",
                        headers={"Content-Type": "application/json"},
                        timeout=10.0
                    )

                    if response.status_code == 200:
                        self.add_debug("✅ Model unloaded successfully")
                        yield

                        # Reload model
                        self.add_debug("🚀 Reloading TabbyAPI model...")
                        yield

                        load_response = requests.post(
                            f"{self.backend_url}/v1/model/load",
                            json={"name": self.selected_model},
                            headers={"Content-Type": "application/json"},
                            timeout=30.0
                        )

                        if load_response.status_code == 200:
                            self.add_debug(f"✅ {backend_name} restarted successfully")
                        else:
                            self.add_debug(f"⚠️ Model reload failed: {load_response.status_code}")
                    else:
                        self.add_debug(f"⚠️ Model unload failed: {response.status_code}")

                except Exception as e:
                    self.add_debug(f"⚠️ TabbyAPI restart failed: {e}")

                yield  # Update UI

        except Exception as e:
            self.add_debug(f"❌ {backend_name} restart failed: {e}")
        finally:
            self.backend_switching = False
            yield  # Re-enable buttons

    async def restart_ollama(self):
        """Legacy method - calls restart_backend()"""
        await self.restart_backend()


    def restart_aifred(self):
        """Restart AIfred service via systemctl"""
        import subprocess
        import threading

        try:
            self.add_debug("🔄 Restarting AIfred service...")

            # Schedule systemd restart in background thread
            # This allows us to return rx.call_script() BEFORE the service dies
            def restart_service_delayed():
                import time
                time.sleep(0.5)  # Short delay to let browser script execute first
                subprocess.run(["systemctl", "restart", "aifred-intelligence"], check=False)

            thread = threading.Thread(target=restart_service_delayed, daemon=True)
            thread.start()

            self.add_debug("✅ AIfred service restart initiated")
            self.add_debug("🔄 Browser wird in 0.5s neu geladen...")

            # Return the reload script IMMEDIATELY
            # This executes in browser BEFORE systemd kills the service
            # Browser will reload, wait for service to come back up, then reconnect
            return rx.call_script("window.location.reload(true)")

        except Exception as e:
            self.add_debug(f"❌ AIfred service restart failed: {e}")


    def set_selected_model(self, model: str):
        """Set selected model"""
        self.selected_model = model
        # Clear thinking mode warning when model changes
        self.thinking_mode_warning = ""
        self.add_debug(f"📝 Model changed to: {model}")
        self._save_settings()

    def toggle_thinking_mode(self):
        """Toggle Qwen3 Thinking Mode"""
        self.enable_thinking = not self.enable_thinking
        mode_name = "Thinking Mode" if self.enable_thinking else "Non-Thinking Mode"
        temp = "0.6" if self.enable_thinking else "0.7"
        self.add_debug(f"🧠 {mode_name} aktiviert (temp={temp})")
        self._save_settings()

    def toggle_yarn(self):
        """Toggle YaRN context extension"""
        self.enable_yarn = not self.enable_yarn
        status = "aktiviert" if self.enable_yarn else "deaktiviert"
        self.add_debug(f"📏 YaRN Context Extension {status} (Faktor: {self.yarn_factor}x)")
        if self.enable_yarn:
            self.add_debug("⚠️ Backend-Neustart erforderlich für YaRN-Aktivierung!")
        self._save_settings()

    def set_yarn_factor(self, factor: str):
        """Set YaRN scaling factor"""
        try:
            factor_float = float(factor)
            if 1.0 <= factor_float <= 8.0:
                self.yarn_factor = factor_float
                # Use actual vLLM max_model_len (26608), not native model context (40960)
                estimated_context = int(self.vllm_max_tokens * factor_float)
                self.add_debug(f"📏 YaRN Faktor: {factor_float}x (~{estimated_context} tokens)")

                # Warn if factor is high (potential VRAM overflow)
                if factor_float > 2.0:
                    self.add_debug(f"⚠️ Hoher YaRN-Faktor ({factor_float}x) kann VRAM überschreiten → möglicher Crash!")
                    self.add_debug("💡 Tipp: Bei VRAM-Problemen Faktor reduzieren oder mehr GPU-RAM nutzen")

                self._save_settings()
        except ValueError:
            self.add_debug(f"❌ Ungültiger YaRN-Faktor: {factor}")

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
        self._save_settings()  # Persist research mode to settings.json

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
