"""Backend mixin for AIfred state.

Handles backend initialization, switching between backends (Ollama, llama.cpp,
vLLM, Cloud APIs), model loading, vision detection, and on_load.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..lib.config import LLAMASWAP_BACKENDS

import os
import subprocess
import uuid

import reflex as rx

from ..lib.logging_utils import initialize_debug_log, log_exception, log_message
from ..lib import config
from ..lib.config import (
    CLOUD_API_PROVIDERS,
)
from ..backends.cloud_api import is_cloud_api_configured
from ..lib.model_manager import sort_models_grouped
from ..lib.gpu_utils import total_actual_vram_gb
from ..lib.stt_client import get_default_client as get_stt_client

if TYPE_CHECKING:
    from ..lib.agent_tuning import AgentTuning

# Module-level globals are in _base.py and re-exported from __init__.py.
# We import them at usage sites to avoid circular imports.


# Sentinel-ID fuer "wie AIfred-LLM" in den Modell-Dropdowns. Radix laesst
# einen LEEREN Options-Wert nicht zu, intern bleibt die Bedeutung aber die
# leere Modell-ID ("erbe vom Hauptmodell").
SAME_AS_AIFRED = "__same_as_aifred__"


class BackendMixin(rx.State, mixin=True):
    """Mixin for backend initialization, switching, and model management."""

    if TYPE_CHECKING:
        # Members of other AIState mixins used here. mypy checks each mixin on its own and sees
        # them only through these declarations; where AIState combines the mixins it checks them
        # against the real definitions.
        _session_initialized: bool
        agent_tuning: rx.Field[dict[str, AgentTuning]]
        auto_refresh_enabled: rx.Field[bool]
        consensus_type: rx.Field[str]
        debug_messages: rx.Field[list[str]]
        enable_tts: rx.Field[bool]
        enter_sends_message: rx.Field[bool]
        image_upload_warning: rx.Field[str]
        max_debate_rounds: rx.Field[int]
        multi_agent_mode: rx.Field[str]
        narrator_voices: rx.Field[dict[str, str]]
        pending_images: rx.Field[list[dict[str, str]]]
        session_id: rx.Field[str]
        show_transcription: rx.Field[bool]
        temperature_mode: rx.Field[str]
        tts_agents: rx.Field[dict[str, dict[str, Any]]]
        tts_autoplay: rx.Field[bool]
        tts_pitch: rx.Field[str]
        tts_playback_rate: rx.Field[str]
        ui_language: rx.Field[str]
        user_gender: rx.Field[str]
        user_name: rx.Field[str]

        def _effective_model_id(self, agent: str) -> str: ...
        def _load_channel_settings(self, settings: dict[str, Any]) -> None: ...
        def _refresh_vision_settings(self) -> None: ...
        def _reset_sampling_for_all_agents(
            self,
            *,
            inheriting_only: bool,
            include_temperature: bool,
        ) -> None: ...
        def _save_settings(self) -> None: ...
        def _show_model_calibration_info(self, model_id: str): ...
        @rx.event
        def add_debug(self, message: str) -> None: ...
        @rx.event
        def refresh_session_list(self): ...

    # ── Backend Settings ──────────────────────────────────────────
    backend_type: rx.Field[str] = rx.field("ollama")  # "ollama", "vllm", "llamacpp", "cloud_api"
    backend_id: rx.Field[str] = rx.field("ollama")  # Pure backend ID (synced with backend_type)
    current_backend_label: rx.Field[str] = rx.field("Ollama")  # Display label for current backend
    backend_url: rx.Field[str] = rx.field(config.DEFAULT_OLLAMA_URL)

    # Cloud API Settings (only relevant when backend_type == "cloud_api")
    cloud_api_provider: rx.Field[str] = rx.field("qwen")  # "claude", "qwen", "kimi"
    cloud_api_provider_label: rx.Field[str] = rx.field("Qwen (DashScope)")
    cloud_api_key_configured: rx.Field[bool] = rx.field(False)

    # Backend ID/Label Mapping (static - all possible backends)
    available_backends_dict: rx.Field[dict[str, str]] = rx.field(default_factory=lambda: {
        "ollama": "Ollama",
        "llamacpp": "llama.cpp",
        "vllm": "vLLM",
        "cloud_api": "Cloud APIs",
    })

    # ── Model Settings ────────────────────────────────────────────
    # Per-agent model/model_id live in agent_tuning (AgentConfigMixin).
    available_models: rx.Field[list[str]] = rx.field(default_factory=list)
    available_models_dict: rx.Field[dict[str, str]] = rx.field(default_factory=dict)
    vision_models_cache: rx.Field[list[str]] = rx.field(default_factory=list)
    available_vision_models_list: rx.Field[list[str]] = rx.field(default_factory=list)
    # Fertig gerenderte Vision-Modell-Zeilen fürs Dropdown: je ein Dict mit
    # ``name`` (Display-Name = Select-value), ``badge`` (⚡ No Swap / 🔄 Swap
    # / "") und ``color``. "No Swap" heißt: Bildanfrage läuft über den
    # Ollama-Side-Channel parallel, das Chat-Modell bleibt geladen; "Swap":
    # llama-swap verdrängt es. Einmal beim Discovery bestimmt (Ollama-VLM-
    # Liste wird dort ohnehin geholt) — kein API-Call im Render.
    available_vision_models_rich: rx.Field[list[dict[str, str]]] = rx.field(default_factory=list)

    automatik_model: rx.Field[str] = rx.field("")
    automatik_model_id: rx.Field[str] = rx.field("")

    # ── Backend Status ────────────────────────────────────────────
    backend_healthy: rx.Field[bool] = rx.field(False)
    backend_info: rx.Field[str] = rx.field("")
    model_count: rx.Field[int] = rx.field(0)
    backend_switching: rx.Field[bool] = rx.field(False)
    backend_initializing: rx.Field[bool] = rx.field(True)

    # ── Initialization Flags ──────────────────────────────────────
    _backend_initialized: bool = False
    _model_preloaded: bool = False
    _on_load_running: bool = False

    # ── GPU Detection ─────────────────────────────────────────────
    gpu_detected: rx.Field[bool] = rx.field(False)
    gpu_name: rx.Field[str] = rx.field("")
    gpu_compute_cap: rx.Field[float] = rx.field(0.0)
    gpu_warnings: rx.Field[list[str]] = rx.field(default_factory=list)
    gpu_count: rx.Field[int] = rx.field(1)
    gpu_vram_gb: rx.Field[float] = rx.field(0)
    gpu_all_names: rx.Field[list[str]] = rx.field(default_factory=list)
    available_backends: rx.Field[list[str]] = rx.field(default_factory=lambda: [
        "ollama", "llamacpp", "vllm", "cloud_api",
    ])
    available_backends_list: rx.Field[list[str]] = rx.field(default_factory=lambda: [
        "Ollama", "llama.cpp", "vLLM", "Cloud APIs",
    ])

    # ── vLLM YaRN Settings ────────────────────────────────────────
    enable_yarn: rx.Field[bool] = rx.field(False)
    yarn_factor: rx.Field[float] = rx.field(1.0)
    yarn_factor_input: rx.Field[str] = rx.field("1.0")
    yarn_max_factor: rx.Field[float] = rx.field(0.0)
    yarn_max_tested: rx.Field[bool] = rx.field(False)
    vllm_max_tokens: rx.Field[int] = rx.field(0)
    vllm_native_context: rx.Field[int] = rx.field(0)

    # ── Cached Model Metadata ─────────────────────────────────────
    _automatik_model_context_limit: int = 0
    _min_agent_context_limit: int = 0

    # ================================================================
    # COMPUTED PROPERTIES
    # ================================================================

    @rx.var
    def gpu_display_text(self) -> str:
        """Format GPU info for UI display."""
        if not self.gpu_detected:
            return ""

        if self.gpu_count == 1:
            return f"{self.gpu_name} (Compute {self.gpu_compute_cap}, {self.gpu_vram_gb} GB)"

        unique_names = list(dict.fromkeys(self.gpu_all_names))
        if len(unique_names) == 1:
            return f"{self.gpu_count}x {unique_names[0]} (Compute {self.gpu_compute_cap}, {self.gpu_vram_gb} GB total)"
        # Mixed GPUs: show count per type (e.g. "2x Tesla P40 + Quadro RTX 8000")
        from collections import Counter
        counts = Counter(self.gpu_all_names)
        parts = []
        for name in unique_names:
            count = counts[name]
            parts.append(f"{count}x {name}" if count > 1 else name)
        return f"{' + '.join(parts)} ({self.gpu_vram_gb} GB total)"

    @rx.var
    def gpu_compatible_text(self) -> str:
        """Compatible backends as display text (excluding cloud_api)."""
        display_names = {
            "ollama": "Ollama",
            "llamacpp": "llama.cpp",
            "vllm": "vLLM",
        }
        names = [display_names.get(b, b) for b in self.available_backends if b != "cloud_api"]
        return ", ".join(names) if names else ""

    @rx.var
    def backend_supports_dynamic_models(self) -> bool:
        """Check if current backend supports dynamic model switching.

        Seit vLLM ueber llama-swap laeuft, koennen ALLE Backends Modelle
        dynamisch wechseln (llama-swap swappt die Eintraege on demand).
        """
        return True

    @rx.var
    def backend_label(self) -> str:
        """Get display label for current backend."""
        return self.available_backends_dict.get(self.backend_id, self.backend_id)

    @rx.var(deps=["automatik_model_id"], auto_deps=False)
    def automatik_model_select_id(self) -> str:
        """Select-Wert: die Modell-ID, leer wird zum Sentinel."""
        return SAME_AS_AIFRED if not self.automatik_model_id else self.automatik_model_id

    @rx.var(deps=["available_models_dict", "ui_language"], auto_deps=False)
    def automatik_available_models_rich(self) -> list[dict[str, str]]:
        """Optionen fuer Automatik: Sentinel zuerst, dann alle Modelle."""
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"
        return [{"id": SAME_AS_AIFRED, "label": t("sokrates_llm_same", lang=lang),
                 "badge": "", "color": ""}] + [
            {"id": mid, "label": label, "badge": "", "color": ""}
            for mid, label in self.available_models_dict.items()
        ]

    @property
    def _effective_automatik_id(self) -> str:
        """Model ID of the Automatik (rules: lib/runtime_settings)."""
        from ..lib.runtime_settings import effective_automatik_id
        return effective_automatik_id(self)

    # ================================================================
    # HELPER METHODS
    # ================================================================

    def _load_all_agent_model_params(self) -> None:
        """Load model parameters for all agents from cache (rules: lib/runtime_settings)."""
        from ..lib.runtime_settings import load_agent_model_params
        for agent, tuning in self.agent_tuning.items():
            load_agent_model_params(self, agent, tuning.model_id)

    def _clear_models_state(self, include_vision: bool = False) -> None:
        """Reset available models to empty (used on backend/provider switch errors)."""
        self.available_models_dict = {}
        self.available_models = []
        if include_vision:
            self.vision_models_cache = []
            self.available_vision_models_list = []
            self.available_vision_models_rich = []

    # ================================================================
    # ON_LOAD
    # ================================================================

    @rx.event
    async def on_load(self):
        """Called when page loads - initialize backend and load models.

        Backend is initialized once globally at server startup.
        Page reloads simply restore state from global variables.
        """
        from . import _global_backend_initialized, _global_backend_state

        # Use a mutable reference for the module-level flag
        import aifred.state._base as _base_module

        log_message(f"🔥 on_load() CALLED - Global init: {_global_backend_initialized}, Session init: {self._backend_initialized}")

        # FIRST-TIME GLOBAL INITIALIZATION (once per server start).
        # The whole init body runs INSIDE _backend_init_lock so concurrent
        # on_load callbacks (two tabs opening at once, ASGI worker race)
        # serialize: the first one runs init, the others block on the
        # lock until it's done and then re-check the flag and skip.
        if not _base_module._global_backend_initialized:
            async with _base_module._backend_init_lock:
                if not _base_module._global_backend_initialized:
                    print("=" * 60)
                    print("🚀 FIRST-TIME SERVER INITIALIZATION...")
                    print("=" * 60)

                    initialize_debug_log(force_reset=False)

                    import platform
                    kernel = platform.release()
                    is_wsl = "microsoft" in kernel.lower()
                    is_windows = os.name == "nt"
                    if is_wsl:
                        env_label = "WSL2 (WDDM — VRAM swapping possible)"
                    elif is_windows:
                        env_label = "Windows (WDDM — VRAM swapping possible)"
                    else:
                        env_label = "Native Linux (no VRAM swapping)"
                    log_message(f"🖥️ Platform: {platform.system()} {kernel} — {env_label}")


                    # Audio state cleanup task
                    import asyncio as _asyncio
                    from ..lib.audio_state import cleanup_audio_state_task
                    _asyncio.create_task(cleanup_audio_state_task())

                    # Vision pre-warm: when vision_mode is "live" the VLM
                    # is supposed to stay resident in VRAM. Fire-and-forget
                    # because the load can take seconds and we don't want
                    # to block server startup behind it. On-demand mode
                    # deliberately skips this — the whole point of
                    # on-demand is lazy loading.
                    from ..lib.vision_prewarm import get_vision_mode, prewarm_vlm
                    if get_vision_mode() == "live":
                        log_message("🎥 Vision pre-warm: starting (mode=live)")
                        _asyncio.create_task(prewarm_vlm())

                    # Lookup-Cache cleanup task (Speculative-Decoding-Caches einsammeln)
                    from ..lib.lookup_cache_cleanup import cleanup_lookup_cache_task
                    _asyncio.create_task(cleanup_lookup_cache_task())

                    # Vision cleanup task — räumt alte Face-Crops, Motion-
                    # Frames und Vision-DB-Events auf, TTL aus
                    # plugins/tools/vision/settings.json face_recognition.retention_days.
                    from ..lib.vision_cleanup import cleanup_vision_task
                    _asyncio.create_task(cleanup_vision_task())

                    # Audit-Log cleanup task — prunt die append-only tool_audit-
                    # Tabelle (SECURITY_AUDIT_RETENTION_DAYS) am selben Slot.
                    from ..lib.security import cleanup_audit_log_task
                    _asyncio.create_task(cleanup_audit_log_task())

                    # vLLM-Compile-Cache cleanup task — Artefakte geloeschter
                    # Modelle und verworfener Betriebspunkte (Alter oder
                    # Obergrenze, siehe VLLM_PRODUCTION_CACHE_*) am selben Slot.
                    from ..lib.calibration.vllm_probe import cleanup_vllm_compile_cache_task
                    _asyncio.create_task(cleanup_vllm_compile_cache_task())

                    # Audio index incremental sync (only for sources already populated)
                    from ..lib.audio_index import sync_audio_index_task
                    _asyncio.create_task(sync_audio_index_task())

                    # Whisper STT: Docker container (started on demand or always-on)
                    if get_stt_client().is_ready():
                        log_message("✅ Whisper: Docker service ready")
                    else:
                        log_message("⚠️ Whisper: Docker service not running (will start on first STT request)")

                    # GPU Detection (once per server)
                    log_message("🔍 Detecting GPU capabilities...")
                    try:
                        from ..lib.gpu_detection import detect_gpu
                        gpu_info = detect_gpu()
                        if gpu_info:
                            _global_backend_state["gpu_info"] = gpu_info

                            total_vram_gb = total_actual_vram_gb(gpu_info)

                            unique_names = list(dict.fromkeys(gpu_info.all_gpu_names))
                            if gpu_info.gpu_count == 1:
                                log_message(f"✅ GPU: {gpu_info.name} (Compute {gpu_info.compute_capability}, {total_vram_gb} GB)")
                            elif len(unique_names) == 1:
                                log_message(f"✅ GPU: {gpu_info.gpu_count}x {gpu_info.name} (Compute {gpu_info.compute_capability}, {total_vram_gb} GB total)")
                            else:
                                log_message(f"✅ GPU: {' + '.join(unique_names)} ({total_vram_gb} GB total)")

                            if gpu_info.unsupported_backends:
                                log_message(f"⚠️ Incompatible backends: {', '.join(gpu_info.unsupported_backends)}")
                            if gpu_info.warnings:
                                for warning in gpu_info.warnings[:2]:
                                    log_message(f"⚠️ {warning}")
                        else:
                            log_message("ℹ️ No GPU detected or nvidia-smi not available")
                    except (subprocess.CalledProcessError, ImportError, RuntimeError) as e:
                        log_message(f"⚠️ GPU detection failed: {e}")

                    # Unload all Ollama models to ensure clean VRAM slate
                    try:
                        from ..backends.ollama import OllamaBackend
                        ollama = OllamaBackend()
                        _success, unloaded = await ollama.unload_all_models(wait_for_stability=False)
                        if unloaded:
                            log_message(f"🧹 Startup: Unloaded {len(unloaded)} model(s) from VRAM: {', '.join(unloaded)}")
                        else:
                            log_message("🧹 Startup: No models to unload (VRAM clean)")
                        await ollama.client.aclose()
                    except (RuntimeError, OSError) as e:
                        log_message(f"ℹ️ Ollama not available for startup cleanup: {e}")

                    # Start Message Hub (channel listeners, scheduler)
                    from ..lib.message_hub import message_hub, register_channel_workers
                    register_channel_workers(message_hub)
                    from ..lib.scheduler import scheduler_loop
                    if not message_hub.is_running("scheduler"):
                        message_hub.register("scheduler", scheduler_loop)
                    await message_hub.start_all()

                    # Only mark done AFTER the whole body succeeded — a
                    # failure above leaves the flag False so the next
                    # on_load retries instead of running forever without
                    # vector cache / message hub / scheduler.
                    _base_module._global_backend_initialized = True
                    print("✅ Global initialization complete")

        # PER-SESSION INITIALIZATION (every user/tab/reload)
        if self._on_load_running:
            log_message("⏭️ on_load already running, skipping duplicate call")
            return
        self._on_load_running = True
        # try/finally: any exception (or GeneratorExit at the cookie yield
        # below) must reset the flag — otherwise every later on_load returns
        # early and the session can never initialize until server restart.
        try:
            # Load session list immediately (before backend init which can take time)
            self.refresh_session_list()

            # Cross-session model sync: the full settings restore below only runs
            # for a FRESH session (guarded by _backend_initialized). A tab reload
            # of an existing session would otherwise keep a stale model when it
            # was changed elsewhere (another tab/device) — settings.json is the
            # SSOT and is written on every model change. Re-read just the
            # persisted model fields on every on_load so the dropdown and the
            # effective model reflect the current persisted state. Idempotent:
            # on a fresh session the full restore sets the same values afterwards.
            from ..lib.settings import load_settings as _sync_load_settings
            _sync = _sync_load_settings() or {}
            for _sync_agent, _sync_entry in _sync.get("agent_tuning", {}).items():
                _sync_bucket = self.agent_tuning.get(_sync_agent)
                if _sync_bucket is None:
                    continue
                if "model" in _sync_entry:
                    _sync_bucket.model = _sync_entry["model"]
                if "model_id" in _sync_entry:
                    _sync_bucket.model_id = _sync_entry["model_id"]
            if "automatik_model" in _sync:
                self.automatik_model = _sync["automatik_model"]

            if not self._backend_initialized:
                log_message("📱 Initializing session...")

                from ..lib.formatting import set_ui_locale
                set_ui_locale(self.ui_language)

                # Load saved settings
                from ..lib.settings import load_settings
                saved_settings = load_settings()
                _had_backend_settings = False

                if saved_settings:
                    self.backend_type = saved_settings.get("backend_type", self.backend_type)
                    self.backend_id = self.backend_type
                    self.current_backend_label = self.available_backends_dict.get(self.backend_id, self.backend_id)

                    # Cloud API provider
                    saved_provider = saved_settings.get("cloud_api_provider", self.cloud_api_provider)
                    if saved_provider in CLOUD_API_PROVIDERS:
                        self.cloud_api_provider = saved_provider
                        self.cloud_api_provider_label = CLOUD_API_PROVIDERS[saved_provider]["name"]
                        # Resolve the key status here too — otherwise the button
                        # shows "key missing" on startup until the user toggles the
                        # backend, because the async model-load path (which sets it)
                        # doesn't run during this synchronous settings restore.
                        self.cloud_api_key_configured = is_cloud_api_configured(saved_provider)

                    self.temperature_mode = saved_settings.get("temperature_mode", self.temperature_mode)
                    # Load UI language and update global locale + prompt language
                    saved_ui_lang = saved_settings.get("ui_language", self.ui_language)
                    if saved_ui_lang in ["de", "en"]:
                        self.ui_language = saved_ui_lang
                        set_ui_locale(saved_ui_lang)

                    # Message Hub channel switches: the session must know them
                    # from the start, or its first settings save would lose them.
                    self._load_channel_settings(saved_settings)

                    # Load user name and gender
                    self.user_name = saved_settings.get("user_name", self.user_name)
                    self.user_gender = saved_settings.get("user_gender", self.user_gender)

                    # Per-agent tuning and the backend's models (rules: lib/runtime_settings).
                    from ..lib.runtime_settings import apply_saved_models, apply_saved_tuning
                    apply_saved_tuning(self, saved_settings)
                    _had_backend_settings = apply_saved_models(self, saved_settings)
                    self.automatik_model = self.automatik_model_id
                    if _had_backend_settings:
                        # Load all model parameters from cache on startup
                        self._load_all_agent_model_params()

                    self.add_debug(f"⚙️ Settings loaded (backend: {self.backend_type})")

                    # Send TTS playback rate to JavaScript
                    rate_value = self.tts_playback_rate.replace("x", "")
                    yield rx.call_script(f"setTimeout(() => {{ if (typeof setTtsPlaybackRate === 'function') setTtsPlaybackRate({rate_value}); }}, 100)")

                # The backend's default models where none is set (rules: lib/runtime_settings).
                from ..lib.runtime_settings import apply_default_models
                for agent, model in apply_default_models(self, _had_backend_settings):
                    if model:
                        self.add_debug(f"⚙️ Using default {agent}_model from config.py: {model}")
                    elif agent in ("aifred", "automatik"):
                        self.add_debug(f"⚠️ No {agent}_model configured")
                    elif agent == "vision":
                        self.add_debug("ℹ️ No vision_model configured - will auto-detect first available vision model")
                self.automatik_model = self.automatik_model_id

                # Generate internal session ID
                if not self.session_id:
                    self.session_id = str(uuid.uuid4())

                # Restore GPU info from global state
                gpu_info = _global_backend_state.get("gpu_info")
                if gpu_info:
                    self.gpu_detected = True
                    self.gpu_name = gpu_info.name
                    self.gpu_compute_cap = gpu_info.compute_capability
                    self.gpu_warnings = gpu_info.warnings
                    self.gpu_count = gpu_info.gpu_count
                    self.gpu_all_names = gpu_info.all_gpu_names

                    self.gpu_vram_gb = total_actual_vram_gb(gpu_info)

                    self.add_debug(f"🎮 GPU: {self.gpu_display_text}")

                    if gpu_info.recommended_backends:
                        self.available_backends = gpu_info.recommended_backends.copy()
                        if "cloud_api" not in self.available_backends:
                            self.available_backends.append("cloud_api")
                        self.available_backends_list = [
                            self.available_backends_dict.get(bid, bid)
                            for bid in self.available_backends
                        ]
                        _global_backend_state["available_backends"] = self.available_backends
                        _global_backend_state["available_backends_list"] = self.available_backends_list
                        self.add_debug(f"✅ Compatible backends: {', '.join(self.available_backends)}")

                        if self.backend_type not in self.available_backends:
                            old_backend = self.backend_type
                            self.backend_type = self.available_backends[0]
                            self.backend_id = self.backend_type
                            self.add_debug(f"⚠️ Backend '{old_backend}' not compatible with {gpu_info.name}")
                            self.add_debug(f"🔄 Auto-switched to '{self.backend_type}'")

                        self.current_backend_label = self.available_backends_dict.get(self.backend_id, self.backend_id)
                        _global_backend_state["current_backend_label"] = self.current_backend_label

                # Initialize backend (or restore from global state)
                self.add_debug("🔧 Initializing backend...")
                backend_init_success = False
                was_fast_path = False
                try:
                    was_fast_path = await self.initialize_backend()
                    backend_init_success = True
                except Exception as e:  # noqa: BLE001 — boundary of the backend start: the UI shows the error state, the log the traceback
                    self.add_debug(f"❌ Backend init failed: {e}")
                    log_exception("❌ Backend init failed", e)

                if backend_init_success and not was_fast_path:
                    self.add_debug("✅ Backend ready")

                if backend_init_success:
                    from aifred.lib.logging_utils import console_separator
                    console_separator()
                    self.debug_messages = [*self.debug_messages, "────────────────────"]

                self._backend_initialized = True
                log_message("✅ Session initialization complete")

                # Authentication: Read username from cookie
                if not self._session_initialized:
                    from ..lib.browser_storage import get_username_script
                    log_message("🔐 Requesting username cookie from browser...")
                    # Import AIState at usage site to avoid circular import
                    from ._base import AIState
                    yield rx.call_script(
                        get_username_script(),
                        callback=AIState.handle_username_loaded
                    )

        finally:
            self._on_load_running = False

    # ================================================================
    # INITIALIZE BACKEND
    # ================================================================

    @rx.event
    async def initialize_backend(self) -> bool:
        """Initialize LLM backend.

        Uses global state to prevent re-initialization on page reload.
        Returns True for FAST PATH (restored), False for SLOW PATH (full init).
        """
        from . import _global_backend_state

        is_same_backend = (_global_backend_state["backend_type"] == self.backend_type)
        init_complete = _global_backend_state.get("_init_complete", False)

        if is_same_backend and _global_backend_state["available_models"] and init_complete:
            # FAST PATH: Restore from global state
            log_message(f"⚡ Backend '{self.backend_type}' already initialized, restoring from global state...")

            self.backend_url = _global_backend_state["backend_url"]
            self.available_models = _global_backend_state["available_models"]
            self.available_models_dict = _global_backend_state.get("available_models_dict", {})
            self.vision_models_cache = _global_backend_state.get("vision_models_cache", [])
            self.available_vision_models_list = _global_backend_state.get("available_vision_models_list", [])
            # Badges hängen vom gerade Geladenen ab (check_visiond_fits) —
            # pro Sitzung neu berechnen statt den Stand vom Backend-Start zu
            # übernehmen. Cloud-APIs haben keine Badges.
            if self.backend_type == "cloud_api":
                self.available_vision_models_rich = _global_backend_state.get("available_vision_models_rich", [])
            else:
                self.available_vision_models_rich = self._build_vision_rich(self.vision_models_cache)

            self.available_backends = _global_backend_state.get("available_backends", self.available_backends)
            self.available_backends_list = _global_backend_state.get("available_backends_list", self.available_backends_list)
            self.current_backend_label = _global_backend_state.get("current_backend_label",
                self.available_backends_dict.get(self.backend_type, self.backend_type))

            # Validate and sync aifred_model (model_id is always base ID)
            if self.agent_tuning["aifred"].model_id and self.agent_tuning["aifred"].model_id in self.available_models_dict:
                self.agent_tuning["aifred"].model = self._label_for("aifred", self.agent_tuning["aifred"].model_id)
            elif _global_backend_state.get("aifred_model_id") in self.available_models_dict:
                self.agent_tuning["aifred"].model_id = _global_backend_state["aifred_model_id"]
                self.agent_tuning["aifred"].model = self._label_for("aifred", self.agent_tuning["aifred"].model_id)
            else:
                # Stale main model — same policy as the SLOW PATH below:
                # clear instead of silently substituting another model,
                # the user picks explicitly (send_message guards on empty).
                log_message(
                    f"⚠️ Configured model '{self.agent_tuning["aifred"].model_id}' not available — cleared"
                )
                self.agent_tuning["aifred"].model_id = ""
                self.agent_tuning["aifred"].model = ""

            # Validate and sync automatik_model
            if not self.automatik_model_id:
                self.automatik_model = ""
            elif self.automatik_model_id in self.available_models_dict:
                self.automatik_model = self._label_for("automatik", self.automatik_model_id)
            elif _global_backend_state.get("automatik_model_id") in self.available_models_dict:
                self.automatik_model_id = _global_backend_state["automatik_model_id"]
                self.automatik_model = self._label_for("automatik", self.automatik_model_id)
            else:
                log_message(f"⚠️ Configured automatik model '{self.automatik_model_id}' not found, using same as AIfred")
                self.automatik_model_id = ""
                self.automatik_model = ""

            # Validate and sync vision_model. Der Describer wird
            # backend-uebergreifend gewaehlt, das Label kommt deshalb aus
            # dem Vision-Katalog — die Backend-Liste enthaelt unter vLLM
            # nur -vllm-Eintraege und liesse die blanke Id stehen, worauf
            # das Dropdown leer bliebe (Peuqui, 2026-09-01).
            if self.agent_tuning["vision"].model_id and self.agent_tuning["vision"].model_id in self.vision_models_cache:
                self.agent_tuning["vision"].model = self._label_for("vision", self.agent_tuning["vision"].model_id)
            elif _global_backend_state.get("vision_model_id") in self.vision_models_cache:
                self.agent_tuning["vision"].model_id = _global_backend_state["vision_model_id"]
                self.agent_tuning["vision"].model = self._label_for("vision", self.agent_tuning["vision"].model_id)
            elif self.vision_models_cache:
                self.agent_tuning["vision"].model_id = self.vision_models_cache[0]
                self.agent_tuning["vision"].model = self._label_for("vision", self.agent_tuning["vision"].model_id)

            # Validate and sync secondary-agent models (Sokrates/Salomo/
            # custom). Clearing the id to "" means "same as AIfred".
            for agent, tuning in self.agent_tuning.items():
                if agent in ("aifred", "vision"):
                    continue
                if tuning.model_id and tuning.model_id in self.available_models_dict:
                    tuning.model = self.available_models_dict[tuning.model_id]
                elif tuning.model_id:
                    tuning.model_id = ""
                    tuning.model = ""

            # Reset sampling parameters to YAML defaults
            self._reset_sampling_for_all_agents(inheriting_only=False, include_temperature=False)

            self.backend_healthy = True
            self.model_count = len(self.available_models)
            self.backend_info = f"{self.model_count} models"
            self.add_debug(f"✅ Backend ready (restored: {self.model_count} models)")

            self.backend_initializing = False
            return True  # FAST PATH

        # SLOW PATH: Full initialization
        log_message(f"🔧 Full backend initialization for '{self.backend_type}'...")

        try:
            self.backend_url = config.BACKEND_URLS.get(self.backend_type, config.DEFAULT_OLLAMA_URL)

            self.add_debug(f"🔧 Creating backend: {self.backend_type}")
            log_message(f"   URL: {self.backend_url}")

            self.backend_healthy = True
            self.backend_info = f"{self.backend_type} initializing..."
            self.add_debug(f"⚡ Backend: {self.backend_type} (skip health check)")

            # Load models using centralized discovery module
            from ..lib.model_discovery import discover_models
            try:
                if self.backend_type in LLAMASWAP_BACKENDS:
                    # Beide Backends sind Sichten auf denselben llama-swap-
                    # Katalog: llamacpp zeigt die GGUF-Eintraege, vllm die
                    # "-vllm"-Eintraege (vLLM-Checkpoints als llama-swap-
                    # Eintraege, gestartet/gestoppt von llama-swap selbst).
                    models_dict = discover_models(
                        self.backend_type,
                        backend_url=self.backend_url
                    )
                    if not models_dict:
                        self.add_debug("⚠️ llama-swap not reachable, starting service...")
                        try:
                            import asyncio
                            from ..lib.process_utils import start_llama_swap
                            start_llama_swap()
                            await asyncio.sleep(2.0)
                            models_dict = discover_models(
                                self.backend_type,
                                backend_url=self.backend_url
                            )
                            if models_dict:
                                self.add_debug("✅ llama-swap auto-started")
                        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                            self.add_debug(f"⚠️ Could not start llama-swap: {e}")

                    if models_dict:
                        self.available_models_dict = models_dict
                        self.available_models = list(self.available_models_dict.values())
                        kind = "vLLM" if self.backend_type == "vllm" else "llama.cpp"
                        self.add_debug(f"📂 Found {len(self.available_models)} {kind} models")
                    else:
                        self._clear_models_state()
                        self.add_debug(f"⚠️ llama-swap not reachable at {self.backend_url}")
                        self.add_debug("💡 Install llama-swap service or start manually")

                elif self.backend_type == "cloud_api":
                    provider_config = CLOUD_API_PROVIDERS.get(self.cloud_api_provider)
                    if provider_config:
                        self.cloud_api_key_configured = is_cloud_api_configured(self.cloud_api_provider)
                        if self.cloud_api_key_configured:
                            self.add_debug(f"✅ API key configured ({provider_config['env_key']})")
                            try:
                                from ..backends.cloud_api import CloudAPIBackend, get_cloud_api_key
                                api_key = get_cloud_api_key(self.cloud_api_provider)
                                temp_backend = CloudAPIBackend(
                                    base_url=provider_config["base_url"],
                                    api_key=api_key or "",
                                    provider=self.cloud_api_provider
                                )
                                models = await temp_backend.list_models()
                                await temp_backend.close()

                                if models:
                                    self.available_models_dict = sort_models_grouped({m: m for m in models})
                                    self.available_models = list(self.available_models_dict.values())
                                    self.add_debug(f"☁️ {provider_config['name']}: {len(models)} models loaded")
                                else:
                                    self._clear_models_state()
                                    self.add_debug(f"⚠️ No models returned from {provider_config['name']} API")
                            except (RuntimeError, ValueError, KeyError) as e:
                                self._clear_models_state()
                                self.add_debug(f"⚠️ Failed to fetch models: {e}")
                        else:
                            self._clear_models_state()
                            self.add_debug(f"⚠️ API key missing: Set {provider_config['env_key']} in .env")
                    else:
                        self._clear_models_state()
                        self.add_debug(f"⚠️ Unknown cloud provider: {self.cloud_api_provider}")

                else:
                    # Ollama
                    self.available_models_dict = discover_models(
                        self.backend_type,
                        backend_url=self.backend_url
                    )
                    self.available_models = list(self.available_models_dict.values())

                # Validate and sync aifred_model (model_id is always base ID)
                self.add_debug(f"🔍 Checking: '{self.agent_tuning["aifred"].model_id}' available in {self.backend_type}?")

                if self.agent_tuning["aifred"].model_id in self.available_models_dict:
                    self.agent_tuning["aifred"].model = self._label_for("aifred", self.agent_tuning["aifred"].model_id)
                    self.add_debug(f"✅ Model found: {self.agent_tuning["aifred"].model_id}")
                elif self.available_models_dict:
                    # Stale main model (not pulled / deleted). Clear it instead
                    # of silently substituting another model — the user picks
                    # or pulls one. The guard (available_models_dict non-empty)
                    # means a transient backend outage never wipes the choice.
                    self.add_debug(f"⚠️ '{self.agent_tuning["aifred"].model_id}' not in {self.backend_type} — cleared, pick a model")
                    log_message(f"⚠️ Configured model '{self.agent_tuning["aifred"].model_id}' not available — cleared")
                    self.agent_tuning["aifred"].model_id = ""
                    self.agent_tuning["aifred"].model = ""

                # Validate and sync automatik_model
                if not self.automatik_model_id:
                    pass
                elif self.automatik_model_id in self.available_models_dict:
                    self.automatik_model = self._label_for("automatik", self.automatik_model_id)
                elif self.available_models_dict:
                    log_message(f"⚠️ Configured automatik model '{self.automatik_model_id}' not found, using same as AIfred")
                    self.automatik_model_id = ""
                    self.automatik_model = ""

                # Validate and sync secondary-agent models (Sokrates/Salomo/
                # custom). Clearing the id to "" means "same as AIfred" (the
                # default dropdown option). Only clear when discovery actually
                # returned models (available_models_dict non-empty) — a
                # transient backend outage must not wipe the choice.
                for agent, tuning in self.agent_tuning.items():
                    if agent in ("aifred", "vision"):
                        continue
                    if tuning.model_id and tuning.model_id in self.available_models_dict:
                        tuning.model = self.available_models_dict[tuning.model_id]
                    elif tuning.model_id and self.available_models_dict:
                        log_message(f"⚠️ Configured {agent} model '{tuning.model_id}' not available — reset to 'same as AIfred'")
                        tuning.model_id = ""
                        tuning.model = ""

                self.model_count = len(self.available_models)
                self.backend_info = f"{self.model_count} models"
                self.backend_healthy = True

                # Format model display with calibrated context (SSOT in
                # lib/model_vram_cache — thin wrapper binds backend_type)
                from ..lib.model_vram_cache import (
                    format_model_with_ctx as _format_model_with_ctx,
                    get_llamacpp_calibration,
                    get_ollama_calibrated_max_context,
                    get_rope_factor_for_model,
                )

                def format_model_with_ctx(model_display: str, model_id: str) -> str:
                    return _format_model_with_ctx(model_display, model_id, self.backend_type)

                from ..lib.agent_config import get_agent_label
                # Show effective model (includes TTS variant if GPU TTS is active)
                from ..lib.tts_llm_coordination import get_effective_model_info
                effective_info = get_effective_model_info(self.backend_type)
                base_info = format_model_with_ctx(self.agent_tuning["aifred"].model, self.agent_tuning["aifred"].model_id)
                # If TTS changes the model, show effective info instead
                aifred_display = effective_info if effective_info and effective_info != self.agent_tuning["aifred"].model else base_info

                self.add_debug(f"✅ {len(self.available_models)} models available")
                self.add_debug(f"   {get_agent_label('aifred')}: {aifred_display}")
                if self.automatik_model_id:
                    self.add_debug(f"   \u2728 Automatic: {format_model_with_ctx(self.automatik_model, self.automatik_model_id)}")
                else:
                    self.add_debug("   \u2728 Automatic: (= AIfred)")
                if self.multi_agent_mode != "standard":
                    if self.agent_tuning["sokrates"].model_id:
                        self.add_debug(f"   {get_agent_label('sokrates')}: {format_model_with_ctx(self.agent_tuning["sokrates"].model, self.agent_tuning["sokrates"].model_id)}")
                    if self.agent_tuning["salomo"].model_id:
                        self.add_debug(f"   {get_agent_label('salomo')}: {format_model_with_ctx(self.agent_tuning["salomo"].model, self.agent_tuning["salomo"].model_id)}")

                # Cache min context limit for session-load display
                context_limits = []
                for model_id in [self.agent_tuning["aifred"].model_id, self.agent_tuning["sokrates"].model_id, self.agent_tuning["salomo"].model_id]:
                    if model_id:
                        if self.backend_type == "llamacpp":
                            ctx = get_llamacpp_calibration(model_id)
                        elif self.backend_type == "vllm":
                            from ..lib.operating_points import get_vllm_entry_context
                            ctx = get_vllm_entry_context(model_id)
                        else:
                            ctx = get_ollama_calibrated_max_context(model_id, get_rope_factor_for_model(model_id))
                        if ctx:
                            context_limits.append(ctx)
                self._min_agent_context_limit = min(context_limits) if context_limits else 0

            except Exception as e:  # noqa: BLE001 — boundary of the backend start: the UI shows the error state, the log the traceback
                self.backend_healthy = False
                self.backend_info = f"{self.backend_type} error"
                self.add_debug(f"❌ Model loading failed: {e}")
                log_exception("❌ Model loading failed", e)

            # Check backend capabilities for model switching
            from aifred.backends import BackendFactory
            temp_backend = BackendFactory.create(self.backend_type, base_url=self.backend_url)  # type: ignore[assignment]
            caps = temp_backend.get_capabilities()

            if not caps.get("dynamic_models", True) and self.automatik_model_id and self.automatik_model_id != self.agent_tuning["aifred"].model_id:
                self.automatik_model = ""
                self.automatik_model_id = ""
                self._save_settings()

            # Store in global state
            _global_backend_state["backend_type"] = self.backend_type
            _global_backend_state["backend_url"] = self.backend_url
            _global_backend_state["aifred_model"] = self.agent_tuning["aifred"].model
            _global_backend_state["aifred_model_id"] = self.agent_tuning["aifred"].model_id
            _global_backend_state["automatik_model"] = self.automatik_model
            _global_backend_state["automatik_model_id"] = self.automatik_model_id
            _global_backend_state["available_models"] = self.available_models
            _global_backend_state["available_models_dict"] = self.available_models_dict
            _global_backend_state["current_backend_label"] = self.current_backend_label

            # Load model parameters from cache
            self._load_all_agent_model_params()

            # Reset sampling parameters
            self._reset_sampling_for_all_agents(inheriting_only=False, include_temperature=False)

            # Detect vision models
            self.add_debug("🔍 Detecting vision-capable models...")
            await self._detect_vision_models()
            # Anzeigenamen nachziehen, sonst zeigen die Dropdowns nichts an
            self._sync_agent_display_names()

            # vLLM: Do NOT auto-start server — user triggers calibration manually
            # (like Ollama/llama-swap: discover models first, start on demand)

            _global_backend_state["_init_complete"] = True
            log_message(f"✅ Backend '{self.backend_type}' fully initialized and stored in global state")

            self.backend_initializing = False
            return False  # SLOW PATH

        except Exception as e:  # noqa: BLE001 — boundary of the backend start: the UI shows the error state, the log the traceback
            log_exception("❌ Backend initialization failed", e)
            self.backend_healthy = False
            self.backend_info = f"Error: {e!s}"
            self.add_debug(f"❌ Backend initialization failed: {e}")
            self.backend_initializing = False
            return False

    # ================================================================
    # VISION MODEL DETECTION
    # ================================================================

    def _sync_agent_display_names(self) -> None:
        """``model`` aus ``model_id`` nachziehen — Anzeigename statt blanker ID.

        Die Dropdowns fuehren Anzeige-Labels ("Name (12,3 GB)"), gespeichert
        wird aber die blanke ID. Beim Wiederherstellen der Einstellungen
        landet die ID in BEIDEN Feldern (_backend_mixin, backend_models-
        Schleife) — dann passt der Wert zu keiner Option und das Auswahlfeld
        bleibt LEER, obwohl die Liste gefuellt ist. Genau so gesehen am
        2026-09-01 beim Vision-LLM und zuvor beim AIfred-LLM.

        Der Abgleich ist idempotent: Ist ``model`` bereits ein Label oder die
        ID im Katalog unbekannt, bleibt alles, wie es ist. Vision benutzt
        seinen eigenen Katalog, weil der Describer backend-uebergreifend
        gewaehlt wird (siehe :meth:`_vision_candidate_catalog`).
        """
        haupt = self.available_models_dict or {}
        vision_katalog = self._vision_candidate_catalog() or {}
        for agent, tuning in self.agent_tuning.items():
            mid = getattr(tuning, "model_id", "")
            if not mid:
                continue
            katalog = vision_katalog if agent == "vision" else haupt
            label = katalog.get(mid)
            if label and tuning.model != label:
                tuning.model = label
        if self.automatik_model_id:
            label = haupt.get(self.automatik_model_id)
            if label and self.automatik_model != label:
                self.automatik_model = label

    # ================================================================
    # MODELL-IDENTITAET (SSoT)
    # ================================================================
    # Die Modell-ID ist die einzige Wahrheit. Das Anzeige-Label
    # ("Name (12,3 GB)") ist eine reine Ansicht und wird NIRGENDS
    # gespeichert oder verglichen. Wer ein Label braucht, holt es hier —
    # so kann keine Aufrufstelle mehr den falschen Katalog erwischen.
    # Vorgeschichte: Genau diese Verwechslung trat am 2026-09-01 an FUENF
    # Stellen auf (Wiederherstellung, Erkennung, Zeilenbau, Setter,
    # Aufloesung) und liess das Vision-Dropdown leer.

    def _catalog_for(self, agent: str) -> dict[str, str]:
        """Zustaendiger Katalog (id → Label) fuer einen Agenten.

        Vision ist der Sonderfall: Der Describer wird backend-uebergreifend
        gewaehlt, weil er als Side-Channel neben dem Chat-Modell laeuft.
        Unter vLLM enthaelt die Backend-Modellliste nur ``-vllm``-Eintraege
        und kennt den llama.cpp-Describer nicht.
        """
        if agent == "vision":
            return self._vision_candidate_catalog()
        return self.available_models_dict

    def _label_for(self, agent: str, model_id: str) -> str:
        """Anzeige-Label zu einer Modell-ID; unbekannte Id bleibt sichtbar."""
        if not model_id:
            return ""
        return self._catalog_for(agent).get(model_id, model_id)

    @rx.var(deps=["available_models_dict"], auto_deps=False)
    def available_models_rich(self) -> list[dict[str, str]]:
        """Optionen der Modell-Dropdowns: ``id`` ist der Wert, ``label`` die Anzeige."""
        return [
            {"id": mid, "label": label, "badge": "", "color": ""}
            for mid, label in self.available_models_dict.items()
        ]

    def _vision_candidate_catalog(self) -> dict[str, str]:
        """Katalog der Vision-Kandidaten (id → Anzeige-Label).

        Unter dem vLLM-Backend: die -vllm-Eintraege selbst (ein Chat-LLM
        mit eigenem Encoder beschreibt seine Bilder selbst) plus die
        GGUF-Sicht des llama-swap-Katalogs fuer den Side-Channel-Describer;
        sonst die Backend-Modellliste selbst.
        """
        if self.backend_type == "vllm":
            from ..lib.model_discovery import discover_llamaswap_models
            return {
                **discover_llamaswap_models(self.backend_url, vllm_entries=False),
                **self.available_models_dict,
            }
        return self.available_models_dict

    def _build_vision_rich(
        self, vision_model_ids: list[str]
    ) -> list[dict[str, str]]:
        """Dropdown-Zeilen (Name + Swap-Badge + Farbe) für die Vision-Modelle.

        Gleiche Logik wie die Laufzeitwahl (``vision_routing``):
        ``🧠 Chat-LLM`` — das Modell IST das Hauptmodell und sieht selbst;
        ``⚡ No Swap`` (grün) — eine seiner ``-visiond``-Platzierungen passt
        gerade neben das Geladene (``fitting_visiond``) oder es läuft über den
        Ollama-Seitenkanal; ``🔄 Swap`` (amber) — es verdrängt das
        Chat-LLM. Die Ollama-VLM-Liste wird EINmal geholt.
        """
        from ..lib.ollama_models import list_ollama_vlm_models
        from ..lib.vision_routing import (
            fitting_visiond, same_model, vision_swap_status,
        )
        from ..lib.vision_utils import has_native_vision
        try:
            oll_names = [m.name for m in list_ollama_vlm_models()]
        except (RuntimeError, OSError):
            oll_names = []
        aifred_base = self.agent_tuning["aifred"].model_id or ""

        catalog = self._vision_candidate_catalog()
        rows: list[dict[str, str]] = []
        for mid in vision_model_ids:
            if mid not in catalog:
                continue
            visiond, fit = fitting_visiond(mid)
            if same_model(mid, aifred_base) and has_native_vision(aifred_base):
                badge, color = "🧠 Chat-LLM", "green"
            elif visiond is not None or (
                fit is None
                and vision_swap_status(mid, self.backend_id, ollama_names=oll_names)
            ):
                badge, color = "⚡ No Swap", "green"
            else:
                badge, color = "🔄 Swap", "orange"
            rows.append({"id": mid, "label": catalog[mid], "badge": badge, "color": color})
        return rows

    async def _detect_vision_models(self) -> None:
        """Detect vision-capable models using backend-specific metadata.

        Der Vision-Describer ist ein Side-Channel (llama.cpp ``-visiond``
        bzw. Ollama-Daemon), KEIN Buerger des Haupt-Backends — unter dem
        vLLM-Backend kommen die Kandidaten deshalb backend-uebergreifend
        aus der GGUF-Sicht des llama-swap-Katalogs (sonst waere dort
        nichts auswaehlbar; Peuqui 2026-08-29).
        """
        from . import _global_backend_state
        from ..lib.vision_utils import is_vision_model, is_vision_model_sync

        vision_model_ids: list[str] = []
        candidates: dict[str, str] = self.available_models_dict

        if self.backend_type == "cloud_api":
            for model_id in candidates:
                if is_vision_model_sync(model_id):
                    vision_model_ids.append(model_id)
        elif self.backend_type == "vllm":
            candidates = self._vision_candidate_catalog()
            for model_id in candidates:
                if is_vision_model_sync(model_id):
                    vision_model_ids.append(model_id)
        else:
            for model_id in candidates:
                try:
                    if await is_vision_model(self, model_id):
                        vision_model_ids.append(model_id)
                except (RuntimeError, OSError) as e:
                    log_message(f"⚠️ Vision detection failed for {model_id}: {e}")

        self.vision_models_cache = vision_model_ids
        _global_backend_state["vision_models_cache"] = vision_model_ids

        self.available_vision_models_list = [
            candidates.get(mid, mid) for mid in vision_model_ids
            if mid in candidates
        ]
        _global_backend_state["available_vision_models_list"] = self.available_vision_models_list

        # Swap-Status pro Modell einmal bestimmen (Ollama-VLM-Liste nur EINmal
        # holen und für alle Modelle wiederverwenden — kein Call pro Modell).
        self.available_vision_models_rich = self._build_vision_rich(
            vision_model_ids
        )
        _global_backend_state["available_vision_models_rich"] = (
            self.available_vision_models_rich
        )

        self.add_debug(f"✅ Found {len(vision_model_ids)} vision-capable models")

        # Auto-select vision_model if not set or empty
        if (not self.agent_tuning["vision"].model_id or self.agent_tuning["vision"].model_id.strip() == "") and vision_model_ids:
            self.agent_tuning["vision"].model_id = vision_model_ids[0]
            self.agent_tuning["vision"].model = self._label_for("vision", self.agent_tuning["vision"].model_id)
            self.add_debug(f"⚙️ Auto-selected vision_model: {self.agent_tuning["vision"].model_id}")
            self._save_settings()
        elif self.agent_tuning["vision"].model_id and vision_model_ids:
            if self.agent_tuning["vision"].model_id in vision_model_ids:
                self.agent_tuning["vision"].model = self._label_for("vision", self.agent_tuning["vision"].model_id)
            else:
                self.add_debug(f"⚠️ Saved vision_model '{self.agent_tuning["vision"].model_id}' not found in vision models, auto-selecting...")
                self.agent_tuning["vision"].model_id = vision_model_ids[0]
                self.agent_tuning["vision"].model = self._label_for("vision", self.agent_tuning["vision"].model_id)
                self._save_settings()

        if self.agent_tuning["vision"].model_id:
            from ..lib.agent_config import get_agent_label as _get_label
            self.add_debug(f"   {_get_label('vision')}: {self.agent_tuning["vision"].model}")

        _global_backend_state["vision_model"] = self.agent_tuning["vision"].model
        _global_backend_state["vision_model_id"] = self.agent_tuning["vision"].model_id

    # ================================================================
    # BACKEND CLEANUP
    # ================================================================

    async def _cleanup_old_backend(self, old_backend: str, new_backend: str) -> None:
        """Clean up resources from previous backend before switching."""
        if old_backend == "ollama":
            self.add_debug("🧹 Unloading Ollama models from VRAM...")
            try:
                from ..lib.llm_client import LLMClient
                llm_client = LLMClient(backend_type="ollama", base_url=config.DEFAULT_OLLAMA_URL)
                backend = llm_client._get_backend()

                if hasattr(backend, 'unload_all_models'):
                    _success, unloaded_models = await backend.unload_all_models()
                    count = len(unloaded_models)
                    if count > 0:
                        self.add_debug(f"✅ Unloaded {count} Ollama model(s)")
                    else:
                        self.add_debug("ℹ️ No Ollama models were loaded")
            except (RuntimeError, AttributeError) as e:
                self.add_debug(f"⚠️ Error unloading Ollama models: {e}")

        elif old_backend in LLAMASWAP_BACKENDS:
            # Beide sind Sichten auf denselben llama-swap — beim Wechsel
            # innerhalb der Familie laeuft der Dienst einfach weiter.
            if new_backend in LLAMASWAP_BACKENDS:
                self.add_debug("ℹ️ llama-swap keeps running (same service)")
                return
            self.add_debug("🛑 Stopping llama-swap service...")
            from ..lib.process_utils import stop_llama_swap
            if stop_llama_swap():
                self.add_debug("✅ llama-swap stopped")
            else:
                self.add_debug("⚠️ Could not stop llama-swap")

    # ================================================================
    # BACKEND SWITCHING
    # ================================================================

    @rx.event
    async def switch_backend_by_label(self, label: str):
        """Switch backend using display label (for native mobile select)."""
        label_to_id = {v: k for k, v in self.available_backends_dict.items()}
        backend_id = label_to_id.get(label, label.lower())
        async for _ in self.switch_backend(backend_id):
            yield

    @rx.event
    async def switch_backend(self, new_backend: str):
        """Switch to different backend and restore last used models."""
        from . import _global_backend_state

        if new_backend in ["header_universal", "separator", "header_modern"]:
            return

        if self.backend_switching:
            self.add_debug("⚠️ Backend switch already in progress, please wait...")
            return

        self.backend_switching = True
        yield

        try:
            old_backend = self.backend_type
            self.add_debug(f"🔄 Switching backend from {old_backend} to {new_backend}...")

            self._save_settings()

            await self._cleanup_old_backend(old_backend, new_backend)

            from ..lib.settings import load_settings
            settings = load_settings() or {}
            backend_models = settings.get("backend_models", {})

            target_main_model = None
            target_auto_model = None
            target_vision_model = None
            target_sokrates_model = None
            target_salomo_model = None

            self.add_debug(f"🔍 Settings contains backends: {list(backend_models.keys())}")
            if new_backend in backend_models:
                saved_models = backend_models[new_backend]
                target_main_model = saved_models.get("aifred")
                target_auto_model = saved_models.get("automatik")
                target_vision_model = saved_models.get("vision")
                target_sokrates_model = saved_models.get("sokrates", "")
                target_salomo_model = saved_models.get("salomo", "")
                self.add_debug(f"📝 Loading {new_backend} from settings: AIfred={target_main_model}, Auto={target_auto_model}, Vision={target_vision_model}, Sokrates={target_sokrates_model or '(Main)'}, Salomo={target_salomo_model or '(Main)'}")
            else:
                default_models = config.BACKEND_DEFAULT_MODELS.get(new_backend, {})
                target_main_model = default_models.get("aifred")
                target_auto_model = default_models.get("automatik")
                target_sokrates_model = default_models.get("sokrates", "")
                target_salomo_model = default_models.get("salomo", "")
                self.add_debug(f"📝 No settings for {new_backend}, using config.py defaults: AIfred={target_main_model}, Auto={target_auto_model}")

            if target_main_model:
                self.agent_tuning["aifred"].model = target_main_model
                self.agent_tuning["aifred"].model_id = target_main_model
            if target_auto_model is not None:
                self.automatik_model = target_auto_model
                self.automatik_model_id = target_auto_model
            if target_vision_model:
                self.agent_tuning["vision"].model = target_vision_model
                self.agent_tuning["vision"].model_id = target_vision_model
            self.agent_tuning["sokrates"].model_id = target_sokrates_model or ""
            self.agent_tuning["sokrates"].model = target_sokrates_model or ""
            self.agent_tuning["salomo"].model_id = target_salomo_model or ""
            self.agent_tuning["salomo"].model = target_salomo_model or ""

            self.backend_type = new_backend
            self.backend_id = new_backend
            self.current_backend_label = self.available_backends_dict.get(new_backend, new_backend)
            _global_backend_state["_init_complete"] = False
            await self.initialize_backend()

            self._save_settings()

        finally:
            self.backend_switching = False
            self.add_debug("✅ Backend switch complete")

            from aifred.lib.logging_utils import console_separator
            console_separator()
            self.debug_messages = [*self.debug_messages, "────────────────────"]

            yield

    # ================================================================
    # CLOUD API PROVIDER SWITCHING
    # ================================================================

    @rx.event
    async def set_cloud_api_provider_by_label(self, label: str):
        """Switch Cloud API provider using display label (cloud_api SSOT)."""
        from ..backends.cloud_api import cloud_provider_from_label
        provider_id = cloud_provider_from_label(label)
        async for _ in self.set_cloud_api_provider(provider_id):
            yield

    @rx.event
    async def set_cloud_api_provider(self, provider: str):
        """Switch Cloud API provider (claude, qwen, kimi)."""
        if provider not in CLOUD_API_PROVIDERS:
            self.add_debug(f"⚠️ Unknown cloud provider: {provider}")
            return

        provider_config = CLOUD_API_PROVIDERS[provider]
        self.cloud_api_provider = provider
        self.cloud_api_provider_label = provider_config["name"]

        self.cloud_api_key_configured = is_cloud_api_configured(provider)

        self.add_debug(f"☁️ Switching to {provider_config['name']}...")

        if self.cloud_api_key_configured:
            self.add_debug(f"✅ API key found ({provider_config['env_key']})")
            try:
                from ..backends.cloud_api import CloudAPIBackend, get_cloud_api_key
                api_key = get_cloud_api_key(provider)
                temp_backend = CloudAPIBackend(
                    base_url=provider_config["base_url"],
                    api_key=api_key or "",
                    provider=provider
                )
                models = await temp_backend.list_models()
                await temp_backend.close()

                if models:
                    self.available_models_dict = {m: m for m in models}
                    self.available_models = models.copy()
                    self.add_debug(f"📋 {len(models)} models available")

                    from ..lib.vision_utils import is_vision_model_sync
                    vl_models = [m for m in models if is_vision_model_sync(m)]
                    self.vision_models_cache = vl_models
                    self.available_vision_models_list = vl_models
                    # Cloud-API kennt kein lokales VRAM-Swap — kein Badge.
                    self.available_vision_models_rich = [
                        {"name": m, "badge": "", "color": ""} for m in vl_models
                    ]
                    if vl_models:
                        self.add_debug(f"📷 {len(vl_models)} vision models")

                    default_model = models[0]
                    self.agent_tuning["aifred"].model = default_model
                    self.agent_tuning["aifred"].model_id = default_model
                    self.automatik_model = default_model
                    self.automatik_model_id = default_model

                    if vl_models:
                        self.agent_tuning["vision"].model = vl_models[0]
                        self.agent_tuning["vision"].model_id = vl_models[0]
                    else:
                        self.agent_tuning["vision"].model = ""
                        self.agent_tuning["vision"].model_id = ""
                else:
                    self._clear_models_state(include_vision=True)
                    self.add_debug("⚠️ No models returned from API")
            except Exception as e:  # noqa: BLE001 — boundary of the backend start: the UI shows the error state, the log the traceback
                log_exception("⚠️ Failed to fetch cloud models", e)
                self._clear_models_state(include_vision=True)
                self.add_debug(f"⚠️ Failed to fetch models: {e}")
        else:
            self._clear_models_state(include_vision=True)
            self.add_debug(f"⚠️ Set {provider_config['env_key']} in .env")

        self._save_settings()
        yield

    # ================================================================
    # MODEL SELECTION
    # ================================================================

    @rx.event
    async def set_aifred_model(self, model_id: str):
        """Set selected model and restart backend if needed."""
        from ..lib.vision_utils import is_vision_model

        # Der Select liefert die MODELL-ID (SSoT); das Label ist Ansicht.
        self.agent_tuning["aifred"].model_id = model_id
        self.agent_tuning["aifred"].model = self._label_for("aifred", model_id)
        self.add_debug(f"📝 AIfred-LLM: {self.agent_tuning["aifred"].model}")

        # Die Vision-Swap-Badges hängen vom Chat-LLM ab (welche -vlm-Profile
        # kalibriert sind) → nach dem LLM-Wechsel neu berechnen, sonst zeigt
        # das Dropdown einen stale Swap-Status aus dem alten Chat-Modell.
        if self.vision_models_cache:
            from . import _global_backend_state
            self.available_vision_models_rich = self._build_vision_rich(
                self.vision_models_cache
            )
            _global_backend_state["available_vision_models_rich"] = (
                self.available_vision_models_rich
            )

        # Load model parameters from cache
        from ..lib.runtime_settings import load_agent_model_params
        load_agent_model_params(self, "aifred", self.agent_tuning["aifred"].model_id)

        if self.backend_type in LLAMASWAP_BACKENDS and not self.agent_tuning["aifred"].has_speed_variant:
            self.agent_tuning["aifred"].speed_mode = False

        self._show_model_calibration_info(self.agent_tuning["aifred"].model_id)

        # Check if switching to non-vision model with pending images
        if len(self.pending_images) > 0:
            if not await is_vision_model(self, self._effective_model_id("aifred")):
                self.image_upload_warning = "⚠️ Selected model doesn't support images. Images will be ignored when sending."
            else:
                self.image_upload_warning = ""

        # Reset sampling params to model defaults for all affected agents
        # (AIfred + every agent inheriting its model, custom agents included)
        self._reset_sampling_for_all_agents(inheriting_only=True, include_temperature=True)

        self._save_settings()
        # vLLM-Eintraege laufen ueber llama-swap: Modellwechsel = Swap beim
        # naechsten Request, kein Server-Restart noetig.
        yield

    @rx.event
    async def set_automatik_model(self, model_id: str):
        """Set automatik model for decision and query optimization."""
        # "" ist das Sentinel fuer "wie AIfred-LLM" — es kommt jetzt als
        # leere ID aus dem Select, nicht mehr als uebersetztes Label.
        if model_id == SAME_AS_AIFRED:
            model_id = ""
        self.automatik_model_id = model_id
        self.automatik_model = self._label_for("automatik", model_id)
        self._save_settings()

        if model_id:
            self.add_debug(f"⚡ Automatic-LLM: {self.automatik_model}")
            self._show_model_calibration_info(self.automatik_model_id)
        else:
            self.add_debug("⚡ Automatic-LLM: (same as Main-LLM)")

    @rx.event
    async def set_vision_model(self, model_id: str):
        """Set vision model for OCR/image analysis."""
        self.agent_tuning["vision"].model_id = model_id
        self.agent_tuning["vision"].model = self._label_for("vision", model_id)
        self.add_debug(f"👁️ Vision-LLM: {self.agent_tuning["vision"].model}")
        self._show_model_calibration_info(self.agent_tuning["vision"].model_id)
        # Like every agent (and like the start-up load in _load_all_agent_model_params).
        if self.backend_type in LLAMASWAP_BACKENDS:
            from ..lib.runtime_settings import load_agent_model_params
            load_agent_model_params(self, "vision", model_id)

        if self.backend_type == "llamacpp":
            from ..lib.calibration import model_has_speed_variant
            self.agent_tuning["vision"].has_speed_variant = model_has_speed_variant(self.agent_tuning["vision"].model_id)
            if not self.agent_tuning["vision"].has_speed_variant:
                self.agent_tuning["vision"].speed_mode = False

        self._save_settings()

    # ================================================================
    # BACKEND GUARD
    # ================================================================

    async def _ensure_backend_initialized(self) -> None:
        """Ensure backend is initialized (called from send_message)."""
        if self._backend_initialized:
            return

        log_message("⚠️ Fallback initialization (on_load didn't run)")
        async for _ in self.on_load():
            pass

