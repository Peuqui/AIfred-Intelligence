"""Backend mixin for AIfred state.

Handles backend initialization, switching between backends (Ollama, llama.cpp,
vLLM, TabbyAPI, Cloud APIs), model loading, vision detection, and on_load.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from typing import Dict, List

import reflex as rx

from ..lib import (
    initialize_debug_log,
    log_message,
    set_language,
)
from ..lib import config
from ..lib.config import (
    CLOUD_API_PROVIDERS,
)
from ..backends.cloud_api import is_cloud_api_configured
from ..lib.model_manager import sort_models_grouped, is_backend_compatible
from ..lib.gpu_utils import total_actual_vram_gb
from ..lib.vector_cache import initialize_vector_cache
from ..lib.audio_processing import is_whisper_ready
from ..lib.vllm_manager import vLLMProcessManager

# Module-level globals are in _base.py and re-exported from __init__.py.
# We import them at usage sites to avoid circular imports.


class BackendMixin(rx.State, mixin=True):
    """Mixin for backend initialization, switching, and model management."""

    # ── Backend Settings ──────────────────────────────────────────
    backend_type: str = "ollama"  # "ollama", "vllm", "tabbyapi", "cloud_api"
    backend_id: str = "ollama"  # Pure backend ID (synced with backend_type)
    current_backend_label: str = "Ollama"  # Display label for current backend
    backend_url: str = config.DEFAULT_OLLAMA_URL

    # Cloud API Settings (only relevant when backend_type == "cloud_api")
    cloud_api_provider: str = "qwen"  # "claude", "qwen", "kimi"
    cloud_api_provider_label: str = "Qwen (DashScope)"
    cloud_api_key_configured: bool = False

    # Backend ID/Label Mapping (static - all possible backends)
    available_backends_dict: Dict[str, str] = {
        "ollama": "Ollama",
        "llamacpp": "llama.cpp",
        "tabbyapi": "TabbyAPI",
        "vllm": "vLLM",
        "cloud_api": "Cloud APIs",
    }

    # ── Model Settings ────────────────────────────────────────────
    aifred_model: str = ""
    aifred_model_id: str = ""
    available_models: List[str] = []
    available_models_dict: Dict[str, str] = {}
    vision_models_cache: List[str] = []
    available_vision_models_list: List[str] = []

    automatik_model: str = ""
    automatik_model_id: str = ""
    vision_model: str = ""
    vision_model_id: str = ""

    # ── Backend Status ────────────────────────────────────────────
    backend_healthy: bool = False
    backend_info: str = ""
    model_count: int = 0
    backend_switching: bool = False
    backend_initializing: bool = True
    vllm_restarting: bool = False

    # ── Initialization Flags ──────────────────────────────────────
    _backend_initialized: bool = False
    _model_preloaded: bool = False
    _on_load_running: bool = False

    # ── GPU Detection ─────────────────────────────────────────────
    gpu_detected: bool = False
    gpu_name: str = ""
    gpu_compute_cap: float = 0.0
    gpu_warnings: List[str] = []
    gpu_count: int = 1
    gpu_vram_gb: float = 0
    gpu_all_names: List[str] = []
    available_backends: List[str] = [
        "ollama", "llamacpp", "tabbyapi", "vllm", "cloud_api",
    ]
    available_backends_list: List[str] = [
        "Ollama", "llama.cpp", "TabbyAPI", "vLLM", "Cloud APIs",
    ]

    # ── vLLM YaRN Settings ────────────────────────────────────────
    enable_yarn: bool = False
    yarn_factor: float = 1.0
    yarn_factor_input: str = "1.0"
    yarn_max_factor: float = 0.0
    yarn_max_tested: bool = False
    vllm_max_tokens: int = 0
    vllm_native_context: int = 0

    # ── Cached Model Metadata ─────────────────────────────────────
    _automatik_model_context_limit: int = 0
    _min_agent_context_limit: int = 0

    # ── VRAM-based Context Limit ──────────────────────────────────
    last_vram_limit: int = 0

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
            "tabbyapi": "TabbyAPI",
        }
        names = [display_names.get(b, b) for b in self.available_backends if b != "cloud_api"]
        return ", ".join(names) if names else ""

    @rx.var
    def grouped_backends_display(self) -> List[str]:
        """Return backend list with headers and separators for dropdown display."""
        grouped: list[str] = []
        grouped.append("header_universal")
        if "ollama" in self.available_backends:
            grouped.append("ollama")
        grouped.append("separator")
        grouped.append("header_modern")
        if "tabbyapi" in self.available_backends:
            grouped.append("tabbyapi")
        if "vllm" in self.available_backends:
            grouped.append("vllm")
        return grouped

    @rx.var
    def backend_supports_dynamic_models(self) -> bool:
        """Check if current backend supports dynamic model switching."""
        return self.backend_type != "vllm"

    @rx.var
    def available_vision_models(self) -> List[str]:
        """Filter available_models to only include vision-capable models."""
        return [self.available_models_dict.get(mid, mid) for mid in self.vision_models_cache
                if mid in self.available_models_dict]

    @rx.var
    def backend_label(self) -> str:
        """Get display label for current backend."""
        return self.available_backends_dict.get(self.backend_id, self.backend_id)

    @rx.var
    def available_backends_display(self) -> List[str]:
        """Get list of backend display names (filtered by GPU compatibility)."""
        return [self.available_backends_dict.get(bid, bid) for bid in self.available_backends
                if bid in self.available_backends_dict]

    @rx.var
    def available_backend_ids(self) -> List[str]:
        """Get list of available backend IDs (filtered by GPU compatibility)."""
        return [bid for bid in self.available_backends_dict.keys()
                if bid in self.available_backends]

    @rx.var
    def available_backends_for_select(self) -> List[List[str]]:
        """Get filtered list of [id, label] pairs for native select."""
        return [[bid, label] for bid, label in self.available_backends_dict.items()
                if bid in self.available_backends]

    @rx.var
    def aifred_model_label(self) -> str:
        """Get display label for selected model."""
        return self.available_models_dict.get(self.aifred_model_id, self.aifred_model_id)

    @rx.var
    def automatik_model_label(self) -> str:
        """Get display label for automatik model (empty = same as AIfred)."""
        if not self.automatik_model_id:
            return self.available_models_dict.get(self.aifred_model_id, self.aifred_model_id)
        return self.available_models_dict.get(self.automatik_model_id, self.automatik_model_id)

    @rx.var
    def vision_model_label(self) -> str:
        """Get display label for vision model."""
        return self.available_models_dict.get(self.vision_model_id, self.vision_model_id)

    @rx.var(deps=["available_models", "ui_language"], auto_deps=False)
    def automatik_available_models(self) -> list[str]:
        """Model list with localized '(wie AIfred-LLM)' as first selectable option."""
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined, has-type]
        return [t("sokrates_llm_same", lang=lang)] + list(self.available_models)

    @rx.var(deps=["automatik_model", "ui_language"], auto_deps=False)
    def automatik_model_select_value(self) -> str:
        """Maps empty string (auto) to the localized sentinel label for the select."""
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined, has-type]
        return t("sokrates_llm_same", lang=lang) if self.automatik_model == "" else self.automatik_model

    @property
    def _effective_automatik_id(self) -> str:
        """Resolve Automatik model ID with TTS profile awareness.

        Empty or same base model as AIfred → follow AIfred exactly.
        Different model → still apply TTS profile if GPU TTS is running
        (otherwise the base profile won't fit in VRAM alongside TTS).
        """
        if not self.automatik_model_id:
            return self._effective_model_id("aifred")  # type: ignore[attr-defined, no-any-return]
        if self.automatik_model_id == self.aifred_model_id:
            return self._effective_model_id("aifred")  # type: ignore[attr-defined, no-any-return]
        # Different Automatik model — still needs TTS variant if GPU TTS is
        # actively enabled in the UI. A leftover running container must not
        # override the user's toggle.
        if self.backend_type == "llamacpp" and self.enable_tts:  # type: ignore[attr-defined]
            from ..lib.tts_engine_manager import _detect_running_tts_engine, GPU_ENGINES
            if self.tts_engine in GPU_ENGINES:  # type: ignore[attr-defined]
                running_tts = _detect_running_tts_engine()
                if running_tts:
                    from ..lib.llamacpp_calibration import parse_llamaswap_config
                    from ..lib.config import LLAMASWAP_CONFIG_PATH
                    tts_variant = f"{self.automatik_model_id}-tts-{running_tts}"
                    swap_cfg = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
                    if tts_variant in swap_cfg:
                        return tts_variant
        return self.automatik_model_id

    @rx.var
    def available_models_for_select(self) -> List[List[str]]:
        """Get list of [id, label] pairs for native model select."""
        return [[mid, label] for mid, label in self.available_models_dict.items()]

    @rx.var
    def available_vision_models_for_select(self) -> List[List[str]]:
        """Get list of [id, label] pairs for vision model select."""
        return [[mid, self.available_models_dict[mid]]
                for mid in self.vision_models_cache
                if mid in self.available_models_dict]

    # ================================================================
    # HELPER METHODS
    # ================================================================

    def get_backend_display_label(self, backend_id: str) -> str:
        """Get display label for backend dropdown items."""
        return config.BACKEND_LABELS.get(backend_id, backend_id)

    def _resolve_model_id(self, display_label: str) -> str:
        """Reverse lookup: find model_id (dict key) from display label."""
        for model_id, label in self.available_models_dict.items():
            if label == display_label:
                return model_id
        if display_label in self.available_models_dict:
            return display_label
        return display_label

    def _load_agent_model_params(self, agent: str, model_id: str) -> None:
        """Load model parameters (rope_factor, max_context, etc.) for one agent from cache.

        Works for both ollama and llamacpp backends.
        """
        if not model_id:
            return

        backend = getattr(self, "backend_type", None) or getattr(self, "backend_id", None)

        if backend == "ollama":
            from ..lib.model_vram_cache import get_model_parameters
            params = get_model_parameters(model_id)
            setattr(self, f"{agent}_rope_factor", params["rope_factor"])
            setattr(self, f"{agent}_max_context", params["max_context"])
            setattr(self, f"{agent}_is_hybrid", params["is_hybrid"])
            setattr(self, f"{agent}_supports_thinking", params["supports_thinking"])
        elif backend == "llamacpp":
            from ..lib.model_vram_cache import (
                get_llamacpp_calibration,
                get_thinking_support_for_model,
                get_llamacpp_speed_split,
            )
            setattr(self, f"{agent}_rope_factor", 1.0)
            setattr(self, f"{agent}_max_context", get_llamacpp_calibration(model_id) or 0)
            setattr(self, f"{agent}_is_hybrid", False)
            setattr(self, f"{agent}_supports_thinking", get_thinking_support_for_model(model_id))
            setattr(self, f"{agent}_has_speed_variant", get_llamacpp_speed_split(model_id)[0] > 0)

    def _load_all_agent_model_params(self) -> None:
        """Load model parameters for all agents from cache."""
        backend = getattr(self, "backend_type", None) or getattr(self, "backend_id", None)
        if backend not in ("ollama", "llamacpp"):
            return

        agents_models = [
            ("aifred", self.aifred_model_id),
            ("sokrates", self.sokrates_model_id),  # type: ignore[attr-defined, has-type]
            ("salomo", self.salomo_model_id),  # type: ignore[attr-defined, has-type]
        ]
        if backend == "llamacpp":
            agents_models.append(("vision", self.vision_model_id))

        for agent, model_id in agents_models:
            self._load_agent_model_params(agent, model_id)

    def _clear_models_state(self, include_vision: bool = False) -> None:
        """Reset available models to empty (used on backend/provider switch errors)."""
        self.available_models_dict = {}
        self.available_models = []
        if include_vision:
            self.vision_models_cache = []
            self.available_vision_models_list = []

    # ================================================================
    # ON_LOAD
    # ================================================================

    async def on_load(self):  # noqa: C901
        """Called when page loads - initialize backend and load models.

        Backend is initialized once globally at server startup.
        Page reloads simply restore state from global variables.
        """
        from . import _global_backend_initialized, _global_backend_state

        # Use a mutable reference for the module-level flag
        import aifred.state._base as _base_module

        log_message(f"🔥 on_load() CALLED - Global init: {_global_backend_initialized}, Session init: {self._backend_initialized}")

        # FIRST-TIME GLOBAL INITIALIZATION (once per server start)
        if not _global_backend_initialized:
            _base_module._global_backend_initialized = True  # Set FIRST to prevent ASGI race
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

            from ..lib.config import DEFAULT_LANGUAGE
            set_language(DEFAULT_LANGUAGE)
            log_message(f"🌍 Language mode: {DEFAULT_LANGUAGE}")

            initialize_vector_cache()
            log_message("💾 Vector Cache: Connected")

            # Whisper STT: Docker container (started on demand or always-on)
            if is_whisper_ready():
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
                success, unloaded = await ollama.unload_all_models(wait_for_stability=False)
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

            print("✅ Global initialization complete")

        # PER-SESSION INITIALIZATION (every user/tab/reload)
        if self._on_load_running:
            log_message("⏭️ on_load already running, skipping duplicate call")
            return
        self._on_load_running = True

        # Load session list immediately (before backend init which can take time)
        self.refresh_session_list()  # type: ignore[attr-defined, has-type]

        if not self._backend_initialized:
            log_message("📱 Initializing session...")

            from ..lib.formatting import set_ui_locale
            set_ui_locale(self.ui_language)  # type: ignore[attr-defined, has-type]

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

                # NOTE: research_mode is per-session now, loaded in _restore_session().
                # Here we just keep the class default (DEFAULT_SESSION_CONFIG["research_mode"]).
                from ..lib import TranslationManager
                self.research_mode_display = TranslationManager.get_research_mode_display(self.research_mode, self.ui_language)  # type: ignore[attr-defined, has-type, arg-type]

                self.temperature = saved_settings.get("temperature", self.temperature)  # type: ignore[attr-defined, has-type]
                self.temperature_mode = saved_settings.get("temperature_mode", self.temperature_mode)  # type: ignore[attr-defined, has-type]
                self.sokrates_temperature = saved_settings.get("sokrates_temperature", self.sokrates_temperature)  # type: ignore[attr-defined, has-type]
                self.sokrates_temperature_offset = saved_settings.get("sokrates_temperature_offset", self.sokrates_temperature_offset)  # type: ignore[attr-defined, has-type]
                self.salomo_temperature = saved_settings.get("salomo_temperature", self.salomo_temperature)  # type: ignore[attr-defined, has-type]
                self.salomo_temperature_offset = saved_settings.get("salomo_temperature_offset", self.salomo_temperature_offset)  # type: ignore[attr-defined, has-type]
                # Load UI language and update global locale + prompt language
                saved_ui_lang = saved_settings.get("ui_language", self.ui_language)  # type: ignore[attr-defined, has-type]
                if saved_ui_lang in ["de", "en"]:
                    self.ui_language = saved_ui_lang  # type: ignore[attr-defined, has-type]
                    set_ui_locale(saved_ui_lang)
                    set_language(saved_ui_lang)

                # Load user name and gender
                self.user_name = saved_settings.get("user_name", self.user_name)  # type: ignore[attr-defined, has-type]
                self.user_gender = saved_settings.get("user_gender", self.user_gender)  # type: ignore[attr-defined, has-type]
                from ..lib.prompt_loader import set_user_name, set_user_gender, init_system_prompt_cache, set_personality_enabled, set_reasoning_enabled
                set_user_name(self.user_name)  # type: ignore[attr-defined, has-type, arg-type]
                set_user_gender(self.user_gender)  # type: ignore[attr-defined, has-type, arg-type]

                # Load and sync personality toggles
                self.aifred_personality = saved_settings.get("aifred_personality", self.aifred_personality)  # type: ignore[attr-defined, has-type]
                self.sokrates_personality = saved_settings.get("sokrates_personality", self.sokrates_personality)  # type: ignore[attr-defined, has-type]
                self.salomo_personality = saved_settings.get("salomo_personality", self.salomo_personality)  # type: ignore[attr-defined, has-type]
                self.vision_personality = saved_settings.get("vision_personality", self.vision_personality)  # type: ignore[attr-defined, has-type]
                set_personality_enabled("aifred", self.aifred_personality)  # type: ignore[attr-defined, has-type, arg-type]
                set_personality_enabled("sokrates", self.sokrates_personality)  # type: ignore[attr-defined, has-type, arg-type]
                set_personality_enabled("salomo", self.salomo_personality)  # type: ignore[attr-defined, has-type, arg-type]
                set_personality_enabled("vision", self.vision_personality)  # type: ignore[attr-defined, has-type, arg-type]

                if "aifred_personality" not in saved_settings or "vision_personality" not in saved_settings:
                    self._save_personality_settings()  # type: ignore[attr-defined, has-type]

                # Load and sync reasoning toggles
                self.aifred_reasoning = saved_settings.get("aifred_reasoning", self.aifred_reasoning)  # type: ignore[attr-defined, has-type]
                self.sokrates_reasoning = saved_settings.get("sokrates_reasoning", self.sokrates_reasoning)  # type: ignore[attr-defined, has-type]
                self.salomo_reasoning = saved_settings.get("salomo_reasoning", self.salomo_reasoning)  # type: ignore[attr-defined, has-type]
                self.vision_reasoning = saved_settings.get("vision_reasoning", self.vision_reasoning)  # type: ignore[attr-defined, has-type]
                set_reasoning_enabled("aifred", self.aifred_reasoning)  # type: ignore[attr-defined, has-type, arg-type]
                set_reasoning_enabled("sokrates", self.sokrates_reasoning)  # type: ignore[attr-defined, has-type, arg-type]
                set_reasoning_enabled("salomo", self.salomo_reasoning)  # type: ignore[attr-defined, has-type, arg-type]
                set_reasoning_enabled("vision", self.vision_reasoning)  # type: ignore[attr-defined, has-type, arg-type]

                if "aifred_reasoning" not in saved_settings or "vision_reasoning" not in saved_settings:
                    self._save_reasoning_settings()  # type: ignore[attr-defined, has-type]

                # Load thinking toggles (read directly from State at runtime, no prompt_loader sync)
                self.aifred_thinking = saved_settings.get("aifred_thinking", self.aifred_thinking)  # type: ignore[attr-defined, has-type]
                self.sokrates_thinking = saved_settings.get("sokrates_thinking", self.sokrates_thinking)  # type: ignore[attr-defined, has-type]
                self.salomo_thinking = saved_settings.get("salomo_thinking", self.salomo_thinking)  # type: ignore[attr-defined, has-type]
                self.vision_thinking = saved_settings.get("vision_thinking", self.vision_thinking)  # type: ignore[attr-defined, has-type]

                if "aifred_thinking" not in saved_settings or "vision_thinking" not in saved_settings:
                    self._save_thinking_settings()  # type: ignore[attr-defined, has-type]

                # Load speed mode toggles (llamacpp only)
                self.aifred_speed_mode = saved_settings.get("aifred_speed_mode", False)  # type: ignore[attr-defined, has-type]
                self.sokrates_speed_mode = saved_settings.get("sokrates_speed_mode", False)  # type: ignore[attr-defined, has-type]
                self.salomo_speed_mode = saved_settings.get("salomo_speed_mode", False)  # type: ignore[attr-defined, has-type]
                self.vision_speed_mode = saved_settings.get("vision_speed_mode", False)  # type: ignore[attr-defined, has-type]

                # Load Vision settings (PERSISTENT)
                self.vision_num_ctx_enabled = saved_settings.get("vision_num_ctx_enabled", self.vision_num_ctx_enabled)  # type: ignore[attr-defined, has-type]
                self.vision_num_ctx = saved_settings.get("vision_num_ctx", self.vision_num_ctx)  # type: ignore[attr-defined, has-type]

                init_system_prompt_cache()

                # Load TTS/STT Settings
                self.enable_tts = saved_settings.get("enable_tts", self.enable_tts)  # type: ignore[attr-defined, has-type]
                saved_engine = saved_settings.get("tts_engine", self.tts_engine)  # type: ignore[attr-defined, has-type]
                if saved_engine and len(saved_engine) > 10:
                    engine_map = {"XTTS": "xtts", "MOSS": "moss", "DashScope": "dashscope",
                                  "Piper": "piper", "eSpeak": "espeak", "Edge": "edge"}
                    for name, key in engine_map.items():
                        if name in saved_engine:
                            saved_engine = key
                            break
                self.tts_engine = saved_engine  # type: ignore[attr-defined, has-type]
                self.xtts_force_cpu = saved_settings.get("xtts_force_cpu", self.xtts_force_cpu)  # type: ignore[attr-defined, has-type]
                # tts_autoplay/tts_streaming_enabled: loaded per-engine by _restore_tts_toggles_for_engine below
                self.tts_playback_rate = saved_settings.get("tts_playback_rate", self.tts_playback_rate)  # type: ignore[attr-defined, has-type]
                self.tts_pitch = saved_settings.get("tts_pitch", self.tts_pitch)  # type: ignore[attr-defined, has-type]
                saved_whisper = saved_settings.get("whisper_model", self.whisper_model_key)  # type: ignore[attr-defined, has-type]
                if "(" in saved_whisper:  # type: ignore[operator]
                    saved_whisper = saved_whisper.split("(")[0].strip()  # type: ignore[union-attr]
                self.whisper_model_key = saved_whisper  # type: ignore[attr-defined, has-type]
                self.show_transcription = saved_settings.get("show_transcription", self.show_transcription)  # type: ignore[attr-defined, has-type]

                # Load TTS voice
                user_voices = saved_settings.get("tts_voices_per_language", {})
                engine_key = self._get_engine_key()  # type: ignore[attr-defined, has-type]
                saved_voice = user_voices.get(engine_key, {}).get(self.ui_language)  # type: ignore[attr-defined, has-type]
                if saved_voice:
                    self.tts_voice = saved_voice  # type: ignore[attr-defined, has-type]
                else:
                    self.tts_voice = saved_settings.get("voice", self.tts_voice)  # type: ignore[attr-defined, has-type]

                self._restore_agent_voices_for_engine(engine_key)  # type: ignore[attr-defined, has-type]
                self._restore_tts_toggles_for_engine(engine_key)  # type: ignore[attr-defined, has-type]

                if self.tts_engine == "xtts":  # type: ignore[attr-defined, has-type]
                    self._refresh_xtts_voices()  # type: ignore[attr-defined, has-type]

                # Load vLLM YaRN Settings
                self.enable_yarn = saved_settings.get("enable_yarn", self.enable_yarn)
                self.yarn_factor = 1.0
                self.yarn_factor_input = "1.0"

                # Load UI Settings
                self.auto_refresh_enabled = saved_settings.get("auto_scroll", self.auto_refresh_enabled)  # type: ignore[attr-defined, has-type]

                # Load Multi-Agent Settings
                self.multi_agent_mode = saved_settings.get("multi_agent_mode", self.multi_agent_mode)  # type: ignore[attr-defined, has-type]
                self.max_debate_rounds = saved_settings.get("max_debate_rounds", self.max_debate_rounds)  # type: ignore[attr-defined, has-type]
                self.consensus_type = saved_settings.get("consensus_type", self.consensus_type)  # type: ignore[attr-defined, has-type]
                self.sokrates_model_id = saved_settings.get("sokrates_model", "")  # type: ignore[attr-defined, has-type]
                self.sokrates_model = self.sokrates_model_id  # type: ignore[attr-defined, has-type]
                self.salomo_model_id = saved_settings.get("salomo_model", "")  # type: ignore[attr-defined, has-type]
                self.salomo_model = self.salomo_model_id  # type: ignore[attr-defined, has-type]
                self.salomo_temperature = saved_settings.get("salomo_temperature", self.salomo_temperature)  # type: ignore[attr-defined, has-type]
                self.salomo_temperature_offset = saved_settings.get("salomo_temperature_offset", self.salomo_temperature_offset)  # type: ignore[attr-defined, has-type]

                # Load per-backend models
                backend_models = saved_settings.get("backend_models", {})
                _had_backend_settings = self.backend_id in backend_models
                if _had_backend_settings:
                    backend_data = backend_models[self.backend_id]
                    selected_raw = backend_data.get("aifred_model", "")
                    automatik_raw = backend_data.get("automatik_model", "")
                    vision_raw = backend_data.get("vision_model", "")
                    sokrates_raw = backend_data.get("sokrates_model", "")
                    salomo_raw = backend_data.get("salomo_model", "")

                    self.aifred_model_id = selected_raw
                    self.automatik_model_id = automatik_raw
                    self.vision_model_id = vision_raw
                    self.sokrates_model_id = sokrates_raw  # type: ignore[attr-defined, has-type]
                    self.salomo_model_id = salomo_raw  # type: ignore[attr-defined, has-type]

                    # Load all model parameters from cache on startup
                    self._load_all_agent_model_params()

                    self.aifred_model = selected_raw
                    self.automatik_model = automatik_raw
                    self.vision_model = vision_raw
                    self.sokrates_model = sokrates_raw  # type: ignore[attr-defined, has-type]
                    self.salomo_model = salomo_raw  # type: ignore[attr-defined, has-type]

                self.add_debug(f"⚙️ Settings loaded (backend: {self.backend_type})")  # type: ignore[attr-defined, has-type]

                # Send TTS playback rate to JavaScript
                rate_value = self.tts_playback_rate.replace("x", "")  # type: ignore[attr-defined, has-type, union-attr]
                yield rx.call_script(f"setTimeout(() => {{ if (typeof setTtsPlaybackRate === 'function') setTtsPlaybackRate({rate_value}); }}, 100)")

            # Apply config.py defaults as final fallback
            backend_defaults = config.BACKEND_DEFAULT_MODELS.get(self.backend_type, {})

            if not self.aifred_model:
                self.aifred_model = backend_defaults.get("aifred_model", "")
                self.aifred_model_id = self.aifred_model
                if self.aifred_model:
                    self.add_debug(f"⚙️ Using default aifred_model from config.py: {self.aifred_model}")  # type: ignore[attr-defined, has-type]
                else:
                    self.add_debug("⚠️ No aifred_model configured")  # type: ignore[attr-defined, has-type]

            if not self.automatik_model and not _had_backend_settings:
                self.automatik_model = backend_defaults.get("automatik_model", "")
                self.automatik_model_id = self.automatik_model
                if self.automatik_model:
                    self.add_debug(f"⚙️ Using default automatik_model from config.py: {self.automatik_model}")  # type: ignore[attr-defined, has-type]
                else:
                    self.add_debug("⚠️ No automatik_model configured")  # type: ignore[attr-defined, has-type]

            if not self.vision_model:
                self.vision_model = backend_defaults.get("vision_model", "")
                self.vision_model_id = self.vision_model
                if self.vision_model:
                    self.add_debug(f"⚙️ Using default vision_model from config.py: {self.vision_model}")  # type: ignore[attr-defined, has-type]
                else:
                    self.add_debug("ℹ️ No vision_model configured - will auto-detect first available vision model")  # type: ignore[attr-defined, has-type]

            # Multi-Agent Models (optional)
            # Only apply config.py defaults if NO per-backend settings exist.
            # Empty string ("") is a valid saved value meaning "use AIfred-LLM".
            if not _had_backend_settings and not self.sokrates_model_id:  # type: ignore[attr-defined, has-type]
                self.sokrates_model_id = backend_defaults.get("sokrates_model", "")  # type: ignore[attr-defined, has-type]
                self.sokrates_model = self.sokrates_model_id  # type: ignore[attr-defined, has-type]
                if self.sokrates_model_id:  # type: ignore[attr-defined, has-type]
                    self.add_debug(f"⚙️ Using default sokrates_model from config.py: {self.sokrates_model_id}")  # type: ignore[attr-defined, has-type]

            if not _had_backend_settings and not self.salomo_model_id:  # type: ignore[attr-defined, has-type]
                self.salomo_model_id = backend_defaults.get("salomo_model", "")  # type: ignore[attr-defined, has-type]
                self.salomo_model = self.salomo_model_id  # type: ignore[attr-defined, has-type]
                if self.salomo_model_id:  # type: ignore[attr-defined, has-type]
                    self.add_debug(f"⚙️ Using default salomo_model from config.py: {self.salomo_model_id}")  # type: ignore[attr-defined, has-type]

            # vLLM and TabbyAPI can only load ONE model at a time
            if self.backend_type in ["vllm", "tabbyapi"]:
                if self.automatik_model != self.aifred_model:
                    self.add_debug(f"⚠️ {self.backend_type} can only load one model - using {self.aifred_model} for both AIfred and Automatic")  # type: ignore[attr-defined, has-type]
                    self.automatik_model = self.aifred_model

            # Generate internal session ID
            if not self.session_id:  # type: ignore[attr-defined, has-type]
                self.session_id = str(uuid.uuid4())  # type: ignore[attr-defined, has-type]

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

                self.add_debug(f"🎮 GPU: {self.gpu_display_text}")  # type: ignore[attr-defined, has-type]

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
                    self.add_debug(f"✅ Compatible backends: {', '.join(self.available_backends)}")  # type: ignore[attr-defined, has-type]

                    if self.backend_type not in self.available_backends:
                        old_backend = self.backend_type
                        self.backend_type = self.available_backends[0]
                        self.backend_id = self.backend_type
                        self.add_debug(f"⚠️ Backend '{old_backend}' not compatible with {gpu_info.name}")  # type: ignore[attr-defined, has-type]
                        self.add_debug(f"🔄 Auto-switched to '{self.backend_type}'")  # type: ignore[attr-defined, has-type]

                    self.current_backend_label = self.available_backends_dict.get(self.backend_id, self.backend_id)
                    _global_backend_state["current_backend_label"] = self.current_backend_label

            # Initialize backend (or restore from global state)
            self.add_debug("🔧 Initializing backend...")  # type: ignore[attr-defined, has-type]
            backend_init_success = False
            was_fast_path = False
            try:
                was_fast_path = await self.initialize_backend()
                backend_init_success = True
            except Exception as e:
                self.add_debug(f"❌ Backend init failed: {e}")  # type: ignore[attr-defined, has-type]
                log_message(f"❌ Backend init failed: {e}")
                import traceback
                log_message(traceback.format_exc())

            if backend_init_success and not was_fast_path:
                self.add_debug("✅ Backend ready")  # type: ignore[attr-defined, has-type]

            if backend_init_success:
                from aifred.lib.logging_utils import console_separator
                console_separator()
                self.debug_messages.append("────────────────────")  # type: ignore[attr-defined, has-type]

            self._backend_initialized = True
            log_message("✅ Session initialization complete")

            # Authentication: Read username from cookie
            if not self._session_initialized:  # type: ignore[attr-defined, has-type]
                from ..lib.browser_storage import get_username_script
                log_message("🔐 Requesting username cookie from browser...")
                # Import AIState at usage site to avoid circular import
                from ._base import AIState
                yield rx.call_script(
                    get_username_script(),
                    callback=AIState.handle_username_loaded
                )

        self._on_load_running = False

    # ================================================================
    # INITIALIZE BACKEND
    # ================================================================

    async def initialize_backend(self) -> bool:  # noqa: C901
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

            self.available_backends = _global_backend_state.get("available_backends", self.available_backends)
            self.available_backends_list = _global_backend_state.get("available_backends_list", self.available_backends_list)
            self.current_backend_label = _global_backend_state.get("current_backend_label",
                self.available_backends_dict.get(self.backend_type, self.backend_type))

            # Validate and sync aifred_model (model_id is always base ID)
            if self.aifred_model_id and self.aifred_model_id in self.available_models_dict:
                self.aifred_model = self.available_models_dict[self.aifred_model_id]
            elif _global_backend_state.get("aifred_model_id") in self.available_models_dict:
                self.aifred_model_id = _global_backend_state["aifred_model_id"]
                self.aifred_model = self.available_models_dict[self.aifred_model_id]
            else:
                first_id = next(iter(self.available_models_dict.keys()), "")
                self.aifred_model_id = first_id
                self.aifred_model = self.available_models_dict.get(first_id, first_id)

            # Validate and sync automatik_model
            if not self.automatik_model_id:
                self.automatik_model = ""
            elif self.automatik_model_id in self.available_models_dict:
                self.automatik_model = self.available_models_dict[self.automatik_model_id]
            elif _global_backend_state.get("automatik_model_id") in self.available_models_dict:
                self.automatik_model_id = _global_backend_state["automatik_model_id"]
                self.automatik_model = self.available_models_dict[self.automatik_model_id]
            else:
                log_message(f"⚠️ Configured automatik model '{self.automatik_model_id}' not found, using same as AIfred")
                self.automatik_model_id = ""
                self.automatik_model = ""

            # Validate and sync vision_model
            if self.vision_model_id and self.vision_model_id in self.vision_models_cache:
                self.vision_model = self.available_models_dict.get(self.vision_model_id, self.vision_model_id)
            elif _global_backend_state.get("vision_model_id") in self.vision_models_cache:
                self.vision_model_id = _global_backend_state["vision_model_id"]
                self.vision_model = self.available_models_dict.get(self.vision_model_id, self.vision_model_id)
            elif self.vision_models_cache:
                self.vision_model_id = self.vision_models_cache[0]
                self.vision_model = self.available_models_dict.get(self.vision_model_id, self.vision_model_id)

            # Validate and sync sokrates_model
            if self.sokrates_model_id and self.sokrates_model_id in self.available_models_dict:  # type: ignore[attr-defined, has-type]
                self.sokrates_model = self.available_models_dict[self.sokrates_model_id]  # type: ignore[attr-defined, has-type]
            elif self.sokrates_model_id:  # type: ignore[attr-defined, has-type]
                self.sokrates_model_id = ""  # type: ignore[attr-defined, has-type]
                self.sokrates_model = ""  # type: ignore[attr-defined, has-type]

            # Validate and sync salomo_model
            if self.salomo_model_id and self.salomo_model_id in self.available_models_dict:  # type: ignore[attr-defined, has-type]
                self.salomo_model = self.available_models_dict[self.salomo_model_id]  # type: ignore[attr-defined, has-type]
            elif self.salomo_model_id:  # type: ignore[attr-defined, has-type]
                self.salomo_model_id = ""  # type: ignore[attr-defined, has-type]
                self.salomo_model = ""  # type: ignore[attr-defined, has-type]

            # vLLM can only load ONE model
            if self.backend_type == "vllm" and self.automatik_model_id:
                self.automatik_model = ""
                self.automatik_model_id = ""
                _global_backend_state["automatik_model"] = ""
                _global_backend_state["automatik_model_id"] = ""
                self._save_settings()  # type: ignore[attr-defined, has-type]

            # Check vLLM manager status
            if self.backend_type == "vllm":
                vllm_manager = _global_backend_state.get("vllm_manager")
                if vllm_manager and vllm_manager.is_running():
                    self.add_debug("✅ vLLM server already running (restored from global state)")  # type: ignore[attr-defined, has-type]
                else:
                    self.add_debug("⚠️ vLLM manager exists but server not running")  # type: ignore[attr-defined, has-type]

            # Reset sampling parameters to YAML defaults
            for agent in ["aifred", "sokrates", "salomo"]:
                self._reset_agent_sampling(agent, include_temperature=False)  # type: ignore[attr-defined]

            self.backend_healthy = True
            self.model_count = len(self.available_models)
            self.backend_info = f"{self.model_count} models"
            self.add_debug(f"✅ Backend ready (restored: {self.model_count} models)")  # type: ignore[attr-defined, has-type]

            self.backend_initializing = False
            return True  # FAST PATH

        # SLOW PATH: Full initialization
        log_message(f"🔧 Full backend initialization for '{self.backend_type}'...")

        try:
            self.backend_url = config.BACKEND_URLS.get(self.backend_type, config.DEFAULT_OLLAMA_URL)

            self.add_debug(f"🔧 Creating backend: {self.backend_type}")  # type: ignore[attr-defined, has-type]
            log_message(f"   URL: {self.backend_url}")

            self.backend_healthy = True
            self.backend_info = f"{self.backend_type} initializing..."
            self.add_debug(f"⚡ Backend: {self.backend_type} (skip health check)")  # type: ignore[attr-defined, has-type]

            # Load models using centralized discovery module
            from ..lib.model_discovery import discover_models
            try:
                if self.backend_type in ["vllm", "tabbyapi"]:
                    self.available_models_dict = discover_models(
                        self.backend_type,
                        is_compatible_fn=is_backend_compatible
                    )
                    self.available_models = list(self.available_models_dict.values())
                    self.add_debug(f"📂 Found {len(self.available_models)} {self.backend_type}-compatible models")  # type: ignore[attr-defined, has-type]

                elif self.backend_type == "llamacpp":
                    models_dict = discover_models(
                        self.backend_type,
                        backend_url=self.backend_url
                    )
                    if not models_dict:
                        self.add_debug("⚠️ llama-swap not reachable, starting service...")  # type: ignore[attr-defined, has-type]
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
                                self.add_debug("✅ llama-swap auto-started")  # type: ignore[attr-defined, has-type]
                        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                            self.add_debug(f"⚠️ Could not start llama-swap: {e}")  # type: ignore[attr-defined, has-type]

                    if models_dict:
                        self.available_models_dict = models_dict
                        self.available_models = list(self.available_models_dict.values())
                        self.add_debug(f"📂 Found {len(self.available_models)} llama.cpp models")  # type: ignore[attr-defined, has-type]
                    else:
                        self._clear_models_state()
                        self.add_debug(f"⚠️ llama-swap not reachable at {self.backend_url}")  # type: ignore[attr-defined, has-type]
                        self.add_debug("💡 Install llama-swap service or start manually")  # type: ignore[attr-defined, has-type]

                elif self.backend_type == "cloud_api":
                    provider_config = CLOUD_API_PROVIDERS.get(self.cloud_api_provider)
                    if provider_config:
                        self.cloud_api_key_configured = is_cloud_api_configured(self.cloud_api_provider)
                        if self.cloud_api_key_configured:
                            self.add_debug(f"✅ API key configured ({provider_config['env_key']})")  # type: ignore[attr-defined, has-type]
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
                                    self.add_debug(f"☁️ {provider_config['name']}: {len(models)} models loaded")  # type: ignore[attr-defined, has-type]
                                else:
                                    self._clear_models_state()
                                    self.add_debug(f"⚠️ No models returned from {provider_config['name']} API")  # type: ignore[attr-defined, has-type]
                            except (RuntimeError, ValueError, KeyError) as e:
                                self._clear_models_state()
                                self.add_debug(f"⚠️ Failed to fetch models: {e}")  # type: ignore[attr-defined, has-type]
                        else:
                            self._clear_models_state()
                            self.add_debug(f"⚠️ API key missing: Set {provider_config['env_key']} in .env")  # type: ignore[attr-defined, has-type]
                    else:
                        self._clear_models_state()
                        self.add_debug(f"⚠️ Unknown cloud provider: {self.cloud_api_provider}")  # type: ignore[attr-defined, has-type]

                else:
                    # Ollama
                    self.available_models_dict = discover_models(
                        self.backend_type,
                        backend_url=self.backend_url
                    )
                    self.available_models = list(self.available_models_dict.values())

                # Validate and sync aifred_model (model_id is always base ID)
                self.add_debug(f"🔍 Checking: '{self.aifred_model_id}' available in {self.backend_type}?")  # type: ignore[attr-defined, has-type]

                if self.aifred_model_id in self.available_models_dict:
                    self.aifred_model = self.available_models_dict[self.aifred_model_id]
                    self.add_debug(f"✅ Model found: {self.aifred_model_id}")  # type: ignore[attr-defined, has-type]
                elif self.available_models_dict:
                    first_id = next(iter(self.available_models_dict.keys()))
                    self.add_debug(f"⚠️ '{self.aifred_model_id}' not in {self.backend_type}! Using: '{first_id}'")  # type: ignore[attr-defined, has-type]
                    log_message(f"⚠️ Configured model '{self.aifred_model_id}' not found, using '{first_id}'")
                    self.aifred_model_id = first_id
                    self.aifred_model = self.available_models_dict[first_id]

                # Validate and sync automatik_model
                if not self.automatik_model_id:
                    pass
                elif self.automatik_model_id in self.available_models_dict:
                    self.automatik_model = self.available_models_dict[self.automatik_model_id]
                elif self.available_models_dict:
                    log_message(f"⚠️ Configured automatik model '{self.automatik_model_id}' not found, using same as AIfred")
                    self.automatik_model_id = ""
                    self.automatik_model = ""

                # Validate and sync sokrates_model
                if self.sokrates_model_id and self.sokrates_model_id in self.available_models_dict:  # type: ignore[attr-defined, has-type]
                    self.sokrates_model = self.available_models_dict[self.sokrates_model_id]  # type: ignore[attr-defined, has-type]
                elif self.sokrates_model_id:  # type: ignore[attr-defined, has-type]
                    log_message(f"⚠️ Configured sokrates model '{self.sokrates_model_id}' not found, clearing")  # type: ignore[attr-defined, has-type]
                    self.sokrates_model_id = ""  # type: ignore[attr-defined, has-type]
                    self.sokrates_model = ""  # type: ignore[attr-defined, has-type]

                # Validate and sync salomo_model
                if self.salomo_model_id and self.salomo_model_id in self.available_models_dict:  # type: ignore[attr-defined, has-type]
                    self.salomo_model = self.available_models_dict[self.salomo_model_id]  # type: ignore[attr-defined, has-type]
                elif self.salomo_model_id:  # type: ignore[attr-defined, has-type]
                    log_message(f"⚠️ Configured salomo model '{self.salomo_model_id}' not found, clearing")  # type: ignore[attr-defined, has-type]
                    self.salomo_model_id = ""  # type: ignore[attr-defined, has-type]
                    self.salomo_model = ""  # type: ignore[attr-defined, has-type]

                self.model_count = len(self.available_models)
                self.backend_info = f"{self.model_count} models"
                self.backend_healthy = True

                # Format model display with calibrated context
                from ..lib.model_vram_cache import get_ollama_calibrated_max_context, get_rope_factor_for_model, get_llamacpp_calibration
                from ..lib.formatting import format_number

                def format_model_with_ctx(model_display: str, model_id: str) -> str:
                    """Format model display with calibrated context limit."""
                    if not model_id:
                        return model_display
                    if self.backend_type == "llamacpp":
                        ctx = get_llamacpp_calibration(model_id)
                    else:
                        ctx = get_ollama_calibrated_max_context(model_id, get_rope_factor_for_model(model_id))
                    if ctx:
                        if model_display.endswith(")"):
                            return model_display[:-1] + f", {format_number(ctx)} ctx)"
                        return f"{model_display} ({format_number(ctx)} ctx)"
                    else:
                        if model_display.endswith(")"):
                            return model_display[:-1] + ", ctx not calibrated)"
                        return f"{model_display} (ctx not calibrated)"

                from ..lib.agent_config import get_agent_label
                # Show effective model (includes TTS variant if GPU TTS is active)
                from ..lib.tts_engine_manager import get_effective_model_info
                effective_info = get_effective_model_info(self.backend_type)
                base_info = format_model_with_ctx(self.aifred_model, self.aifred_model_id)
                # If TTS changes the model, show effective info instead
                aifred_display = effective_info if effective_info and effective_info != self.aifred_model else base_info

                self.add_debug(f"✅ {len(self.available_models)} models available")  # type: ignore[attr-defined, has-type]
                self.add_debug(f"   {get_agent_label('aifred')}: {aifred_display}")  # type: ignore[attr-defined, has-type]
                if self.backend_type.lower() not in ["vllm", "tabbyapi"]:
                    if self.automatik_model_id:
                        self.add_debug(f"   \u2728 Automatic: {format_model_with_ctx(self.automatik_model, self.automatik_model_id)}")  # type: ignore[attr-defined, has-type]
                    else:
                        self.add_debug("   \u2728 Automatic: (= AIfred)")  # type: ignore[attr-defined, has-type]
                    if self.multi_agent_mode != "standard":  # type: ignore[attr-defined, has-type]
                        if self.sokrates_model_id:  # type: ignore[attr-defined, has-type]
                            self.add_debug(f"   {get_agent_label('sokrates')}: {format_model_with_ctx(self.sokrates_model, self.sokrates_model_id)}")  # type: ignore[attr-defined, has-type]
                        if self.salomo_model_id:  # type: ignore[attr-defined, has-type]
                            self.add_debug(f"   {get_agent_label('salomo')}: {format_model_with_ctx(self.salomo_model, self.salomo_model_id)}")  # type: ignore[attr-defined, has-type]

                # Cache min context limit for session-load display
                context_limits = []
                for model_id in [self.aifred_model_id, self.sokrates_model_id, self.salomo_model_id]:  # type: ignore[attr-defined, has-type]
                    if model_id:
                        if self.backend_type == "llamacpp":
                            ctx = get_llamacpp_calibration(model_id)
                        else:
                            ctx = get_ollama_calibrated_max_context(model_id, get_rope_factor_for_model(model_id))
                        if ctx:
                            context_limits.append(ctx)
                self._min_agent_context_limit = min(context_limits) if context_limits else 0

            except Exception as e:
                self.backend_healthy = False
                self.backend_info = f"{self.backend_type} error"
                self.add_debug(f"❌ Model loading failed: {e}")  # type: ignore[attr-defined, has-type]
                log_message(f"❌ Model loading failed: {e}")

            # Check backend capabilities for model switching
            from aifred.backends import BackendFactory
            temp_backend = BackendFactory.create(self.backend_type, base_url=self.backend_url)  # type: ignore[assignment]
            caps = temp_backend.get_capabilities()

            if not caps.get("dynamic_models", True) and self.automatik_model_id and self.automatik_model_id != self.aifred_model_id:
                self.automatik_model = ""
                self.automatik_model_id = ""
                self._save_settings()  # type: ignore[attr-defined, has-type]

            # Store in global state
            _global_backend_state["backend_type"] = self.backend_type
            _global_backend_state["backend_url"] = self.backend_url
            _global_backend_state["aifred_model"] = self.aifred_model
            _global_backend_state["aifred_model_id"] = self.aifred_model_id
            _global_backend_state["automatik_model"] = self.automatik_model
            _global_backend_state["automatik_model_id"] = self.automatik_model_id
            _global_backend_state["available_models"] = self.available_models
            _global_backend_state["available_models_dict"] = self.available_models_dict
            _global_backend_state["current_backend_label"] = self.current_backend_label

            # Load model parameters from cache
            self._load_all_agent_model_params()

            # Reset sampling parameters
            for agent in ["aifred", "sokrates", "salomo"]:
                self._reset_agent_sampling(agent, include_temperature=False)  # type: ignore[attr-defined]

            # Detect vision models
            self.add_debug("🔍 Detecting vision-capable models...")  # type: ignore[attr-defined, has-type]
            await self._detect_vision_models()

            # vLLM: Do NOT auto-start server — user triggers calibration manually
            # (like Ollama/llama-swap: discover models first, start on demand)

            _global_backend_state["_init_complete"] = True
            log_message(f"✅ Backend '{self.backend_type}' fully initialized and stored in global state")

            self.backend_initializing = False
            return False  # SLOW PATH

        except Exception as e:
            self.backend_healthy = False
            self.backend_info = f"Error: {str(e)}"
            self.add_debug(f"❌ Backend initialization failed: {e}")  # type: ignore[attr-defined, has-type]
            self.backend_initializing = False
            return False

    # ================================================================
    # VISION MODEL DETECTION
    # ================================================================

    async def _detect_vision_models(self) -> None:
        """Detect vision-capable models using backend-specific metadata."""
        from . import _global_backend_state
        from ..lib.vision_utils import is_vision_model, is_vision_model_sync

        vision_model_ids: list[str] = []

        if self.backend_type == "cloud_api":
            for model_id in self.available_models_dict.keys():
                if is_vision_model_sync(model_id):
                    vision_model_ids.append(model_id)
        else:
            for model_id, model_display in self.available_models_dict.items():
                try:
                    if await is_vision_model(self, model_id):
                        vision_model_ids.append(model_id)
                except (RuntimeError, OSError) as e:
                    log_message(f"⚠️ Vision detection failed for {model_id}: {e}")

        self.vision_models_cache = vision_model_ids
        _global_backend_state["vision_models_cache"] = vision_model_ids

        self.available_vision_models_list = [
            self.available_models_dict.get(mid, mid) for mid in vision_model_ids
            if mid in self.available_models_dict
        ]
        _global_backend_state["available_vision_models_list"] = self.available_vision_models_list

        self.add_debug(f"✅ Found {len(vision_model_ids)} vision-capable models")  # type: ignore[attr-defined, has-type]

        # Auto-select vision_model if not set or empty
        if (not self.vision_model_id or self.vision_model_id.strip() == "") and vision_model_ids:
            self.vision_model_id = vision_model_ids[0]
            self.vision_model = self.available_models_dict.get(self.vision_model_id, self.vision_model_id)
            self.add_debug(f"⚙️ Auto-selected vision_model: {self.vision_model_id}")  # type: ignore[attr-defined, has-type]
            self._save_settings()  # type: ignore[attr-defined, has-type]
        elif self.vision_model_id and vision_model_ids:
            if self.vision_model_id in vision_model_ids:
                self.vision_model = self.available_models_dict.get(self.vision_model_id, self.vision_model_id)
            else:
                self.add_debug(f"⚠️ Saved vision_model '{self.vision_model_id}' not found in vision models, auto-selecting...")  # type: ignore[attr-defined, has-type]
                self.vision_model_id = vision_model_ids[0]
                self.vision_model = self.available_models_dict.get(self.vision_model_id, self.vision_model_id)
                self._save_settings()  # type: ignore[attr-defined, has-type]

        if self.vision_model_id:
            from ..lib.agent_config import get_agent_label as _get_label
            self.add_debug(f"   {_get_label('vision')}: {self.vision_model}")  # type: ignore[attr-defined, has-type]

        _global_backend_state["vision_model"] = self.vision_model
        _global_backend_state["vision_model_id"] = self.vision_model_id

    # ================================================================
    # vLLM SERVER MANAGEMENT
    # ================================================================

    async def _start_vllm_server(self, model_id: str = "") -> None:
        """Start vLLM server process with specified model.

        Args:
            model_id: Model to load. Empty string = use self.aifred_model_id.
        """
        from . import _global_backend_state

        startup_model = model_id or self.aifred_model_id
        if not startup_model:
            self.add_debug("⚠️ No model selected — cannot start vLLM")  # type: ignore[attr-defined, has-type]
            return

        try:
            existing_manager = _global_backend_state.get("vllm_manager")
            if existing_manager and existing_manager.is_running():
                self.add_debug("✅ vLLM server already running (using existing process)")  # type: ignore[attr-defined, has-type]
                return

            self.add_debug(f"🚀 Starting vLLM server with {startup_model}...")  # type: ignore[attr-defined, has-type]

            yarn_config = None
            if self.enable_yarn and self.yarn_factor > 1.0:
                yarn_config = {
                    "factor": self.yarn_factor,
                    "original_max_position_embeddings": self.vllm_native_context
                }
                self.add_debug(f"🔧 YaRN: {self.yarn_factor}x scaling ({self.vllm_native_context:,} → {int(self.vllm_native_context * self.yarn_factor):,} tokens)")  # type: ignore[attr-defined, has-type]

            # Get compatible GPU indices for vLLM
            gpu_info = _global_backend_state.get("gpu_info")
            vllm_gpu_indices = None
            if gpu_info and gpu_info.backend_gpu_indices:
                vllm_gpu_indices = gpu_info.backend_gpu_indices.get("vllm") or None
                if vllm_gpu_indices and len(vllm_gpu_indices) < gpu_info.gpu_count:
                    compatible_names = [gpu_info.all_gpu_names[i] for i in vllm_gpu_indices]
                    compatible_vram = sum(gpu_info.all_gpu_vram_mb[i] for i in vllm_gpu_indices)
                    self.add_debug(f"🎯 vLLM restricted to GPU {','.join(str(i) for i in vllm_gpu_indices)}: {', '.join(compatible_names)} ({compatible_vram // 1024} GB)")  # type: ignore[attr-defined, has-type]

            vllm_manager = vLLMProcessManager(
                port=8001,
                max_model_len=0,
                gpu_memory_utilization=0.90,
                yarn_config=yarn_config,
                gpu_indices=vllm_gpu_indices
            )

            success, context_info = await vllm_manager.start_with_auto_detection(
                model=startup_model,
                timeout=120,
                feedback_callback=self.add_debug  # type: ignore[attr-defined, has-type]
            )

            if success and context_info:
                self.vllm_native_context = context_info["native_context"]
                self.vllm_max_tokens = context_info["hardware_limit"]

                native = context_info['native_context']

                if "reduced_yarn_factor" in context_info:
                    reduced_factor = context_info["reduced_yarn_factor"]
                    self.yarn_factor = reduced_factor
                    self.yarn_factor_input = f"{reduced_factor:.2f}"
                    self._save_settings()  # type: ignore[attr-defined, has-type]

                    if native > 0:
                        self.yarn_max_factor = reduced_factor
                        self.yarn_max_tested = True
                        self.add_debug(f"✅ YaRN factor automatically reduced to {reduced_factor:.2f}x (VRAM limit)")  # type: ignore[attr-defined, has-type]
                        self.add_debug(f"📏 Maximum YaRN factor: ~{self.yarn_max_factor:.1f}x (determined by test)")  # type: ignore[attr-defined, has-type]
                else:
                    self.yarn_max_factor = 0.0
                    self.yarn_max_tested = False
                    self.yarn_factor_input = f"{self.yarn_factor:.2f}"

                self.add_debug("📊 Context Info:")  # type: ignore[attr-defined, has-type]
                self.add_debug(f"  • Native: {context_info['native_context']:,} tokens (config.json)")  # type: ignore[attr-defined, has-type]
                self.add_debug(f"  • Hardware Limit: {context_info['hardware_limit']:,} tokens (VRAM)")  # type: ignore[attr-defined, has-type]
                self.add_debug(f"  • Used: {context_info['used_context']:,} tokens")  # type: ignore[attr-defined, has-type]

                from ..backends import BackendFactory
                vllm_backend = BackendFactory.create("vllm", base_url=self.backend_url)

                debug_messages = [
                    f"📊 Pre-calculated Context Limit: {context_info['hardware_limit']:,} tokens",
                    f"   Native: {context_info['native_context']:,} tokens (config.json)",
                    f"   Hardware Limit: {context_info['hardware_limit']:,} tokens (VRAM)",
                    f"   Used: {context_info['used_context']:,} tokens"
                ]

                vllm_backend.set_startup_context(  # type: ignore[union-attr]
                    context=context_info["hardware_limit"],
                    debug_messages=debug_messages
                )

                _global_backend_state["vllm_manager"] = vllm_manager
                self.add_debug("✅ vLLM server ready on port 8001")  # type: ignore[attr-defined, has-type]
            else:
                raise RuntimeError("vLLM failed to start with auto-detection")

        except Exception as e:
            self.add_debug(f"❌ Failed to start vLLM: {e}")  # type: ignore[attr-defined, has-type]
            _global_backend_state["vllm_manager"] = None

    async def _stop_vllm_server(self) -> None:
        """Stop vLLM server process gracefully."""
        from . import _global_backend_state

        vllm_manager = _global_backend_state.get("vllm_manager")
        if vllm_manager and vllm_manager.is_running():
            self.add_debug("🛑 Stopping vLLM server...")  # type: ignore[attr-defined, has-type]
            await vllm_manager.stop()
            _global_backend_state["vllm_manager"] = None
            self.add_debug("✅ vLLM server stopped")  # type: ignore[attr-defined, has-type]

    async def _restart_vllm_with_new_config(self) -> None:
        """Force restart vLLM server with new configuration (model or YaRN changes)."""
        from . import _global_backend_state

        try:
            # Force stop + start (even with same model — config changed)
            await self._stop_vllm_server()
            await self._start_vllm_server(self.aifred_model_id)

            _global_backend_state["aifred_model"] = self.aifred_model
            _global_backend_state["automatik_model"] = self.automatik_model

        except (RuntimeError, OSError) as e:
            self.add_debug(f"❌ vLLM restart failed: {e}")  # type: ignore[attr-defined, has-type]
            raise

    # ================================================================
    # BACKEND CLEANUP
    # ================================================================

    async def _cleanup_old_backend(self, old_backend: str) -> None:
        """Clean up resources from previous backend before switching."""
        from . import _global_backend_state
        from ..lib.process_utils import stop_backend_process

        if old_backend == "ollama":
            self.add_debug("🧹 Unloading Ollama models from VRAM...")  # type: ignore[attr-defined, has-type]
            try:
                from ..lib.llm_client import LLMClient
                llm_client = LLMClient(backend_type="ollama", base_url=config.DEFAULT_OLLAMA_URL)
                backend = llm_client._get_backend()

                if hasattr(backend, 'unload_all_models'):
                    success, unloaded_models = await backend.unload_all_models()
                    count = len(unloaded_models)
                    if count > 0:
                        self.add_debug(f"✅ Unloaded {count} Ollama model(s)")  # type: ignore[attr-defined, has-type]
                    else:
                        self.add_debug("ℹ️ No Ollama models were loaded")  # type: ignore[attr-defined, has-type]
            except (RuntimeError, AttributeError) as e:
                self.add_debug(f"⚠️ Error unloading Ollama models: {e}")  # type: ignore[attr-defined, has-type]

        elif old_backend == "vllm":
            self.add_debug("🛑 Stopping vLLM server...")  # type: ignore[attr-defined, has-type]
            if await stop_backend_process("vllm"):
                self.add_debug("✅ vLLM server stopped")  # type: ignore[attr-defined, has-type]
                _global_backend_state["vllm_manager"] = None
            else:
                self.add_debug("ℹ️ vLLM server was not running")  # type: ignore[attr-defined, has-type]

        elif old_backend == "tabbyapi":
            self.add_debug("🛑 Stopping TabbyAPI server...")  # type: ignore[attr-defined, has-type]
            if await stop_backend_process("tabbyapi"):
                self.add_debug("✅ TabbyAPI server stopped")  # type: ignore[attr-defined, has-type]
            else:
                self.add_debug("ℹ️ TabbyAPI server was not running")  # type: ignore[attr-defined, has-type]

        elif old_backend == "llamacpp":
            self.add_debug("🛑 Stopping llama-swap service...")  # type: ignore[attr-defined, has-type]
            from ..lib.process_utils import stop_llama_swap
            if stop_llama_swap():
                self.add_debug("✅ llama-swap stopped")  # type: ignore[attr-defined, has-type]
            else:
                self.add_debug("⚠️ Could not stop llama-swap")  # type: ignore[attr-defined, has-type]

    # ================================================================
    # BACKEND SWITCHING
    # ================================================================

    async def switch_backend_by_label(self, label: str):
        """Switch backend using display label (for native mobile select)."""
        label_to_id = {v: k for k, v in self.available_backends_dict.items()}
        backend_id = label_to_id.get(label, label.lower())
        async for _ in self.switch_backend(backend_id):
            yield

    async def switch_backend(self, new_backend: str):
        """Switch to different backend and restore last used models."""
        from . import _global_backend_state

        if new_backend in ["header_universal", "separator", "header_modern"]:
            return

        if self.backend_switching:
            self.add_debug("⚠️ Backend switch already in progress, please wait...")  # type: ignore[attr-defined, has-type]
            return

        self.backend_switching = True
        yield

        try:
            old_backend = self.backend_type
            self.add_debug(f"🔄 Switching backend from {old_backend} to {new_backend}...")  # type: ignore[attr-defined, has-type]

            self._save_settings()  # type: ignore[attr-defined, has-type]

            await self._cleanup_old_backend(old_backend)

            from ..lib.settings import load_settings
            settings = load_settings() or {}
            backend_models = settings.get("backend_models", {})

            target_main_model = None
            target_auto_model = None
            target_vision_model = None
            target_sokrates_model = None
            target_salomo_model = None

            self.add_debug(f"🔍 Settings contains backends: {list(backend_models.keys())}")  # type: ignore[attr-defined, has-type]
            if new_backend in backend_models:
                saved_models = backend_models[new_backend]
                target_main_model = saved_models.get("aifred_model")
                target_auto_model = saved_models.get("automatik_model")
                target_vision_model = saved_models.get("vision_model")
                target_sokrates_model = saved_models.get("sokrates_model", "")
                target_salomo_model = saved_models.get("salomo_model", "")
                self.add_debug(f"📝 Loading {new_backend} from settings: AIfred={target_main_model}, Auto={target_auto_model}, Vision={target_vision_model}, Sokrates={target_sokrates_model or '(Main)'}, Salomo={target_salomo_model or '(Main)'}")  # type: ignore[attr-defined, has-type]
            else:
                default_models = config.BACKEND_DEFAULT_MODELS.get(new_backend, {})
                target_main_model = default_models.get("aifred_model")
                target_auto_model = default_models.get("automatik_model")
                target_sokrates_model = default_models.get("sokrates_model", "")
                target_salomo_model = default_models.get("salomo_model", "")
                self.add_debug(f"📝 No settings for {new_backend}, using config.py defaults: AIfred={target_main_model}, Auto={target_auto_model}")  # type: ignore[attr-defined, has-type]

            if target_main_model:
                self.aifred_model = target_main_model
                self.aifred_model_id = target_main_model
            if target_auto_model is not None:
                self.automatik_model = target_auto_model
                self.automatik_model_id = target_auto_model
            if target_vision_model:
                self.vision_model = target_vision_model
                self.vision_model_id = target_vision_model
            self.sokrates_model_id = target_sokrates_model or ""  # type: ignore[attr-defined, has-type]
            self.sokrates_model = target_sokrates_model or ""  # type: ignore[attr-defined, has-type]
            self.salomo_model_id = target_salomo_model or ""  # type: ignore[attr-defined, has-type]
            self.salomo_model = target_salomo_model or ""  # type: ignore[attr-defined, has-type]

            if new_backend in ["vllm", "tabbyapi"]:
                if self.automatik_model_id:
                    self.add_debug(f"⚠️ {new_backend} can only load one model - Automatic will use AIfred-LLM")  # type: ignore[attr-defined, has-type]
                self.automatik_model = ""
                self.automatik_model_id = ""

            self.backend_type = new_backend
            self.backend_id = new_backend
            self.current_backend_label = self.available_backends_dict.get(new_backend, new_backend)
            _global_backend_state["_init_complete"] = False
            await self.initialize_backend()

            self._save_settings()  # type: ignore[attr-defined, has-type]

        finally:
            self.backend_switching = False
            self.add_debug("✅ Backend switch complete")  # type: ignore[attr-defined, has-type]

            from aifred.lib.logging_utils import console_separator
            console_separator()
            self.debug_messages.append("────────────────────")  # type: ignore[attr-defined, has-type]

            yield

    # ================================================================
    # CLOUD API PROVIDER SWITCHING
    # ================================================================

    async def set_cloud_api_provider_by_label(self, label: str):
        """Switch Cloud API provider using display label."""
        label_to_id = {
            "Claude (Anthropic)": "claude",
            "Qwen (DashScope)": "qwen",
            "DeepSeek": "deepseek",
            "Kimi (Moonshot)": "kimi",
        }
        provider_id = label_to_id.get(label, "qwen")
        async for _ in self.set_cloud_api_provider(provider_id):
            yield

    async def set_cloud_api_provider(self, provider: str):
        """Switch Cloud API provider (claude, qwen, kimi)."""
        if provider not in CLOUD_API_PROVIDERS:
            self.add_debug(f"⚠️ Unknown cloud provider: {provider}")  # type: ignore[attr-defined, has-type]
            return

        provider_config = CLOUD_API_PROVIDERS[provider]
        self.cloud_api_provider = provider
        self.cloud_api_provider_label = provider_config["name"]

        self.cloud_api_key_configured = is_cloud_api_configured(provider)

        self.add_debug(f"☁️ Switching to {provider_config['name']}...")  # type: ignore[attr-defined, has-type]

        if self.cloud_api_key_configured:
            self.add_debug(f"✅ API key found ({provider_config['env_key']})")  # type: ignore[attr-defined, has-type]
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
                    self.add_debug(f"📋 {len(models)} models available")  # type: ignore[attr-defined, has-type]

                    from ..lib.vision_utils import is_vision_model_sync
                    vl_models = [m for m in models if is_vision_model_sync(m)]
                    self.vision_models_cache = vl_models
                    self.available_vision_models_list = vl_models
                    if vl_models:
                        self.add_debug(f"📷 {len(vl_models)} vision models")  # type: ignore[attr-defined, has-type]

                    default_model = models[0]
                    self.aifred_model = default_model
                    self.aifred_model_id = default_model
                    self.automatik_model = default_model
                    self.automatik_model_id = default_model

                    if vl_models:
                        self.vision_model = vl_models[0]
                        self.vision_model_id = vl_models[0]
                    else:
                        self.vision_model = ""
                        self.vision_model_id = ""
                else:
                    self._clear_models_state(include_vision=True)
                    self.add_debug("⚠️ No models returned from API")  # type: ignore[attr-defined, has-type]
            except Exception as e:
                self._clear_models_state(include_vision=True)
                self.add_debug(f"⚠️ Failed to fetch models: {e}")  # type: ignore[attr-defined, has-type]
        else:
            self._clear_models_state(include_vision=True)
            self.add_debug(f"⚠️ Set {provider_config['env_key']} in .env")  # type: ignore[attr-defined, has-type]

        self._save_settings()  # type: ignore[attr-defined, has-type]
        yield

    # ================================================================
    # MODEL SELECTION
    # ================================================================

    async def set_aifred_model(self, model: str):
        """Set selected model and restart backend if needed."""
        from ..lib.vision_utils import is_vision_model

        old_model = self.aifred_model
        self.aifred_model = model
        self.aifred_model_id = self._resolve_model_id(model)
        self.add_debug(f"📝 AIfred-LLM: {model}")  # type: ignore[attr-defined, has-type]

        # Load model parameters from cache
        self._load_agent_model_params("aifred", self.aifred_model_id)
        if self.backend_type == "llamacpp" and not getattr(self, "aifred_has_speed_variant", False):
            self.aifred_speed_mode = False  # type: ignore[attr-defined, has-type]

        self._show_model_calibration_info(self.aifred_model_id)  # type: ignore[attr-defined]

        # Check if switching to non-vision model with pending images
        if len(self.pending_images) > 0:  # type: ignore[attr-defined, has-type]
            if not await is_vision_model(self, self._effective_model_id("aifred")):  # type: ignore[attr-defined]
                self.image_upload_warning = "⚠️ Selected model doesn't support images. Images will be ignored when sending."  # type: ignore[attr-defined, has-type]
            else:
                self.image_upload_warning = ""  # type: ignore[attr-defined, has-type]

        # Reset sampling params to model defaults for all affected agents
        self._reset_agent_sampling("aifred")  # type: ignore[attr-defined]
        if not self.sokrates_model_id:  # type: ignore[attr-defined, has-type]
            self._reset_agent_sampling("sokrates")  # type: ignore[attr-defined]
        if not self.salomo_model_id:  # type: ignore[attr-defined, has-type]
            self._reset_agent_sampling("salomo")  # type: ignore[attr-defined]

        self._save_settings()  # type: ignore[attr-defined, has-type]

        # vLLM/TabbyAPI: Force restart backend for model change
        if self.backend_type in ["vllm", "tabbyapi"] and old_model != model:
            if self.backend_type == "vllm" and self.automatik_model != model:
                self.automatik_model = model

            if self.backend_type == "vllm":
                old_yarn_factor = self.yarn_factor
                if old_yarn_factor != 1.0:
                    self.yarn_factor = 1.0
                    self.yarn_factor_input = "1.0"
                    self.yarn_max_factor = 0.0
                    self.yarn_max_tested = False
                    self.add_debug(f"🔄 YaRN factor reset: {old_yarn_factor:.1f}x → 1.0x (new model needs recalibration)")  # type: ignore[attr-defined, has-type]

            self.add_debug("🔄 Backend restart for model switch...")  # type: ignore[attr-defined, has-type]

            self.vllm_restarting = True
            yield

            try:
                if self.backend_type == "vllm":
                    await self._restart_vllm_with_new_config()
                else:
                    await self.initialize_backend()
                self.add_debug(f"✅ New model loaded: {model}")  # type: ignore[attr-defined, has-type]
            finally:
                self.vllm_restarting = False
                yield

    async def set_aifred_model_by_id(self, model_id: str):
        """Set selected model using pure ID (new key-value system)."""
        self.aifred_model_id = model_id

        if self.backend_id == "ollama":
            from ..lib.model_vram_cache import get_rope_factor_for_model
            self.aifred_rope_factor = get_rope_factor_for_model(model_id)  # type: ignore[attr-defined, has-type]
        elif self.backend_type == "llamacpp":
            self.aifred_rope_factor = 1.0  # type: ignore[attr-defined, has-type]

        display_label = self.available_models_dict.get(model_id, model_id)
        self.aifred_model = display_label

        async for _ in self.set_aifred_model(display_label):
            pass

    async def set_automatik_model(self, model: str):
        """Set automatik model for decision and query optimization."""
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined, has-type]
        if model == t("sokrates_llm_same", lang=lang):
            model = ""

        old_model = self.automatik_model
        self.automatik_model = model
        self.automatik_model_id = self._resolve_model_id(model)
        self._save_settings()  # type: ignore[attr-defined, has-type]

        if model:
            self.add_debug(f"⚡ Automatic-LLM: {model}")  # type: ignore[attr-defined, has-type]
            self._show_model_calibration_info(self.automatik_model_id)  # type: ignore[attr-defined]
        else:
            self.add_debug("⚡ Automatic-LLM: (same as Main-LLM)")  # type: ignore[attr-defined, has-type]

        if self.backend_type in ["vllm", "tabbyapi"] and old_model != model:
            self.add_debug("🔄 Backend restart for Automatic model switch...")  # type: ignore[attr-defined, has-type]
            await self.initialize_backend()
            self.add_debug("✅ New Automatic model loaded")  # type: ignore[attr-defined, has-type]

    async def set_automatik_model_by_id(self, model_id: str):
        """Set automatik model using pure ID (new key-value system)."""
        self.automatik_model_id = model_id

        if model_id:
            if self.backend_id == "ollama":
                from ..lib.model_vram_cache import get_rope_factor_for_model
                self.automatik_rope_factor = get_rope_factor_for_model(model_id)  # type: ignore[attr-defined, has-type]
            elif self.backend_type == "llamacpp":
                self.automatik_rope_factor = 1.0  # type: ignore[attr-defined, has-type]

        display_label = self.available_models_dict.get(model_id, model_id)
        self.automatik_model = display_label

        await self.set_automatik_model(display_label)

    async def set_vision_model(self, model: str):
        """Set vision model for OCR/image analysis."""
        self.vision_model = model
        self.vision_model_id = self._resolve_model_id(model)
        self.add_debug(f"👁️ Vision-LLM: {model}")  # type: ignore[attr-defined, has-type]
        self._show_model_calibration_info(self.vision_model_id)  # type: ignore[attr-defined]

        if self.backend_type == "llamacpp":
            from ..lib.model_vram_cache import get_llamacpp_speed_split
            self.vision_has_speed_variant = get_llamacpp_speed_split(self.vision_model_id)[0] > 0  # type: ignore[attr-defined, has-type]
            if not self.vision_has_speed_variant:  # type: ignore[attr-defined, has-type]
                self.vision_speed_mode = False  # type: ignore[attr-defined, has-type]

        self._save_settings()  # type: ignore[attr-defined, has-type]

    async def set_vision_model_by_id(self, model_id: str):
        """Set vision model using pure ID (new key-value system)."""
        self.vision_model_id = model_id

        if self.backend_id == "ollama":
            from ..lib.model_vram_cache import get_rope_factor_for_model
            self.vision_rope_factor = get_rope_factor_for_model(model_id)  # type: ignore[attr-defined, has-type]
        elif self.backend_type == "llamacpp":
            self.vision_rope_factor = 1.0  # type: ignore[attr-defined, has-type]

        display_label = self.available_models_dict.get(model_id, model_id)
        self.vision_model = display_label

        await self.set_vision_model(display_label)

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

    async def _ensure_vllm_model(self, model_id: str = "") -> None:
        """Ensure vLLM is running with the specified model.

        - Not running → start with model_id
        - Running with same model → touch TTL
        - Running with different model → restart with new model

        Args:
            model_id: Model to ensure. Empty string = use self.aifred_model_id.
        """
        from . import _global_backend_state

        target_model = model_id or self.aifred_model_id
        if not target_model:
            self.add_debug("⚠️ No model selected — cannot start vLLM")  # type: ignore[attr-defined, has-type]
            return

        existing_manager = _global_backend_state.get("vllm_manager")

        if existing_manager and existing_manager.is_running():
            if existing_manager.current_model == target_model:
                existing_manager.touch()  # Same model — just reset TTL
                return
            # Different model — stop first, then start with new model
            self.add_debug(f"🔄 vLLM model switch: {existing_manager.current_model} → {target_model}")  # type: ignore[attr-defined, has-type]
            await self._stop_vllm_server()

        await self._start_vllm_server(target_model)
