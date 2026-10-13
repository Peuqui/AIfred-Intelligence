"""Dünner HTTP-Client für den STT-Container (Repo STT-Suite, Port 5080).

SSOT für jeden Zugriff auf den Whisper-/Parakeet-Dienst: URL, Timeouts,
Fehlerabbildung und der Start des Containers auf Abruf. Der Client kennt nur
die HTTP-Schnittstelle des Dienstes. Welche Politik AIfred darauf aufsetzt
(GPU zuerst, CPU-Wiederholung, Warten vor einem Modellstart) liegt bei den
Aufrufern in ``audio_processing``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import requests

from .config import (
    STT_SUITE_COMPOSE_PATH,
    STT_SUITE_CPU_COMPOSE_PATH,
    STT_SUITE_DIR,
    WHISPER_CONTROL_TIMEOUT_S,
    WHISPER_HEALTH_TIMEOUT_S,
    WHISPER_SERVICE_URL,
    WHISPER_START_POLL_S,
    WHISPER_START_TIMEOUT_S,
    WHISPER_TRANSCRIBE_TIMEOUT_S,
)
from .logging_utils import log_message
from .process_utils import docker_compose_action
from .service_status import notify_used


class STTError(RuntimeError):
    """Der STT-Dienst hat eine Anfrage nicht erfüllt; die Meldung ist anzeigbar."""


class STTUnreachableError(STTError):
    """Keine Verbindung zum Dienst (Container läuft nicht?)."""


class STTTimeoutError(STTError, TimeoutError):
    """Der Dienst hat nicht innerhalb des Timeouts geantwortet."""


class STTGPUUnavailableError(STTError):
    """HTTP 503: keine GPU mit genug freiem VRAM — der Aufrufer darf auf die CPU ausweichen."""


class STTBusyError(STTError):
    """HTTP 409: eine Transkription läuft gerade."""


class STTClient:
    def __init__(self, base_url: str, compose_path: str) -> None:
        self.base_url = base_url
        self.compose_path = compose_path

    # ---- HTTP-Grundlage --------------------------------------------------

    def _request(self, method: str, path: str, timeout: float, **kwargs: Any) -> requests.Response:
        """Anfrage senden und Netzwerkfehler als STTError-Familie melden."""
        try:
            return requests.request(method, f"{self.base_url}{path}", timeout=timeout, **kwargs)
        except requests.Timeout as e:
            raise STTTimeoutError(f"STT service timed out on {path} after {timeout}s") from e
        except requests.ConnectionError as e:
            raise STTUnreachableError("STT service not reachable (container running?)") from e
        except requests.RequestException as e:
            raise STTError(f"STT request {path} failed: {e}") from e

    def _call(self, method: str, path: str, timeout: float, error_label: str = "", **kwargs: Any) -> dict[str, Any]:
        """Anfrage senden, Dienstfehler abbilden, JSON-Antwort liefern."""
        resp = self._request(method, path, timeout, **kwargs)
        if not resp.ok:
            self._raise_service_error(resp, error_label)
        try:
            data: dict[str, Any] = resp.json()
        except ValueError as e:
            raise STTError(f"STT service sent invalid JSON on {path}") from e
        return data

    @staticmethod
    def _raise_service_error(resp: requests.Response, error_label: str) -> None:
        """Den vom Dienst gemeldeten Grund (``error``) in die passende Ausnahme legen."""
        try:
            reason = resp.json().get("error", resp.text[:200])
        except ValueError:
            reason = resp.text[:200]
        message = f"{error_label}: {reason}" if error_label else f"{resp.status_code} {reason}"
        if resp.status_code == 503:
            raise STTGPUUnavailableError(message)
        if resp.status_code == 409:
            raise STTBusyError(message)
        raise STTError(message)

    # ---- Schnittstelle des Dienstes -------------------------------------

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/health", WHISPER_HEALTH_TIMEOUT_S)

    def is_ready(self) -> bool:
        """Antwortet der Dienst und hat er nichts mehr zu laden? Status ``ok`` (Modell geladen) und
        ``idle`` (Modelle laden erst bei der ersten Anfrage, z. B. nach ``/unload?device=all``) zählen
        als bereit, ``loading`` (Start lädt noch) nicht. Dies ist eine Abfrage, die auch einen toten
        Container mit ``False`` beantwortet — daher fängt sie STTError."""
        try:
            return self.health().get("status") in ("ok", "idle")
        except STTError:
            return False

    def status(self) -> dict[str, Any]:
        return self._call("GET", "/status", WHISPER_CONTROL_TIMEOUT_S)

    def set_config(self, payload: dict[str, Any]) -> None:
        """Konfiguration der Engines schreiben; gelesen wird sie über ``status()``."""
        self._call("POST", "/config", WHISPER_CONTROL_TIMEOUT_S, json=payload)

    def unload(self, device: str, force: bool = False) -> list[str]:
        """Worker des Geräts beenden (``cuda``, ``cpu``, ``all``). Liefert die
        entladenen Geräte. Eine laufende Transkription wird mit STTBusyError
        gemeldet, bis ``force`` sie abbricht."""
        params = {"device": device, **({"force": "1"} if force else {})}
        data = self._call("POST", "/unload", WHISPER_CONTROL_TIMEOUT_S, params=params)
        unloaded: list[str] = data.get("unloaded", [])
        return unloaded

    def transcribe(
        self, audio_path: str, language: str, device: str, diarize: bool,
    ) -> dict[str, Any]:
        """Datei transkribieren und die Antwort des Dienstes unverändert liefern.

        Raises:
            STTGPUUnavailableError: 503, kein VRAM für ``device="cuda"``.
            STTTimeoutError: länger als WHISPER_TRANSCRIBE_TIMEOUT_S.
            STTUnreachableError: Dienst nicht erreichbar.
            STTError: jeder andere Fehler des Dienstes.
        """
        with open(audio_path, "rb") as f:
            result = self._call(
                "POST", "/transcribe", WHISPER_TRANSCRIBE_TIMEOUT_S,
                error_label=f"Whisper ({device})",
                files={"file": (Path(audio_path).name, f)},
                data={"device": device, "language": language,
                      "diarize": "1" if diarize else "0"},
            )
        notify_used("stt")  # the transcription reset the idle clock of the GPU worker
        return result

    # ---- Container auf Abruf ---------------------------------------------

    def start_container(self) -> tuple[bool, str]:
        if not os.path.exists(self.compose_path):
            return False, f"STT-Suite not found at {STT_SUITE_DIR} — run scripts/install-all.sh"
        # No NVIDIA GPU on this machine: the CPU variant of the compose file (the service then answers
        # device=cuda with 503 and AIfred transcribes on the CPU model).
        from .gpu_utils import get_all_gpus_memory_info

        overrides = () if get_all_gpus_memory_info() else (STT_SUITE_CPU_COMPOSE_PATH,)
        return docker_compose_action(self.compose_path, "up", "Whisper", overrides)

    def ensure_ready(self, timeout_s: float = WHISPER_START_TIMEOUT_S) -> None:
        """Container bei Bedarf starten und auf das geladene Modell warten.

        Raises:
            STTError: Start fehlgeschlagen oder Modell nicht rechtzeitig geladen.
        """
        if self.is_ready():
            return
        started, message = self.start_container()
        if not started:
            raise STTError(message)
        log_message("Whisper: Waiting for model to load...")
        deadline = time.monotonic() + timeout_s
        while not self.is_ready():
            if time.monotonic() >= deadline:
                raise STTError(f"Whisper: Timeout after {timeout_s}s waiting for model")
            time.sleep(WHISPER_START_POLL_S)
        log_message("Whisper: Model loaded")


_default_client: STTClient | None = None


def get_default_client() -> STTClient:
    """Der eine Client des Prozesses (URL und Pfad aus config.py)."""
    global _default_client
    if _default_client is None:
        _default_client = STTClient(WHISPER_SERVICE_URL, STT_SUITE_COMPOSE_PATH)
    return _default_client
