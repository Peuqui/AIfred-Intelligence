"""TTS configuration mixin for AIfred state.

Handles TTS engine selection, voice settings, per-agent voice configuration,
and engine-specific preferences (XTTS GPU/CPU mode, voice caching, etc.).

Does NOT contain TTS streaming/generation logic (see _tts_streaming_mixin.py).
"""

from __future__ import annotations

from typing import Any, Dict, List

import reflex as rx


class TTSConfigMixin(rx.State, mixin=True):
    """Mixin for TTS configuration, voice settings, and engine management."""

    # ── TTS Settings ──────────────────────────────────────────────
    enable_tts: bool = False
    tts_voice: str = "AIfred"  # Default voice - XTTS custom voice
    tts_engine: str = "xtts"  # TTS engine key (default: XTTS)
    tts_autoplay: bool = True  # Auto-play TTS audio after generation (user setting)
    tts_playback_rate: str = "1.0x"  # Browser playback rate (1.0 = neutral, speed via Agent Settings)
    tts_pitch: str = "1.0"  # Pitch adjustment (0.8 = lower, 1.0 = normal, 1.2 = higher)
    # Per-Agent TTS Voice Settings (for Multi-Agent mode with distinct voices)
    # Format: agent_id -> {"voice": str, "speed": str, "pitch": str, "enabled": bool}
    # Agents: aifred (default), sokrates, salomo
    tts_agent_voices: Dict[str, Dict[str, Any]] = {
        "aifred": {"voice": "\u2605 AIfred", "speed": "1.0x", "pitch": "1.0", "enabled": True},
        "sokrates": {"voice": "\u2605 Sokrates", "speed": "1.0x", "pitch": "1.0", "enabled": True},
        "salomo": {"voice": "Baldur Sanjin", "speed": "1.0x", "pitch": "1.0", "enabled": True},
    }
    # XTTS voices cache - refreshed when engine changes to XTTS
    xtts_voices_cache: List[str] = []
    # XTTS CPU Mode - Force CPU inference (slower but saves GPU VRAM for LLM)
    xtts_force_cpu: bool = False
    # MOSS-TTS device ("cuda", "cpu", or "" if not running)
    # Used by context_manager/context_utils for VRAM reservation
    moss_tts_device: str = ""
    # Streaming TTS toggle (config only — streaming logic is elsewhere)
    tts_streaming_enabled: bool = True  # Enable streaming TTS (vs waiting for full response)

    # ── Computed Vars ─────────────────────────────────────────────

    @rx.var(deps=["ui_language"], auto_deps=False)
    def tts_engines(self) -> List[str]:
        """Available TTS engines for main dropdown (includes 'Off')."""
        from ..lib.config import TTS_ENGINE_KEYS
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
        return [t(f"tts_engine_{key}", lang=lang) for key in TTS_ENGINE_KEYS]

    @rx.var
    def xtts_gpu_enabled(self) -> bool:
        """Computed: True when GPU mode, False when CPU mode."""
        return not self.xtts_force_cpu

    @rx.var(deps=["tts_engine", "xtts_voices_cache"], auto_deps=False)
    def available_tts_voices(self) -> List[str]:
        """
        Returns list of available TTS voices for the current engine.
        Edge TTS, XTTS v2, Piper and eSpeak have different voice sets.

        Note: Uses auto_deps=False with explicit deps to disable automatic
        dependency detection (Reflex cannot introspect module-level imports).
        XTTS voices come from xtts_voices_cache (refreshed via _refresh_xtts_voices).
        """
        from ..lib.config import EDGE_TTS_VOICES, ESPEAK_VOICES, PIPER_VOICES

        if self.tts_engine == "xtts":
            # Use cached voices (refreshed when engine changes to XTTS)
            if self.xtts_voices_cache:
                return self.xtts_voices_cache  # Already sorted by _refresh_xtts_voices
            # Fallback when service unavailable
            from ..lib.config import XTTS_VOICES_FALLBACK, sort_voices_custom_first
            return sort_voices_custom_first(list(XTTS_VOICES_FALLBACK.keys()))
        elif self.tts_engine == "moss":  # MOSS-TTS (batch)
            from ..lib.config import get_moss_voices, MOSS_TTS_VOICES_FALLBACK
            voices = get_moss_voices()
            if voices:
                return sorted(list(voices.keys()))
            return sorted(list(MOSS_TTS_VOICES_FALLBACK.keys()))
        elif self.tts_engine == "qwen3local":  # Qwen3-TTS local container
            from ..lib.config import get_qwen3local_voices, QWEN3_TTS_VOICES_FALLBACK
            voices = get_qwen3local_voices()
            if voices:
                return sorted(list(voices.keys()))
            return sorted(list(QWEN3_TTS_VOICES_FALLBACK.keys()))
        elif self.tts_engine == "fishspeech":  # Fish-Speech S2 Pro local container
            from ..lib.config import get_fishspeech_voices, FISH_SPEECH_VOICES_FALLBACK
            voices = get_fishspeech_voices()
            if voices:
                return sorted(list(voices.keys()))
            return sorted(list(FISH_SPEECH_VOICES_FALLBACK.keys()))
        elif self.tts_engine == "dashscope":
            from ..lib.config import DASHSCOPE_VOICES, sort_voices_custom_first
            return sort_voices_custom_first(list(DASHSCOPE_VOICES.keys()))
        elif self.tts_engine == "piper":
            return sorted(list(PIPER_VOICES.keys()))
        elif self.tts_engine == "espeak":
            return sorted(list(ESPEAK_VOICES.keys()))
        else:
            return sorted(list(EDGE_TTS_VOICES.keys()))

    @rx.var(deps=["enable_tts"], auto_deps=False)
    def tts_player_visible(self) -> bool:
        """Returns True if TTS audio player should be visible.

        Player is visible when TTS is enabled (always shows player controls).
        """
        return self.enable_tts

    @rx.var(deps=["enable_tts", "tts_engine", "ui_language"], auto_deps=False)
    def tts_engine_or_off(self) -> str:
        """Dropdown value: translated engine label when TTS enabled, translated 'Off' when disabled."""
        from ..lib.i18n import tts_key_to_label
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
        return tts_key_to_label(self.tts_engine, lang=lang) if self.enable_tts else tts_key_to_label("off", lang=lang)

    # ── Agent Editor TTS State ───────────────────────────────────
    # The editor lets you configure voices per backend per agent.
    # editor_tts_engine selects which backend you're configuring.
    # _editor_tts_settings holds the loaded settings for that agent+engine.
    editor_tts_engine: str = "xtts"  # Default, overridden by active engine on agent load
    _editor_tts_settings: Dict[str, Any] = {}  # {"voice": ..., "speed": ..., "pitch": ..., "enabled": ...}

    @rx.var(deps=["ui_language", "editor_tts_engine", "_editor_tts_settings"], auto_deps=False)
    def editor_tts_engine_label(self) -> str:
        """Translated label: 'Off' when agent TTS disabled, engine label otherwise."""
        from ..lib.i18n import tts_key_to_label
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
        if not self._editor_tts_settings.get("enabled", True):
            return tts_key_to_label("off", lang=lang)
        return tts_key_to_label(self.editor_tts_engine, lang=lang)

    @rx.var(deps=["editor_tts_engine", "xtts_voices_cache", "_editor_tts_settings"], auto_deps=False)
    def editor_tts_available_voices(self) -> List[str]:
        """Available voices for the editor's selected TTS engine.

        Base: saved voices from settings.json (always shown).
        If engine is running: merge live voices for more options.
        Selected voice always comes from settings, never from live query.
        """
        from ..lib.settings import load_settings

        engine = self.editor_tts_engine

        # 1. Base: saved voices from settings.json
        saved_voices: set[str] = set()
        settings = load_settings() or {}
        per_engine = settings.get("tts_agent_voices_per_engine", {}).get(engine, {})
        for cfg in per_engine.values():
            v = cfg.get("voice", "")
            if v:
                saved_voices.add(v)

        # Also include the currently loaded voice
        current_voice = self._editor_tts_settings.get("voice", "")
        if current_voice:
            saved_voices.add(current_voice)

        # 2. If engine is running, merge live voices for more selection
        live_voices: set[str] = set()
        if engine == "xtts":
            if self.xtts_voices_cache:
                live_voices = set(self.xtts_voices_cache)
        elif engine == "moss":
            from ..lib.config import get_moss_voices
            voices = get_moss_voices()  # Returns None if not running
            if voices:
                live_voices = set(voices.keys())
        elif engine == "qwen3local":
            from ..lib.config import get_qwen3local_voices, QWEN3_TTS_VOICES_FALLBACK
            voices = get_qwen3local_voices()
            live_voices = set(voices.keys()) if voices else set(QWEN3_TTS_VOICES_FALLBACK.keys())
        elif engine == "fishspeech":
            from ..lib.config import get_fishspeech_voices, FISH_SPEECH_VOICES_FALLBACK
            voices = get_fishspeech_voices()
            live_voices = set(voices.keys()) if voices else set(FISH_SPEECH_VOICES_FALLBACK.keys())
        elif engine == "dashscope":
            from ..lib.config import DASHSCOPE_VOICES
            live_voices = set(DASHSCOPE_VOICES.keys())
        elif engine == "piper":
            from ..lib.config import PIPER_VOICES
            live_voices = set(PIPER_VOICES.keys())
        elif engine == "espeak":
            from ..lib.config import ESPEAK_VOICES
            live_voices = set(ESPEAK_VOICES.keys())
        else:
            from ..lib.config import EDGE_TTS_VOICES
            live_voices = set(EDGE_TTS_VOICES.keys())

        # 3. Merge: saved (always) + live (if available)
        # ★ voices (cloned) come first, then alphabetical
        all_voices = saved_voices | live_voices
        starred = sorted(v for v in all_voices if v.startswith("★"))
        regular = sorted(v for v in all_voices if not v.startswith("★"))
        return starred + regular

    @rx.var(deps=["_editor_tts_settings"], auto_deps=False)
    def editor_agent_tts_voice(self) -> str:
        return str(self._editor_tts_settings.get("voice", ""))

    @rx.var(deps=["_editor_tts_settings"], auto_deps=False)
    def editor_agent_tts_speed(self) -> str:
        return str(self._editor_tts_settings.get("speed", "1.0x"))

    @rx.var(deps=["_editor_tts_settings"], auto_deps=False)
    def editor_agent_tts_pitch(self) -> str:
        return str(self._editor_tts_settings.get("pitch", "1.0"))

    @rx.var(deps=["_editor_tts_settings"], auto_deps=False)
    def editor_agent_tts_enabled(self) -> bool:
        return bool(self._editor_tts_settings.get("enabled", True))

    @rx.var(deps=["_editor_tts_settings"], auto_deps=False)
    def editor_agent_tts_language(self) -> str:
        """Dropdown value: human-readable label for the stored ISO code.
        Empty / unset / "auto" → "Auto" (= follow detected/UI language).
        """
        from ..lib.config import TTS_LANGUAGE_CODE_TO_LABEL
        code = str(self._editor_tts_settings.get("language", "") or "auto")
        return TTS_LANGUAGE_CODE_TO_LABEL.get(code, "Auto")

    @rx.var(auto_deps=False)
    def tts_language_labels(self) -> List[str]:
        """Static list of labels for the agent-editor language dropdown."""
        from ..lib.config import TTS_LANGUAGE_LABELS
        return TTS_LANGUAGE_LABELS

    def set_editor_agent_tts_language(self, label: str) -> None:
        """Persist a per-agent TTS-language override (or clear back to 'auto')."""
        from ..lib.config import TTS_LANGUAGE_LABEL_TO_CODE
        code = TTS_LANGUAGE_LABEL_TO_CODE.get(label, "auto")
        self._editor_tts_settings["language"] = code
        self._save_editor_tts_settings()

    # ── TTS Toggle / Engine Selection ─────────────────────────────

    def set_tts_engine_or_off(self, selection: str):
        """Combined TTS on/off + engine selection from single dropdown.

        Receives translated label from dropdown, maps to internal key.
        "Off"/"Aus" disables TTS, any engine label enables TTS.

        Uses switch_tts_engine() generator as SSOT — yields after each
        status message for live Reflex UI updates.
        """
        from ..lib.i18n import tts_label_to_key
        from ..lib.tts_engine_manager import ensure_tts_state, stop_engine, GPU_ENGINES

        key = tts_label_to_key(selection)
        if key == "off":
            if not self.enable_tts:
                yield  # Must yield even for no-op so Reflex updates UI
                return

            # Save per-engine settings before disabling
            self._save_agent_voices_for_engine(self.tts_engine)
            self._save_tts_toggles_for_engine(self.tts_engine)

            self.enable_tts = False
            self.add_debug("🔊 TTS: disabled")  # type: ignore[attr-defined]

            # Stop running Docker container
            if self.tts_engine in GPU_ENGINES:
                success, msg = stop_engine(self.tts_engine)
                self.add_debug(f"✅ {msg}" if success else f"❌ {msg}")  # type: ignore[attr-defined]
                if self.tts_engine == "moss":
                    self.moss_tts_device = ""

            self._save_settings()  # type: ignore[attr-defined]
            yield
            return

        # Engine selected — no-op if already active with same engine
        if self.enable_tts and key == self.tts_engine:
            yield
            return

        was_enabled = self.enable_tts
        old_key = self.tts_engine

        # Save current per-engine settings BEFORE switching
        if was_enabled:
            self._save_agent_voices_for_engine(old_key)
            self._save_tts_toggles_for_engine(old_key)

        # Enable TTS + set engine key (menu change → save immediately)
        self.enable_tts = True
        self.tts_engine = key
        self._save_settings()  # type: ignore[attr-defined]

        # Restore per-engine settings into state (reads from settings.json, no write)
        self._restore_agent_voices_for_engine(key)
        self._restore_tts_toggles_for_engine(key)
        self._switch_tts_voice_for_language(self.ui_language)  # type: ignore[attr-defined]

        self.add_debug(f"🔊 TTS Engine: {key}")  # type: ignore[attr-defined]
        yield

        # SSOT: ensure_tts_state generator — each yield = one blocking step done
        gen = ensure_tts_state(
            wanted_tts=key if key in GPU_ENGINES else "",
            backend_type=self.backend_type,  # type: ignore[attr-defined]
            xtts_force_cpu=self.xtts_force_cpu,
        )
        result = None
        try:
            while True:
                msg = next(gen)
                self.add_debug(f"🔊 {msg}")  # type: ignore[attr-defined]
                yield  # Reflex UI update after each step
        except StopIteration as e:
            result = e.value

        # Update engine-specific Reflex state from result
        if result and key == "moss":
            self.moss_tts_device = result.moss_device if result.success else ""
        if key == "xtts":
            self._refresh_xtts_voices()

    # ── Voice / Speed / Pitch / Autoplay ──────────────────────────

    def set_tts_voice(self, voice: str):
        """Set TTS voice"""
        self.tts_voice = voice
        self.add_debug(f"🔊 TTS Voice: {voice}")  # type: ignore[attr-defined]
        self._save_settings()  # type: ignore[attr-defined]

    def toggle_xtts_gpu(self, use_gpu: bool):
        """Toggle XTTS GPU mode with immediate UI feedback."""
        import os
        from ..lib.process_utils import set_xtts_cpu_mode
        from ..lib.settings import SETTINGS_FILE

        force_cpu = not use_gpu
        self.xtts_force_cpu = force_cpu
        mode_str = "GPU (auto)" if use_gpu else "CPU (forced)"
        self.add_debug(f"🔊 XTTS: Wechsle zu {mode_str}...")  # type: ignore[attr-defined]
        self._save_settings()  # type: ignore[attr-defined]
        # Update mtime tracker so periodic poll doesn't re-trigger "Settings reloaded"
        try:
            self._last_settings_mtime = os.path.getmtime(SETTINGS_FILE)  # type: ignore[attr-defined]
        except OSError:
            pass
        yield

        success, message = set_xtts_cpu_mode(force_cpu)
        if success:
            self.add_debug(f"✅ {message}")  # type: ignore[attr-defined]
        else:
            self.add_debug(f"❌ {message}")  # type: ignore[attr-defined]

    def set_xtts_force_cpu(self, force_cpu: bool):
        """Set XTTS CPU mode and restart container.

        When force_cpu=True:
        - XTTS runs on CPU (slower, but saves GPU VRAM for LLM)
        - No VRAM reservation needed for context calculation

        When force_cpu=False:
        - XTTS auto-detects GPU/CPU based on available VRAM
        - VRAM reservation applied to context calculation
        """
        from ..lib.process_utils import set_xtts_cpu_mode

        self.xtts_force_cpu = force_cpu
        mode_str = "CPU (forced)" if force_cpu else "GPU (auto)"
        self.add_debug(f"🔊 XTTS Mode: {mode_str} - restarting container...")  # type: ignore[attr-defined]

        # Restart XTTS container with new setting
        success, message = set_xtts_cpu_mode(force_cpu)

        if success:
            self.add_debug(f"✅ {message}")  # type: ignore[attr-defined]
        else:
            self.add_debug(f"❌ {message}")  # type: ignore[attr-defined]

        self._save_settings()  # type: ignore[attr-defined]

    async def unload_xtts_model(self):
        """Unload XTTS model from memory to free VRAM."""
        import httpx
        from ..lib.config import XTTS_SERVICE_URL

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(f"{XTTS_SERVICE_URL}/unload")
                response.raise_for_status()
                data = response.json()

                if data.get("success"):
                    freed_device = data.get("freed_device", "unknown")
                    self.add_debug(f"✅ XTTS model unloaded from {freed_device}")  # type: ignore[attr-defined]
                    yield rx.toast.success(f"XTTS model unloaded from {freed_device}", duration=3000)
                else:
                    self.add_debug("⚠️ XTTS unload failed")  # type: ignore[attr-defined]
                    yield rx.toast.error("Failed to unload XTTS model", duration=3000)

        except httpx.HTTPError as e:
            self.add_debug(f"❌ XTTS unload error: {e}")  # type: ignore[attr-defined]
            yield rx.toast.error(f"Error: {e}", duration=3000)

    # Note: set_tts_speed removed - generation always at 1.0, tempo via browser playback rate

    def toggle_tts_autoplay(self):
        """Toggle TTS auto-play"""
        self.tts_autoplay = not self.tts_autoplay
        self.add_debug(f"🔊 TTS Auto-Play: {'enabled' if self.tts_autoplay else 'disabled'}")  # type: ignore[attr-defined]
        self._save_tts_toggles_for_engine(self.tts_engine)

    def toggle_tts_streaming(self):
        """Toggle streaming TTS (sentence-by-sentence vs complete response)"""
        self.tts_streaming_enabled = not self.tts_streaming_enabled
        mode = "Streaming (realtime)" if self.tts_streaming_enabled else "Standard (after response)"
        self.add_debug(f"🔊 TTS Mode: {mode}")  # type: ignore[attr-defined]
        self._save_tts_toggles_for_engine(self.tts_engine)

    def set_tts_playback_rate(self, rate: str):
        """Set TTS playback rate (browser-side only, TTS generation stays at 1.0)"""
        self.tts_playback_rate = rate
        self.add_debug(f"🔊 TTS Tempo: {rate}")  # type: ignore[attr-defined]
        self._save_settings()  # type: ignore[attr-defined]
        # Apply rate to current audio player via JavaScript
        rate_value = rate.replace("x", "")
        return rx.call_script(f"setTtsPlaybackRate({rate_value})")

    def set_tts_pitch(self, pitch: str):
        """Set TTS pitch adjustment (applied via ffmpeg post-processing)"""
        self.tts_pitch = pitch
        self.add_debug(f"🔊 TTS Pitch: {pitch}")  # type: ignore[attr-defined]
        self._save_settings()  # type: ignore[attr-defined]

    # ── Per-Agent Voice Settings ──────────────────────────────────

    def set_agent_voice(self, agent: str, voice: str):
        """Set voice for a specific agent."""
        if agent in self.tts_agent_voices:
            self.tts_agent_voices[agent]["voice"] = voice
            self.add_debug(f"🔊 {agent.capitalize()} Voice: {voice}")  # type: ignore[attr-defined]
            self._save_agent_voices_for_engine(self.tts_engine)

    def set_agent_speed(self, agent: str, speed: str):
        """Set playback speed for a specific agent."""
        if agent in self.tts_agent_voices:
            self.tts_agent_voices[agent]["speed"] = speed
            self.add_debug(f"🔊 {agent.capitalize()} Speed: {speed}")  # type: ignore[attr-defined]
            self._save_agent_voices_for_engine(self.tts_engine)

    def set_agent_pitch(self, agent: str, pitch: str):
        """Set pitch for a specific agent."""
        if agent in self.tts_agent_voices:
            self.tts_agent_voices[agent]["pitch"] = pitch
            self.add_debug(f"🔊 {agent.capitalize()} Pitch: {pitch}")  # type: ignore[attr-defined]
            self._save_agent_voices_for_engine(self.tts_engine)

    def toggle_agent_tts(self, agent: str):
        """Toggle TTS enabled for a specific agent."""
        if agent in self.tts_agent_voices:
            self.tts_agent_voices[agent]["enabled"] = not self.tts_agent_voices[agent]["enabled"]
            status = "enabled" if self.tts_agent_voices[agent]["enabled"] else "disabled"
            self.add_debug(f"🔊 {agent.capitalize()} TTS: {status}")  # type: ignore[attr-defined]
            self._save_agent_voices_for_engine(self.tts_engine)

    # ── Agent Editor TTS Handlers ────────────────────────────────

    def _load_editor_tts_settings(self) -> None:
        """Load TTS settings for the current editor agent + editor engine."""
        from ..lib.settings import load_settings
        from ..lib.agent_config import get_tts_voice_default

        agent_id = self.editor_agent_id  # type: ignore[attr-defined]
        engine = self.editor_tts_engine
        if not agent_id:
            self._editor_tts_settings = {}
            return

        # If editor engine matches the active engine, read from live state
        if engine == self.tts_engine:
            self._editor_tts_settings = dict(
                self.tts_agent_voices.get(agent_id, {"voice": "", "speed": "1.0x", "pitch": "1.0", "enabled": True})
            )
            return

        # Otherwise read from saved per-engine settings
        settings = load_settings() or {}
        saved = settings.get("tts_agent_voices_per_engine", {}).get(engine, {}).get(agent_id)
        if saved:
            self._editor_tts_settings = dict(saved)
        else:
            # Fall back to engine defaults from agents.json
            self._editor_tts_settings = get_tts_voice_default(agent_id, engine)

    def _save_editor_tts_settings(self) -> None:
        """Save current editor TTS settings to the correct storage."""
        agent_id = self.editor_agent_id  # type: ignore[attr-defined]
        engine = self.editor_tts_engine
        if not agent_id:
            return

        # If editor engine matches the active engine, update live state
        if engine == self.tts_engine:
            # Re-assign the entire dict so Reflex detects the state change
            updated = dict(self.tts_agent_voices)
            updated[agent_id] = dict(self._editor_tts_settings)
            self.tts_agent_voices = updated
            self._save_agent_voices_for_engine(engine)
            return

        # Otherwise save to per-engine settings in settings.json
        from ..lib.settings import load_settings, save_settings
        settings = load_settings() or {}
        if "tts_agent_voices_per_engine" not in settings:
            settings["tts_agent_voices_per_engine"] = {}
        if engine not in settings["tts_agent_voices_per_engine"]:
            settings["tts_agent_voices_per_engine"][engine] = {}
        settings["tts_agent_voices_per_engine"][engine][agent_id] = dict(self._editor_tts_settings)
        save_settings(settings)

    def set_editor_tts_engine(self, label: str) -> None:
        """Switch the TTS engine in the editor (for voice configuration).

        'Off' sets enabled=False for this agent and saves immediately.
        Any engine sets enabled=True and loads voice settings for that engine.
        """
        from ..lib.i18n import tts_label_to_key
        key = tts_label_to_key(label)
        if key == "off":
            self._editor_tts_settings["enabled"] = False
            self._save_editor_tts_settings()
            return
        self.editor_tts_engine = key
        self._load_editor_tts_settings()
        self._editor_tts_settings["enabled"] = True
        self._save_editor_tts_settings()

    def set_editor_agent_tts_voice(self, voice: str):
        """Set TTS voice for the agent currently open in the editor."""
        self._editor_tts_settings["voice"] = voice
        self._save_editor_tts_settings()

    def set_editor_agent_tts_speed(self, speed: str):
        """Set TTS speed for the agent currently open in the editor."""
        self._editor_tts_settings["speed"] = speed
        self._save_editor_tts_settings()

    def set_editor_agent_tts_pitch(self, pitch: str):
        """Set TTS pitch for the agent currently open in the editor."""
        self._editor_tts_settings["pitch"] = pitch
        self._save_editor_tts_settings()

    def toggle_editor_agent_tts(self):
        """Toggle TTS for the agent currently open in the editor."""
        self._editor_tts_settings["enabled"] = not self._editor_tts_settings.get("enabled", True)
        self._save_editor_tts_settings()

    # ── Engine Key Helper ─────────────────────────────────────────

    def _get_engine_key(self) -> str:
        """Get engine key for config lookup (xtts, moss, dashscope, piper, espeak, edge).

        Since tts_engine now stores keys directly, this just returns self.tts_engine.
        """
        return self.tts_engine

    # ── XTTS Voice Refresh ────────────────────────────────────────

    def ensure_all_agents_have_tts(self) -> None:
        """Ensure every registered agent has a TTS voice entry.

        Adds missing agents with engine-specific defaults.
        Removes entries for agents that no longer exist.
        Called after settings load and after agent create/delete.
        """
        from ..lib.agent_config import (
            get_agent_ids,
            get_tts_voice_default,
            load_agents_raw,
        )

        # System-Agents (role="system", e.g. calibration) and the vision
        # agent never appear in chat → keep them out of TTS settings so
        # they don't show up in restore/save logs and the agent-editor
        # voice list.
        agents_raw = load_agents_raw()
        excluded = {
            agent_id for agent_id, cfg in agents_raw.items()
            if cfg.get("role") == "system"
        }
        excluded.add("vision")

        registered = set(get_agent_ids()) - excluded
        current = set(self.tts_agent_voices.keys())

        # Add missing agents (defaults sourced from agents.json tts_voices)
        for agent_id in registered - current:
            self.tts_agent_voices[agent_id] = get_tts_voice_default(agent_id, self.tts_engine)

        # Remove agents that no longer exist OR are now excluded
        for agent_id in (current - registered):
            del self.tts_agent_voices[agent_id]

    def _refresh_xtts_voices(self):
        """Refresh XTTS voices from Docker service.

        Also validates that agent voices are in the available list.
        If a saved voice is not found, it resets to the default.
        """
        from ..lib.config import get_xtts_voices
        from ..lib.agent_config import get_tts_voice_default
        voices = get_xtts_voices()
        if voices:
            from ..lib.config import sort_voices_custom_first
            self.xtts_voices_cache = sort_voices_custom_first(list(voices.keys()))
            self.add_debug(f"🎤 XTTS: {len(voices)} voices loaded")  # type: ignore[attr-defined]

            # Validate all agent voices — reset if not in available list
            for agent in list(self.tts_agent_voices.keys()):
                current_voice = self.tts_agent_voices[agent].get("voice", "")
                if current_voice and current_voice not in self.xtts_voices_cache:
                    default_voice = str(get_tts_voice_default(agent, "xtts").get("voice", ""))
                    if default_voice:
                        self.tts_agent_voices[agent]["voice"] = default_voice
                        self.add_debug(f"⚠️ XTTS: Reset {agent} voice to {default_voice}")  # type: ignore[attr-defined]

    # ── Per-Engine Settings Persistence ───────────────────────────

    def _save_agent_voices_for_engine(self, engine_key: str):
        """Save current agent voices to settings for the specified engine.

        Called before switching to a different TTS engine to preserve
        the user's agent voice preferences for that engine.
        """
        import copy
        import os
        from ..lib.settings import load_settings, save_settings, SETTINGS_FILE

        settings = load_settings() or {}
        if "tts_agent_voices_per_engine" not in settings:
            settings["tts_agent_voices_per_engine"] = {}

        # Deep copy current agent voices
        settings["tts_agent_voices_per_engine"][engine_key] = copy.deepcopy(self.tts_agent_voices)
        save_settings(settings)
        # Update mtime tracker so periodic poll doesn't trigger spurious reload
        try:
            self._last_settings_mtime = os.path.getmtime(SETTINGS_FILE)  # type: ignore[attr-defined]
        except OSError:
            pass

    def _restore_agent_voices_for_engine(self, engine_key: str):
        """Restore agent voices from settings for the specified engine.

        Called after switching to a different TTS engine to restore
        the user's previously saved agent voice preferences for that engine.
        Falls back to engine-specific defaults if no saved preferences exist.
        After restoring, runs _strip_stale_voices_for_engine to clear
        any leftovers the engine doesn't know about (Variant B cleanup).
        """
        from ..lib.settings import load_settings
        from ..lib.agent_config import get_tts_voice_defaults_for_engine

        settings = load_settings() or {}
        saved_agent_voices = settings.get("tts_agent_voices_per_engine", {}).get(engine_key)

        if saved_agent_voices:
            # Restore from saved preferences
            for agent in self.tts_agent_voices:
                if agent in saved_agent_voices:
                    self.tts_agent_voices[agent].update(saved_agent_voices[agent])
            source = "Restored"
        else:
            # Use engine-specific defaults from agents.json (known agents
            # get specific defaults, others keep their current voice)
            defaults = get_tts_voice_defaults_for_engine(engine_key)
            for agent in self.tts_agent_voices:
                if agent in defaults:
                    self.tts_agent_voices[agent].update(defaults[agent])
            source = "Default"

        # Log actual agent voices
        voice_list = ", ".join(
            f"{a.capitalize()}={self.tts_agent_voices[a].get('voice', '?')}"
            for a in self.tts_agent_voices
        )
        self.add_debug(f"🔊 {source} agent voices for {engine_key}: {voice_list}")  # type: ignore[attr-defined]

        # Variant B cleanup: clear voices the engine doesn't know.
        # Stale "★ AIfred" / "Baldur Sanjin" entries (left over from a
        # previous XTTS configuration) get zeroed out here so the
        # dropdown only ever shows real, working voices.
        self._strip_stale_voices_for_engine(engine_key)

    def _live_voices_for_engine(self, engine_key: str) -> set[str]:
        """Set of voice names the given engine can actually produce.

        Goes through the engine registry: prefer the engine's live
        ``get_voices()`` (HTTP discovery for container engines, static
        catalogue otherwise), fall back to its ``voices_fallback``.
        For XTTS we honour the in-state cache to avoid a hot-path
        HTTP call when the engine is up.
        """
        from ..lib.tts_engines import get_engine
        eng = get_engine(engine_key)
        if eng is None:
            return set()
        # XTTS: state-side cache wins (loaded once when the engine
        # came up, refreshed by _refresh_xtts_voices).
        if engine_key == "xtts" and self.xtts_voices_cache:
            return set(self.xtts_voices_cache)
        live = eng.get_voices()
        if live:
            return set(live.keys())
        return set(eng.voices_fallback.keys())

    def _strip_stale_voices_for_engine(self, engine_key: str) -> None:
        """Variant B: clear any per-agent voice the engine doesn't know.

        Triggered by the engine-switch restore path. Stale entries from
        previous engine configs (a "★ AIfred" left over after switching
        from XTTS to Qwen3, say) are reset to empty string so the agent
        falls back to the default voice on the next request and the
        dropdown stops showing fake options.

        The cleanup is persisted via _save_agent_voices_for_engine so it
        survives a reload — no need to re-clean on every restore.
        """
        live = self._live_voices_for_engine(engine_key)
        if not live:
            return
        cleared: list[str] = []
        for agent, cfg in self.tts_agent_voices.items():
            current = str(cfg.get("voice", "") or "")
            if current and current not in live:
                cfg["voice"] = ""
                cleared.append(f"{agent}={current!r}")
        if cleared:
            self.add_debug(  # type: ignore[attr-defined]
                f"🧹 {engine_key}: Cleared {len(cleared)} stale voice(s): {', '.join(cleared)}"
            )
            self._save_agent_voices_for_engine(engine_key)

    def _save_tts_toggles_for_engine(self, engine_key: str):
        """Save current TTS toggles (autoplay, streaming) for the specified engine."""
        import os
        from ..lib.settings import load_settings, save_settings, SETTINGS_FILE

        settings = load_settings() or {}
        if "tts_toggles_per_engine" not in settings:
            settings["tts_toggles_per_engine"] = {}

        settings["tts_toggles_per_engine"][engine_key] = {
            "autoplay": self.tts_autoplay,
            "streaming": self.tts_streaming_enabled,
        }
        save_settings(settings)
        # Update mtime tracker so periodic poll doesn't trigger spurious reload
        try:
            self._last_settings_mtime = os.path.getmtime(SETTINGS_FILE)  # type: ignore[attr-defined]
        except OSError:
            pass

    def _restore_tts_toggles_for_engine(self, engine_key: str):
        """Restore TTS toggles from settings for the specified engine.

        Falls back to engine-specific defaults if no saved preferences exist.
        """
        from ..lib.settings import load_settings
        from ..lib.config import TTS_TOGGLE_DEFAULTS

        settings = load_settings() or {}
        saved_toggles = settings.get("tts_toggles_per_engine", {}).get(engine_key)

        if saved_toggles:
            self.tts_autoplay = saved_toggles.get("autoplay", True)
            self.tts_streaming_enabled = saved_toggles.get("streaming", True)
            self.add_debug(f"🔊 Restored TTS toggles for {engine_key}: autoplay={self.tts_autoplay}, streaming={self.tts_streaming_enabled}")  # type: ignore[attr-defined]
        else:
            defaults = TTS_TOGGLE_DEFAULTS.get(engine_key, {"autoplay": True, "streaming": True})
            self.tts_autoplay = defaults["autoplay"]
            self.tts_streaming_enabled = defaults["streaming"]
            self.add_debug(f"🔊 Default TTS toggles for {engine_key}: autoplay={self.tts_autoplay}, streaming={self.tts_streaming_enabled}")  # type: ignore[attr-defined]

    # ── Language-based Voice Switching ─────────────────────────────

    def _switch_tts_voice_for_language(self, lang: str):
        """Switch TTS voice to appropriate language voice for current engine.

        Priority:
        1. User's saved preference for this engine/language (from assistant_settings.json)
        2. Default voice from TTS_DEFAULT_VOICES config
        """
        from ..lib.config import TTS_DEFAULT_VOICES, EDGE_TTS_VOICES, PIPER_VOICES, ESPEAK_VOICES
        from ..lib.settings import load_settings

        engine_key = self._get_engine_key()

        # Get voice dictionary for current engine
        # Values: str (Edge/XTTS/MOSS/DashScope) or tuple[str, str] (Piper/eSpeak)
        voice_dict: dict[str, Any] = {}
        if engine_key == "piper":
            voice_dict = PIPER_VOICES
        elif engine_key == "espeak":
            voice_dict = ESPEAK_VOICES
        elif engine_key == "xtts":
            # XTTS voices are loaded dynamically - use cached list or fallback
            from ..lib.config import XTTS_VOICES_FALLBACK
            if self.xtts_voices_cache:
                voice_dict = {voice: voice for voice in self.xtts_voices_cache}
            else:
                voice_dict = XTTS_VOICES_FALLBACK
        elif engine_key == "moss":
            # MOSS voices are loaded dynamically - fetch fresh or use fallback
            from ..lib.config import get_moss_voices, MOSS_TTS_VOICES_FALLBACK
            moss_voices = get_moss_voices()
            voice_dict = moss_voices if moss_voices else MOSS_TTS_VOICES_FALLBACK
        elif engine_key == "fishspeech":
            from ..lib.config import get_fishspeech_voices, FISH_SPEECH_VOICES_FALLBACK
            fish_voices = get_fishspeech_voices()
            voice_dict = fish_voices if fish_voices else FISH_SPEECH_VOICES_FALLBACK
        elif engine_key == "dashscope":
            from ..lib.config import DASHSCOPE_VOICES
            voice_dict = DASHSCOPE_VOICES
        else:
            voice_dict = EDGE_TTS_VOICES

        # Priority 1: Check for user's saved preference
        saved_settings = load_settings() or {}
        user_voices = saved_settings.get("tts_voices_per_language", {})
        user_voice = user_voices.get(engine_key, {}).get(lang)

        if user_voice and user_voice in voice_dict:
            self.tts_voice = user_voice
            return

        # Priority 2: Use default voice from config
        default_voice = TTS_DEFAULT_VOICES.get(engine_key, {}).get(lang)

        if default_voice and default_voice in voice_dict:
            self.tts_voice = default_voice
