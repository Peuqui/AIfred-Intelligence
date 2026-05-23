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

    #: True if the engine actually honours the ``language`` argument of
    #: :meth:`generate_speech`. Engines that auto-detect the language
    #: from the text (Fish-Speech) or encode it in the voice id itself
    #: (Edge / Piper / eSpeak) leave this False — the agent editor then
    #: greys out the language dropdown so the user can't set a value
    #: that has no effect. Speed and pitch always apply (ffmpeg
    #: post-processing), so no equivalent flag exists for those.
    supports_language: bool = False

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

    #: Sort key for the UI engine dropdown — lower numbers come first.
    #: Convention: 10 = most-recommended GPU container, 80 = cloud/CLI
    #: fallback. New engines just pick a free integer.
    display_order: int = 100

    #: Local Docker image name (without tag). None for lightweight
    #: engines. Used by :meth:`is_installed` to check provisioning —
    #: the image is the source of truth, the compose file is the recipe.
    image_name: Optional[str] = None

    #: Subdirectory under ``docker/tts/`` containing the build recipe.
    #: Default = engine key; override only when the dir name differs
    #: (MOSS key="moss" but dir="moss-tts").
    compose_subdir: Optional[str] = None

    # ── Locations (overridable via @property in subclasses) ────────
    @property
    def service_url(self) -> Optional[str]:
        """HTTP base URL of the local container's REST API. None for
        engines that don't run as a service we control."""
        return None

    @property
    def docker_compose_path(self) -> Optional[Path]:
        """docker-compose.yml path. The compose file is a *build recipe*
        — present in the repo, used to (re)build the image. NOT used to
        decide whether the engine is installed (the image is).

        Default: ``docker/tts/<compose_subdir or key>/docker-compose.yml``
        — engines only have to override ``compose_subdir`` if the directory
        name differs from the engine key.
        """
        if not self.runs_in_container:
            return None
        from ..config import PROJECT_ROOT
        subdir = self.compose_subdir or self.key
        return Path(PROJECT_ROOT) / "docker" / "tts" / subdir / "docker-compose.yml"

    @property
    def voices_fallback(self) -> dict[str, str]:
        """Static voice list used when the engine is unreachable. Same
        shape as :meth:`get_voices`. Override per engine."""
        return {}

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
        to be started.

        For container engines (``image_name`` set): asks Docker whether
        the image is locally available. The image is the artefact that
        can actually run; the compose file is just a recipe to rebuild
        it. So deleting the image makes the engine "not installed"
        even if the compose file is still in the repo — and a user can
        always restore the engine via ``docker compose build`` without
        any code changes.

        For lightweight engines (Edge / Piper / eSpeak / cloud — no
        ``image_name``): always True, they ship with AIfred.

        Used by the calibration UI and engine dropdowns to hide engines
        the user can't actually run right now.
        """
        if not self.image_name:
            return True
        import subprocess
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", self.image_name],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

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
        """Synthesise ``text`` with ``voice`` into a WAV/MP3/OGG file.

        Returns a URL-style path the browser can fetch (e.g.
        ``"/_upload/tts_audio/audio_123.wav"``), or ``None`` on failure.

        ``language`` is the ISO short code (``"de"`` / ``"en"`` / …).
        The engine maps it through :attr:`language_map` if needed.

        ``speed`` / ``pitch`` semantics: engines with
        ``needs_speed_postprocess=False`` (Piper / eSpeak / Edge) apply
        the values natively here. Engines with ``True`` ignore them and
        rely on the central ffmpeg post-processor in the dispatch path.

        Default: ``NotImplementedError`` — engines must override.
        """
        raise NotImplementedError(
            f"{self.key} engine has no sync generate_speech; engines with "
            f"native async IO should override generate_speech_async instead."
        )

    async def generate_speech_async(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> Optional[str]:
        """Async wrapper around :meth:`generate_speech`. The default
        offloads the sync method to a thread-pool executor so blocking
        HTTP / subprocess calls don't stall the event loop. Engines with
        native async IO (Edge) override this and skip the sync method.
        """
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self.generate_speech, text, voice, language, speed, pitch,
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
