"""Abstract base class for TTS engines."""
from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterator, Literal, Optional


#: Einheiten der Sprachausgabe: wie viel Text auf einmal an die Engine geht. ``sentence`` =
#: satzweises Streaming (früheste erste Sprache), ``paragraph`` = absatzweise (oft bessere
#: Betonung), ``whole`` = alles am Stück (beste Qualität, die erste Sprache kommt erst nach der
#: kompletten Erzeugung). SSOT für Browser und alle Kanäle.
SPEECH_UNITS = ("sentence", "paragraph", "whole")

#: Warum eine Synthese gescheitert ist — bestimmt den Grund der Ansage beim
#: Stimmwechsel: Server nicht erreichbar, Engine antwortet mit Fehler, Fehler
#: im eigenen Code.
FailureReason = Literal["unreachable", "engine", "software"]


class TTSFailure(Exception):
    """Eine Engine hat keine Sprache geliefert. Jede Engine wirft das statt
    ``None`` zurückzugeben — die Eskalationsliste entscheidet, wer weitermacht."""

    def __init__(self, reason: FailureReason, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason: FailureReason = reason
        self.detail = detail


def shared_voice_names() -> list[str]:
    """The cloned voices every container engine reads from the shared tree
    (``TTS_VOICES_DIR``: ``<Name>/<Name>.wav``) — the catalog while no
    container answers, so a new voice shows up without one running."""
    from ..config import TTS_VOICES_DIR
    return sorted(wav.stem for wav in TTS_VOICES_DIR.glob("*/*.wav") if wav.stem == wav.parent.name)


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

    #: Standard-Einheit dieser Engine (siehe ``SPEECH_UNITS``); die Wahl des Users pro Engine
    #: steht in den Einstellungen (``tts_toggles_per_engine``) und gilt systemweit für alle Agenten.
    #: Schnelle Engines streamen satzweise, langsame lokale rendern besser am Stück.
    default_speech_unit: str = "sentence"

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

    #: How many syntheses this engine (per host) may run at once. Measured on
    #: a 3090 Ti: XTTS serves two in parallel without loss, Qwen3-TTS slows
    #: down about five-fold (2× ~14 s instead of 2× ~2.7 s) — it gets 1.
    max_parallel_requests: int = 2

    #: Seconds a container engine gets to come up and load its model —
    #: locally and on a TTS host alike. 0 for engines without a container.
    startup_timeout_s: int = 0

    #: Health endpoint of a container engine (relative to ``service_url``).
    health_path: str = "/health"

    #: Container engines that can land on the CPU although a GPU is free restart once
    #: in that case (MOSS).
    restart_when_on_cpu: bool = False

    #: True if the engine is part of the escalation list a fresh install starts
    #: with (``config.DEFAULT_SETTINGS["tts_escalation"]``), in ``display_order``.
    in_default_escalation: bool = False

    #: True for engines that run in someone else's cloud (DashScope, Edge) —
    #: the text leaves the house. Shown as a label in the escalation list.
    cloud: bool = False

    #: Port of the engine's REST API — set by engines that run as an HTTP
    #: service (the containers). None = no REST API (cloud/CLI engines);
    #: such engines can only run on this machine or in the cloud.
    default_port: Optional[int] = None

    #: True for the engine that speaks in the user's browser (Web Speech API):
    #: no audio file on the server, only for replies into a browser session.
    renders_in_browser: bool = False

    #: Voice used when neither the user nor agents.json names one for an agent
    #: (None = such an agent cannot speak on this engine).
    default_voice: Optional[str] = None

    def __init__(self, address: str = "localhost", port: Optional[int] = None, gpu_uuid: str = "") -> None:
        """``address``/``port`` of the REST API. The registry instances run on
        this machine (``localhost``); :meth:`at_host` binds the same engine to
        a remote TTS host (escalation entries). ``gpu_uuid``: card a local
        container starts on ("" = the side-channel card)."""
        self.address = address
        self.port = port or self.default_port
        self.gpu_uuid = gpu_uuid

    def on_gpu(self, gpu_uuid: str) -> "TTSEngine":
        """The same local engine, started on another card (the escalation
        list picks one with enough free VRAM)."""
        self._require_local("place")
        return type(self)(gpu_uuid=gpu_uuid)

    def at_host(self, address: str, port: Optional[int] = None) -> "TTSEngine":
        """The same engine, bound to a remote host. Raises ``ValueError`` for
        engines without a REST API — they cannot run on another machine."""
        if self.default_port is None:
            raise ValueError(f"TTS engine {self.key!r} has no REST API and cannot run on a remote host")
        return type(self)(address=address, port=port)

    @property
    def is_remote(self) -> bool:
        """True for an instance bound to another machine. Remote engines are
        never started or stopped from here — their host runs them."""
        return self.address != "localhost"

    # ── Own folder + label ─────────────────────────────────────────
    @property
    def package_dir(self) -> Path:
        """Folder of the engine (``tts_engines/<key>/``): its code, ``i18n.json``
        and whatever else belongs to it."""
        return Path(sys.modules[type(self).__module__].__file__ or "").parent

    def label(self, lang: str) -> str:
        """Long, translated name for the engine dropdowns — from the engine's own
        ``i18n.json`` (``{"de": ..., "en": ...}``)."""
        labels: dict[str, str] = json.loads((self.package_dir / "i18n.json").read_text(encoding="utf-8"))
        return labels[lang]

    # ── Locations ──────────────────────────────────────────────────
    @property
    def service_url(self) -> Optional[str]:
        """HTTP base URL of the engine's REST API. None for engines that
        don't run as an HTTP service."""
        if self.port is None:
            return None
        return f"http://{self.address}:{self.port}"

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
        return Path(PROJECT_ROOT) / "docker" / "tts" / self.service_dir / "docker-compose.yml"

    @property
    def service_dir(self) -> str:
        """Directory under ``docker/tts/`` — also the service name a TTS host's
        control script (``scripts/tts-host-ctl.sh start <service>``) takes and
        the ``container_name`` in that directory's docker-compose.yml."""
        return self.compose_subdir or self.key

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

    def _fetch_voices_json(self) -> Optional[dict[str, Any]]:
        """GET ``<service_url>/voices`` of a container engine. A stopped
        container (connection refused) is the normal case and stays
        silent; only a container that answers wrongly is worth a warning.
        Returns ``None`` on any failure."""
        import requests
        try:
            r = requests.get(f"{self.service_url}/voices", timeout=5)
            r.raise_for_status()
            data: dict[str, Any] = r.json()
            return data
        except requests.ConnectionError:
            return None
        except (requests.RequestException, ValueError) as e:
            print(f"⚠️ Failed to fetch {self.label_short} voices: {e}")
            return None

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
        answer through their health endpoint."""
        if not self.runs_in_container:
            return True
        health = self._health()
        return health is not None and self._model_ready(health)

    def _health(self, timeout: float | None = None) -> dict[str, Any] | None:
        """JSON of the engine's health endpoint; ``{}`` when it answers 2xx
        without JSON; ``None`` when it is not reachable or answers an error."""
        import requests
        from ..config import TTS_HEALTH_TIMEOUT_S
        try:
            response = requests.get(
                f"{self.service_url}{self.health_path}", timeout=timeout or TTS_HEALTH_TIMEOUT_S,
            )
            if not response.ok:
                return None
            if not response.headers.get("content-type", "").startswith("application/json"):
                return {}
            data: dict[str, Any] = response.json()
            return data
        except (OSError, ValueError):
            return None

    def _model_ready(self, health: dict[str, Any]) -> bool:
        """Whether a health answer means "model loaded, can synthesise"."""
        return bool(health.get("model_loaded"))

    def _device(self, health: dict[str, Any]) -> str:
        """Compute target a health answer reports ("cuda:0", "cpu")."""
        return str(health.get("device", "unknown"))

    def start(self) -> tuple[bool, str]:
        """Bring the engine up. Returns ``(success, message)``. Only for
        engines on this machine — a remote host runs its engines itself."""
        self._require_local("start")
        return self._start_local()

    def stop(self) -> tuple[bool, str]:
        """Take the engine down. Returns ``(success, message)``."""
        self._require_local("stop")
        return self._stop_local()

    def ensure_ready(self, timeout: int | None = None) -> tuple[bool, str, str]:
        """Ensure the engine is up and serving. Returns
        ``(success, status_message, device)``. ``device`` is the engine's
        compute target ("cuda:0", "cpu", "") — empty for engines that
        don't expose one. A remote engine is not started, only checked."""
        if self.is_remote:
            if self.is_running():
                return True, f"{self.label_short} ready on {self.address}", ""
            return False, f"{self.label_short} not serving on {self.address}", ""
        return self._ensure_ready_local(timeout)

    def _require_local(self, action: str) -> None:
        if self.is_remote:
            raise RuntimeError(
                f"cannot {action} {self.key!r} on remote host {self.address} — the host runs it"
            )

    # Lifecycle for engines on this machine. Container engines run their compose
    # file; the other engines have nothing to start.
    def _start_local(self) -> tuple[bool, str]:
        if not self.runs_in_container:
            return True, "no-op"
        from ..process_utils import docker_compose_action
        return docker_compose_action(str(self.docker_compose_path), "up", self.label_short, self.gpu_uuid)

    def _stop_local(self) -> tuple[bool, str]:
        if not self.runs_in_container:
            return True, "no-op"
        from ..process_utils import docker_compose_action
        return docker_compose_action(str(self.docker_compose_path), "down", self.label_short)

    def _ensure_ready_local(self, timeout: int | None) -> tuple[bool, str, str]:
        """Start the container if needed and wait until its model is loaded."""
        if not self.runs_in_container:
            return True, "ready", ""
        import time
        from ..logging_utils import log_message

        health = self._health(timeout=2)
        if health is not None and self._model_ready(health):
            device = self._device(health)
            if self.restart_when_on_cpu and device == "cpu" and self._gpu_available():
                log_message(f"{self.label_short} is on CPU but a GPU is available — restarting on GPU")
                self._stop_local()
            else:
                return True, f"{self.label_short} already ready ({device})", device

        success, msg = self._start_local()
        if not success:
            return False, msg, ""

        limit = timeout or self.startup_timeout_s
        log_message(f"{self.label_short}: Waiting for model to load...")
        for waited in range(limit):
            health = self._health(timeout=2)
            if health is not None and self._model_ready(health):
                device = self._device(health)
                log_message(f"{self.label_short}: Model loaded on {device}")
                return True, f"{self.label_short} ready ({device})", device
            if waited > 0 and waited % 30 == 0:
                log_message(f"{self.label_short}: still waiting ({waited}s / {limit}s)...")
            time.sleep(1)
        return False, f"{self.label_short}: Timeout after {limit}s waiting for model", ""

    @staticmethod
    def _gpu_available() -> bool:
        from ..process_utils import get_tts_gpu_uuid
        return bool(get_tts_gpu_uuid())

    # ── Speech generation ──────────────────────────────────────────
    def generate_speech(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> str:
        """Synthesise ``text`` with ``voice`` into a WAV/MP3/OGG file.

        Returns a URL-style path the browser can fetch (e.g.
        ``"/_upload/tts_audio/audio_123.wav"``); raises :class:`TTSFailure`
        when no audio comes back.

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
    ) -> str:
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

    def _synthesize_via_http(self, path: str, payload: dict[str, Any], extension: str) -> str:
        """POST ``payload`` to ``<service_url><path>`` and store the returned
        audio. Shared by all container engines (local or on a remote host).
        Returns the URL-style path; raises :class:`TTSFailure` otherwise."""
        import requests
        from ..audio_processing import TTS_AUDIO_DIR, _generate_tts_filename, _validate_audio_output
        from ..config import TTS_CONNECT_TIMEOUT_S, TTS_READ_TIMEOUT_S
        from ..formatting import format_number
        from ..logging_utils import log_message

        filename = _generate_tts_filename(extension)
        output_file = TTS_AUDIO_DIR / filename
        log_message(
            f"🎤 {self.label_short} @ {self.address}: speaker={payload.get('speaker') or payload.get('reference_id')}, "
            f"language={payload.get('language', '-')}, text_length={len(str(payload.get('text', '')))}"
        )
        try:
            response = requests.post(
                f"{self.service_url}{path}", json=payload,
                timeout=(TTS_CONNECT_TIMEOUT_S, TTS_READ_TIMEOUT_S),
            )
        except (requests.ConnectionError, requests.ConnectTimeout) as exc:
            raise TTSFailure("unreachable", f"{self.label_short} at {self.service_url}: {exc}") from exc
        except requests.Timeout as exc:
            raise TTSFailure("engine", f"{self.label_short} did not answer within {format_number(TTS_READ_TIMEOUT_S)} s") from exc
        if response.status_code != 200:
            raise TTSFailure("engine", f"{self.label_short} HTTP {response.status_code}: {response.text[:200]}")
        output_file.write_bytes(response.content)
        if not _validate_audio_output(str(output_file)):
            raise TTSFailure("engine", f"{self.label_short} returned no usable audio")
        log_message(f"✅ {self.label_short}: audio saved → {output_file} ({format_number(output_file.stat().st_size)} bytes)")
        return f"/_upload/tts_audio/{filename}"

    def prepare_voices(self) -> Iterator[str]:
        """Make the engine's voices ready before it speaks (idempotent), yielding
        one progress line per step. Default: nothing to prepare — the DashScope
        engine registers its cloned voices with the cloud here."""
        return iter(())

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
