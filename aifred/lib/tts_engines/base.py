"""Abstract base class for TTS engines."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional


class TTSEngine(ABC):
    """One TTS backend, all in one place.

    Subclasses declare identity + capabilities as class-level attributes,
    and override the methods that actually differ per backend (mostly
    voice discovery + speech generation). Defaults are tuned for the
    lightweight non-container case (Edge / Piper / eSpeak) so that
    container-based engines only need to override what's actually
    different.

    NEVER instantiate directly — only via :data:`registry.TTS_ENGINES`.
    """

    # ── Identity ───────────────────────────────────────────────────
    #: Stable engine key, used in settings.json + the engine dropdown
    #: (e.g. "qwen3local", "xtts", "moss", "edge"). Must match the
    #: suffix in llama-swap profile names ``<model>-tts-<key>``.
    key: str

    #: Short label for compact UI surfaces (channel dropdowns, status
    #: messages). Long, descriptive labels live in i18n.py under
    #: ``tts_engine_<key>``.
    label_short: str

    # ── Capabilities (defaults = lightweight engine) ───────────────
    #: True for engines that run as a Docker container we own
    #: (qwen3local / xtts / moss). False for cloud/CLI engines.
    runs_in_container: bool = False

    #: True if the engine occupies GPU VRAM that the LLM calibration
    #: must reserve. Lightweight engines (Edge / Piper / eSpeak /
    #: DashScope) → False.
    needs_gpu: bool = False

    #: Whether this engine ignores the speed parameter on its own and
    #: requires ffmpeg post-processing to rate-shift the audio.
    needs_speed_postprocess: bool = False

    #: Permanent extra VRAM (MB) to subtract from the TTS GPU's free_mb
    #: during TTS-variant LLM calibration. Used for engines that
    #: allocate dynamically during generate() (Qwen3-TTS grows from
    #: ~5 GB idle to ~7 GB on long bubbles, so a fixed 7.5 GB reserve
    #: is safer than a one-shot peak measurement).
    calibration_vram_reserve_mb: int = 0

    #: True if this engine should appear in channel-plugin dropdowns
    #: (FreeEcho.2 etc.). Excludes engines that need extra credentials
    #: a channel device probably doesn't have wired up.
    suitable_for_channels: bool = True

    # ── Locations (only for container-backed engines) ──────────────
    #: HTTP base URL of the local container's REST API. None for
    #: engines that don't run as a service we control.
    service_url: Optional[str] = None

    #: docker-compose.yml path. None for lightweight engines.
    docker_compose_path: Optional[Path] = None

    # ── Voices ─────────────────────────────────────────────────────
    @abstractmethod
    def get_voices(self) -> dict[str, str]:
        """Return ``{voice_name: voice_id}`` for the engine's *currently
        live* voice set.

        For container engines: hit the container's /voices endpoint.
        For static engines: return the bundled voice map.
        Return ``{}`` if the engine isn't reachable — the caller falls
        back to :attr:`voices_fallback` in that case.
        """

    @property
    def voices_fallback(self) -> dict[str, str]:
        """Static voice list used when the engine is unreachable. Same
        shape as :meth:`get_voices`. Container engines return the
        voices they ship with by default; cloud/CLI engines return
        their stable list (Edge's neural voices etc.)."""
        return {}

    # ── Language mapping (default: ISO codes pass through) ─────────
    @property
    def language_map(self) -> dict[str, str]:
        """Mapping from AIfred's short language code (``"de"``, ``"en"``,
        ``"zh"``, …) to the engine-specific language tag. Empty dict
        means "engine takes the ISO code as-is"."""
        return {}

    # ── Lifecycle ──────────────────────────────────────────────────
    def is_installed(self) -> bool:
        """True if the engine is *provisioned* on this host — i.e. ready
        to be started. Default ``True`` for lightweight engines (they
        ship with AIfred); container engines override to check whether
        their docker-compose.yml has been rolled out.

        Used by the calibration UI to hide engines the user hasn't set
        up, so the picker only shows what's actually usable.
        """
        return True

    def is_running(self) -> bool:
        """True if the engine can accept requests *right now*. Default
        ``True`` for lightweight engines (always-on); container engines
        override with a health check."""
        return True

    def start(self) -> tuple[bool, str]:
        """Bring the engine up. Returns ``(success, message)``."""
        return True, "no-op"

    def stop(self) -> tuple[bool, str]:
        """Take the engine down. Returns ``(success, message)``."""
        return True, "no-op"

    def ensure_ready(self, timeout: int | None = None) -> tuple[bool, str, str]:
        """Ensure the engine is up and serving. Returns
        ``(success, status_message, device)``. ``device`` is the engine's
        compute target ("cuda:0", "cpu", "") — empty for engines that
        don't expose one."""
        return True, "ready", ""

    # ── Speech generation ──────────────────────────────────────────
    def generate_speech(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> Optional[str]:
        """Synthesise ``text`` with ``voice`` into a WAV/MP3 file.

        Returns a URL-style path the browser can fetch (e.g.
        ``"/tts_audio/audio_123.wav"``), or ``None`` on failure.

        ``language`` is the ISO short code (``"de"`` / ``"en"`` / …).
        The engine maps it through :attr:`language_map` if needed.

        Default: ``NotImplementedError``. Not abstract because the
        Edge engine has an async-only signature that doesn't fit this
        sync contract; for now its dispatch stays in
        :func:`audio_processing.generate_tts`. Phase-3 migration of the
        sync engines uses this; Edge gets a follow-up cleanup once we
        introduce an async variant on the base class.
        """
        raise NotImplementedError(
            f"{self.key} engine has no sync generate_speech; route through "
            f"audio_processing.generate_tts for now."
        )

    # ── Calibration support (only container/GPU engines) ───────────
    def calibration_setup(self, debug: Any) -> bool:
        """Called by the LLM-calibration before measuring free VRAM on
        the TTS GPU. Default: bring the container up via
        :meth:`ensure_ready`. Container engines that need a long
        test-inference to materialise their KV-cache can override.

        ``debug`` is the calibration's add_debug callback for
        progress lines.
        """
        ok, msg, _device = self.ensure_ready()
        if ok:
            debug(f"   🔊 {msg}")
        return ok

    def calibration_teardown(self, debug: Any) -> None:
        """Called after the TTS-variant calibration is done. Default:
        stop the container."""
        self.stop()
        debug(f"   🔊 {self.label_short} container stopped")

    # ── Misc ───────────────────────────────────────────────────────
    def __repr__(self) -> str:  # pragma: no cover — debugging convenience
        return f"<TTSEngine {self.key!r}>"
