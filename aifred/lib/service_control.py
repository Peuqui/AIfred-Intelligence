"""Load and unload what the status line shows, on a click of the user (ui/service_status_bar.py,
api/service_status.py).

- The language model and the describer live in llama-swap. Loading takes the way of a chat request
  (``LLMBackend.load_model``: GPU-Orc clears the cards, llama-swap loads). The describer that loads
  is the ``-visiond`` placement of the vision LLM that fits next to what is loaded
  (``fitting_visiond``), so it never pushes the main model out. Unloading is llama-swap's
  ``POST /api/models/unload/<model>``.
- STT and TTS load with their first request, there is nothing to load by hand. Unloading asks the
  suite itself (``POST /gpu/release``): GPU-Orc frees only whole cards or room for a model, and both
  suites may share a card. With ``force``: a click is a deliberate decision, a running reading or
  transcription is aborted (Peuqui, 04.11.2026: "Wenn ich klicken will, dann will ich es auch
  entladen haben"; a transcription with ``device=auto`` goes on on the CPU).

Whether a model is busy (an answer running) the caller checks against the status monitor.
"""

from __future__ import annotations

import asyncio

import requests

from .config import LLAMASWAP_BACKENDS, LLAMASWAP_UNLOAD_TIMEOUT_S
from .gpu_release import RELEASE_DONE_OUTCOMES, GpuReleaseResult
from .service_status import LLAMASWAP_BASE_URL

# The describer profiles (-visiond) are llama.cpp servers, whatever backend the chat uses.
DESCRIBER_BACKEND = "llamacpp"
RELEASE_REASON = "AIfred: unload button in the status line"


class ServiceControlRefused(RuntimeError):
    """The action cannot be done now. ``key`` and ``params`` name the message for the user (i18n)."""

    def __init__(self, key: str, **params: str) -> None:
        super().__init__(f"{key}: {params}" if params else key)
        self.key = key
        self.params = params


# ---- What a load button loads -----------------------------------------------------


def main_model_to_load() -> tuple[str, str]:
    """``(backend type, model)`` a chat request would load now (settings.json, variant included)."""
    from .config import get_effective_model_from_settings
    from .settings import persisted_settings

    backend_type = str(persisted_settings().get("backend_type") or "")
    if backend_type not in LLAMASWAP_BACKENDS:
        raise ServiceControlRefused("service_control_not_llamaswap")
    model = get_effective_model_from_settings("aifred")
    if not model:
        raise ServiceControlRefused("service_control_no_main_model")
    return backend_type, model


def describer_to_load() -> tuple[str, str]:
    """``(backend type, profile)``: the ``-visiond`` placement of the vision LLM in the settings that
    fits next to what is loaded now."""
    from .vision_routing import fitting_visiond, settings_model

    vision = settings_model("vision")
    if not vision:
        raise ServiceControlRefused("service_control_no_describer")
    profile, fit = fitting_visiond(vision)
    if profile is not None:
        return DESCRIBER_BACKEND, profile
    if fit is None:
        raise ServiceControlRefused("service_control_describer_no_profile", model=vision)
    raise ServiceControlRefused("service_control_describer_does_not_fit", reason=fit.message)


# ---- Doing it -------------------------------------------------------------------------


async def load_llamaswap_model(backend_type: str, model: str) -> float:
    """Load ``model`` through the backend's own load path; seconds it took (0.0 when it ran already).

    Raises:
        BackendError: llama-swap is not reachable or the model did not start.
    """
    from ..backends import BackendFactory

    backend = BackendFactory.create(backend_type)
    try:
        return await backend.load_model(model)
    finally:
        await backend.close()


def unload_llamaswap_model(model: str) -> None:
    """Have llama-swap stop ``model``. Blocking: call in a thread from async code.

    Raises:
        ServiceControlRefused: llama-swap does not know the model, refuses or does not answer.
    """
    try:
        response = requests.post(
            f"{LLAMASWAP_BASE_URL}/api/models/unload/{model}", timeout=LLAMASWAP_UNLOAD_TIMEOUT_S,
        )
    except requests.RequestException as error:
        raise ServiceControlRefused("service_control_failed", name=model, error=str(error)) from error
    if not response.ok:
        raise ServiceControlRefused("service_control_failed", name=model, error=f"HTTP {response.status_code} {response.text[:200]}")


async def release_stt_gpu() -> None:
    """End the GPU processes of the STT-Suite, a running transcription included.

    Raises:
        ServiceControlRefused: the end is not proven, or the service does not answer.
    """
    from .stt_client import STTError, get_default_client

    try:
        results = await asyncio.to_thread(get_default_client().gpu_release, force=True, reason=RELEASE_REASON)
    except STTError as error:
        raise ServiceControlRefused("service_control_failed", name="STT", error=str(error)) from error
    _raise_unless_released("STT", results)


async def release_tts_engine(engine: str) -> None:
    """Take the TTS engine ``engine`` off the cards, a running reading included.

    Raises:
        ServiceControlRefused: the end is not proven, or the service does not answer.
    """
    from .tts_client import TTSSuiteError, get_tts_client

    try:
        results = await asyncio.to_thread(get_tts_client().gpu_release, [engine], force=True, reason=RELEASE_REASON)
    except TTSSuiteError as error:
        raise ServiceControlRefused("service_control_failed", name=engine, error=str(error)) from error
    _raise_unless_released(engine, results)


def _raise_unless_released(name: str, results: list[GpuReleaseResult]) -> None:
    for result in results:
        if result.outcome not in RELEASE_DONE_OUTCOMES:
            raise ServiceControlRefused("service_control_failed", name=name, error=f"{result.outcome}: {result.detail}")
